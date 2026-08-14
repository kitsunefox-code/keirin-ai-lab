from __future__ import annotations

"""特徴量を抜き差ししてモデルの実力を測る(アブレーション検証)。

目的:
現行モデルは gain 最大の特徴量が ai_honmei(WINTICKETが公開している「本命」印)で、
2位の2倍以上ある。つまり他人の予想を主軸にしている疑いがある。
公開印は既にオッズへ織り込まれているため、それに頼る限り市場には勝てない。

そこで「印を抜いたら何が残るか」を数値で出す。

評価の作法:
- 分割は必ずレースの「日付」で行う(race_key順ではない)。
  開催をまたぐリークを防ぐため、検証期間のレースは学習に一切入れない
- 精度(Top1)とLogLossに加え、**回収率**まで測る。
  当てにいく精度が上がっても、買って増えなければ意味がないため
- 回収率は確定オッズの実払戻のみを使う(捏造しない)

python scripts/ablation_study.py --db <path>
"""

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.features import FEATURE_NAMES, decode_features  # noqa: E402

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402

UNIT = 100
# 他人の予想(WINTICKETの公開印)由来の特徴量
MARK_FEATURES = ["ai_honmei", "ai_taiko", "ai_tanana", "ai_renshita"]


def load_rows(db: str) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        select e.race_key, e.car_no, e.features_json, e.finish_position, e.is_win,
               r.race_date, r.result_json, r.payouts_json
        from entries e
        join races r on r.race_key = e.race_key
        where e.finish_position is not null and e.features_json is not null
        """
    ).fetchall()
    out = []
    for row in rows:
        # 2026-08-10 に features_json を「値だけの配列」形式へ変えたため、
        # json.loads そのままでは list が返り .get で落ちる。復元は decode_features に任せる。
        feats = decode_features(row["features_json"])
        if not feats:
            continue
        out.append(
            {
                "race_key": row["race_key"],
                "car_no": int(row["car_no"] or 0),
                "features": feats,
                "label": int(row["is_win"] or 0),
                "date": _norm_date(row["race_date"]),
                "payouts": row["payouts_json"],
                "result": row["result_json"],
            }
        )
    conn.close()
    return [r for r in out if r["date"]]


def _norm_date(raw: str | None) -> str:
    """「2026年7月11日」「2026-07-11」を 2026-07-11 に揃える。"""
    if not raw:
        return ""
    import re

    m = re.search(r"(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})", str(raw))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def vec(features: dict, names: list[str]) -> list[float]:
    return [float(features.get(n, 0.0) or 0.0) for n in names]


def train_eval(rows: list[dict], names: list[str], cut_date: str, seed: int = 42) -> dict:
    """cut_date より前で学習し、以降で検証する。"""
    tr = [r for r in rows if r["date"] < cut_date]
    va = [r for r in rows if r["date"] >= cut_date]
    if not tr or not va:
        return {}

    Xtr = np.array([vec(r["features"], names) for r in tr], dtype=float)
    ytr = np.array([r["label"] for r in tr], dtype=float)
    Xva = np.array([vec(r["features"], names) for r in va], dtype=float)
    yva = np.array([r["label"] for r in va], dtype=float)

    pos = max(1.0, ytr.sum())
    neg = max(1.0, len(ytr) - pos)
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "num_leaves": 15,
        "learning_rate": 0.04,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 1,
        "min_data_in_leaf": 25,
        "lambda_l2": 1.0,
        "scale_pos_weight": min(6.0, neg / pos),
        "verbose": -1,
        "seed": seed,
        "deterministic": True,
    }
    booster = lgb.train(
        params,
        lgb.Dataset(Xtr, label=ytr),
        num_boost_round=400,
        valid_sets=[lgb.Dataset(Xva, label=yva)],
        callbacks=[lgb.early_stopping(40, verbose=False), lgb.log_evaluation(0)],
    )
    pred = booster.predict(Xva, num_iteration=booster.best_iteration)

    # レース単位に束ねる
    races: dict[str, list] = {}
    for r, p in zip(va, pred):
        races.setdefault(r["race_key"], []).append((float(p), r))

    hit = 0
    logloss_sum = 0.0
    n = 0
    stake = payout = 0
    exacta_hits = 0
    bets = 0
    returns: list[int] = []
    for key, items in races.items():
        items.sort(key=lambda x: -x[0])
        # Top1精度: 予測1位が実際に1着か
        if items[0][1]["label"] == 1:
            hit += 1
        for p, r in items:
            p = min(max(p, 1e-9), 1 - 1e-9)
            logloss_sum += -(r["label"] * math.log(p) + (1 - r["label"]) * math.log(1 - p))
            n += 1
        # 回収率: 現行運用と同じ2車単2点(1位固定 × 相手上位2)
        row0 = items[0][1]
        payouts = _json(row0["payouts"], {}) or {}
        odds = payouts.get("exacta")
        order = (_json(row0["result"], {}) or {}).get("finish_order") or []
        if odds and len(items) >= 3 and len(order) >= 2:
            picks = {(items[0][1]["car_no"], items[1][1]["car_no"]),
                     (items[0][1]["car_no"], items[2][1]["car_no"])}
            actual = (int(order[0]), int(order[1]))
            stake += UNIT * 2
            bets += 1
            if actual in picks:
                got = int(round(float(odds) * UNIT))
                payout += got
                exacta_hits += 1
                returns.append(got)
            else:
                returns.append(0)

    return {
        "features": len(names),
        "train_races": len({r["race_key"] for r in tr}),
        "valid_races": len(races),
        "top1": hit / len(races) if races else 0.0,
        "logloss": logloss_sum / n if n else 0.0,
        "bet_races": bets,
        "exacta_hit": exacta_hits / bets if bets else 0.0,
        "roi": payout / stake if stake else 0.0,
        "profit": payout - stake,
        "returns": returns,
        "booster": booster,
    }


def roi_ci(returns: list[int], trials: int = 3000) -> tuple[float, float]:
    """1レース(2点=200円)ごとの払戻からブートストラップで回収率の95%信頼区間を出す。"""
    import random

    if not returns:
        return (0.0, 0.0)
    rnd = random.Random(20260809)
    n = len(returns)
    rois = []
    for _ in range(trials):
        total = sum(returns[rnd.randrange(n)] for _ in range(n))
        rois.append(total / (n * UNIT * 2))
    rois.sort()
    return (rois[int(trials * 0.025)], rois[int(trials * 0.975)])


def _json(raw, default=None):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "keirin_learning.sqlite3"))
    ap.add_argument("--cut", default="", help="検証開始日 YYYY-MM-DD(既定は後ろ25%)")
    args = ap.parse_args()

    rows = load_rows(args.db)
    dates = sorted({r["date"] for r in rows})
    cut = args.cut or dates[int(len(dates) * 0.75)]
    print(f"教師: {len({r['race_key'] for r in rows}):,}レース / {len(rows):,}行")
    print(f"期間: {dates[0]} 〜 {dates[-1]}")
    print(f"分割: {cut} より前で学習、以降で検証(日付で厳密に分割)\n")

    variants = [
        ("現行(全45特徴)", FEATURE_NAMES),
        ("印なし(ai_*を除去)", [f for f in FEATURE_NAMES if f not in MARK_FEATURES]),
        ("印だけ", ["bias"] + MARK_FEATURES),
    ]

    print(f"{'条件':<22}{'特徴':>5}{'Top1':>8}{'LogLoss':>9}{'2車単的中':>10}{'回収率':>9}")
    print("-" * 66)
    results = {}
    for label, names in variants:
        res = train_eval(rows, names, cut)
        if not res:
            continue
        results[label] = res
        print(
            f"{label:<22}{res['features']:>5}{res['top1']*100:>7.1f}%"
            f"{res['logloss']:>9.4f}{res['exacta_hit']*100:>9.1f}%{res['roi']*100:>8.1f}%"
        )

    base = results.get("現行(全45特徴)")
    noai = results.get("印なし(ai_*を除去)")
    if base and noai:
        print()
        print(f"検証レース数: {base['valid_races']:,}")
        print(f"印を抜いたときのTop1低下: {(base['top1']-noai['top1'])*100:+.1f}ポイント")
        print(f"印を抜いたときの回収率変化: {(noai['roi']-base['roi'])*100:+.1f}ポイント")
        lo, hi = roi_ci(noai["returns"])
        print(f"印なしの回収率95%信頼区間: {lo*100:.1f}% 〜 {hi*100:.1f}%"
              f"  → {'100%超を主張できる' if lo > 1.0 else '100%超とは言えない'}")

    # --- 頑健性: 分割日とシードを変えても同じ傾向が出るか -------------
    print("\n\n=== 頑健性チェック: 分割日を変えて再現するか ===")
    print(f"{'検証開始日':<14}{'検証R':>7}{'現行ROI':>9}{'印なしROI':>10}{'差':>8}")
    print("-" * 50)
    diffs = []
    for frac in (0.55, 0.62, 0.70, 0.78, 0.85):
        c = dates[int(len(dates) * frac)]
        a = train_eval(rows, FEATURE_NAMES, c)
        b = train_eval(rows, [f for f in FEATURE_NAMES if f not in MARK_FEATURES], c)
        if not a or not b:
            continue
        d = (b["roi"] - a["roi"]) * 100
        diffs.append(d)
        print(f"{c:<14}{a['valid_races']:>7}{a['roi']*100:>8.1f}%{b['roi']*100:>9.1f}%{d:>+7.1f}")
    if diffs:
        print(f"\n  差の平均: {sum(diffs)/len(diffs):+.1f}ポイント / "
              f"印なしが上回った回数: {sum(1 for d in diffs if d>0)}/{len(diffs)}")

    print("\n=== 頑健性チェック: 乱数シードを変えても再現するか(分割は固定) ===")
    print(f"{'seed':<8}{'現行ROI':>9}{'印なしROI':>10}{'差':>8}")
    print("-" * 36)
    sdiffs = []
    for seed in (1, 7, 42, 123, 2026):
        a = train_eval(rows, FEATURE_NAMES, cut, seed=seed)
        b = train_eval(rows, [f for f in FEATURE_NAMES if f not in MARK_FEATURES], cut, seed=seed)
        if not a or not b:
            continue
        d = (b["roi"] - a["roi"]) * 100
        sdiffs.append(d)
        print(f"{seed:<8}{a['roi']*100:>8.1f}%{b['roi']*100:>9.1f}%{d:>+7.1f}")
    if sdiffs:
        print(f"\n  差の平均: {sum(sdiffs)/len(sdiffs):+.1f}ポイント / "
              f"印なしが上回った回数: {sum(1 for d in sdiffs if d>0)}/{len(sdiffs)}")

    # 現行モデルの重要度(全件)
    if base:
        gains = base["booster"].feature_importance(importance_type="gain")
        pairs = sorted(zip(FEATURE_NAMES, gains), key=lambda x: -x[1])
        total = sum(gains) or 1
        print("\n=== 現行モデルの特徴量重要度(gain上位15/全45) ===")
        for name, g in pairs[:15]:
            print(f"  {name:<24}{g:>10.0f}  ({g/total*100:>4.1f}%)")
        mark_share = sum(g for n, g in pairs if n in MARK_FEATURES) / total
        print(f"\n  ai_*(他人の印)の合計シェア: {mark_share*100:.1f}%")


if __name__ == "__main__":
    main()
