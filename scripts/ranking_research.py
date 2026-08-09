from __future__ import annotations

"""ランキング学習(LambdaRank)を試す。

いまの模型は「1着かどうか」の二値分類で学習している。しかし実際に欲しいのは
着順の並びであり、目的がずれている。2着・3着の情報を捨てているのも損。

LambdaRank はレースを1グループとして「並べ方の良さ」を直接最適化する。
先行調査でも着順予測にはランキング学習が整合すると指摘されていた。

比較するもの:
  binary      現行。1着=1, それ以外=0 の二値分類
  lambdarank  1着=3, 2着=2, 3着=1, それ以外=0 の順位学習

評価は同じ土俵(Top1精度・2車単的中率・回収率)で行い、
分割日を5通り変えた平均で判断する。

python scripts/ranking_research.py --db <path>
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

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402

from keirin_ai.features import FEATURE_NAMES, LINE_FEATURE_NAMES, MARK_FEATURE_NAMES  # noqa: E402
from scripts.ablation_study import roi_ci  # noqa: E402
from scripts.line_features_research import _json, _norm_date  # noqa: E402

UNIT = 100


def load_rows(db: str) -> list[dict]:
    """着順つきで読み込む(ランキング学習には順位が要る)。"""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    races = {
        r["race_key"]: r
        for r in conn.execute("select race_key, race_date, result_json, payouts_json from races").fetchall()
    }
    ents = conn.execute(
        """
        select race_key, car_no, features_json, finish_position, is_win
        from entries where finish_position is not null and features_json is not null
        """
    ).fetchall()
    conn.close()

    rows = []
    for e in ents:
        race = races.get(e["race_key"])
        if not race:
            continue
        date = _norm_date(race["race_date"])
        if not date:
            continue
        rows.append(
            {
                "race_key": e["race_key"],
                "car_no": int(e["car_no"] or 0),
                "features": _json(e["features_json"], {}) or {},
                "label": int(e["is_win"] or 0),
                "finish": int(e["finish_position"] or 99),
                "date": date,
                "payouts": race["payouts_json"],
                "result": race["result_json"],
            }
        )
    return rows


def relevance(finish: int) -> int:
    """着順を「並べ方の良さ」の点数へ。1着を最も重く、3着まで評価する。"""
    return {1: 3, 2: 2, 3: 1}.get(finish, 0)


def evaluate(va: list[dict], scores: np.ndarray) -> dict:
    """Top1精度・2車単的中率・回収率をまとめて出す(他の検証と同じ土俵)。"""
    races: dict[str, list] = {}
    for r, s in zip(va, scores):
        races.setdefault(r["race_key"], []).append((float(s), r))

    hit = 0
    stake = payout = 0
    bets = ex_hits = 0
    returns: list[int] = []
    for items in races.values():
        items.sort(key=lambda x: -x[0])
        if items[0][1]["label"] == 1:
            hit += 1
        row0 = items[0][1]
        payouts = _json(row0["payouts"], {}) or {}
        odds = payouts.get("exacta")
        order = (_json(row0["result"], {}) or {}).get("finish_order") or []
        if odds and len(items) >= 3 and len(order) >= 2:
            picks = {
                (items[0][1]["car_no"], items[1][1]["car_no"]),
                (items[0][1]["car_no"], items[2][1]["car_no"]),
            }
            actual = (int(order[0]), int(order[1]))
            stake += UNIT * 2
            bets += 1
            if actual in picks:
                got = int(round(float(odds) * UNIT))
                payout += got
                ex_hits += 1
                returns.append(got)
            else:
                returns.append(0)
    return {
        "top1": hit / len(races) if races else 0.0,
        "exacta_hit": ex_hits / bets if bets else 0.0,
        "roi": payout / stake if stake else 0.0,
        "races": len(races),
        "returns": returns,
    }


def train_eval(rows: list[dict], names: list[str], cut: str, objective: str, seed: int = 42) -> dict:
    tr = [r for r in rows if r["date"] < cut]
    va = [r for r in rows if r["date"] >= cut]
    if not tr or not va:
        return {}

    # ランキング学習はレース単位でまとめる必要がある
    tr.sort(key=lambda r: (r["race_key"], r["car_no"]))
    Xtr = np.array([[r["features"].get(n, 0.0) for n in names] for r in tr], dtype=float)
    Xva = np.array([[r["features"].get(n, 0.0) for n in names] for r in va], dtype=float)

    common = {
        "num_leaves": 15,
        "learning_rate": 0.04,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 1,
        "min_data_in_leaf": 25,
        "lambda_l2": 1.0,
        "verbose": -1,
        "seed": seed,
        "deterministic": True,
    }

    if objective == "lambdarank":
        ytr = np.array([relevance(r["finish"]) for r in tr], dtype=float)
        groups = []
        last = None
        for r in tr:
            if r["race_key"] != last:
                groups.append(0)
                last = r["race_key"]
            groups[-1] += 1
        dataset = lgb.Dataset(Xtr, label=ytr, group=groups)
        params = {**common, "objective": "lambdarank", "metric": "ndcg", "ndcg_eval_at": [1, 3], "label_gain": [0, 1, 3, 7]}
        booster = lgb.train(params, dataset, num_boost_round=300)
    else:
        ytr = np.array([r["label"] for r in tr], dtype=float)
        pos = max(1.0, ytr.sum())
        neg = max(1.0, len(ytr) - pos)
        params = {**common, "objective": "binary", "metric": "binary_logloss", "scale_pos_weight": min(6.0, neg / pos)}
        booster = lgb.train(params, lgb.Dataset(Xtr, label=ytr), num_boost_round=300)

    return evaluate(va, booster.predict(Xva))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "keirin_learning.sqlite3"))
    args = ap.parse_args()

    rows = load_rows(args.db)
    dates = sorted({r["date"] for r in rows})
    print(f"教師: {len({r['race_key'] for r in rows}):,}レース / {len(rows):,}行")
    print(f"期間: {dates[0]} 〜 {dates[-1]}\n")

    names = [f for f in FEATURE_NAMES if f not in MARK_FEATURE_NAMES] + LINE_FEATURE_NAMES
    cuts = [dates[int(len(dates) * f)] for f in (0.55, 0.62, 0.70, 0.78, 0.85)]

    print(f"{'seed':<6}{'二値ROI':>9}{'順位学習ROI':>12}{'差':>7}{'二値的中':>9}{'順位学習':>9}{'二値Top1':>9}{'順位Top1':>9}")
    print("-" * 76)
    roi_win = hit_win = top_win = 0
    trials = 0
    agg: dict[str, list] = {"binary": [], "lambdarank": []}
    rets: dict[str, list] = {"binary": [], "lambdarank": []}
    for seed in (1, 7, 42, 123, 2026):
        vals = {}
        for obj in ("binary", "lambdarank"):
            rs, hs, ts = [], [], []
            for cut in cuts:
                res = train_eval(rows, names, cut, obj, seed=seed)
                if not res:
                    continue
                rs.append(res["roi"])
                hs.append(res["exacta_hit"])
                ts.append(res["top1"])
                rets[obj].extend(res["returns"])
            vals[obj] = (sum(rs) / len(rs), sum(hs) / len(hs), sum(ts) / len(ts))
            agg[obj].append(vals[obj])
        b, l = vals["binary"], vals["lambdarank"]
        trials += 1
        roi_win += 1 if l[0] > b[0] else 0
        hit_win += 1 if l[1] > b[1] else 0
        top_win += 1 if l[2] > b[2] else 0
        print(
            f"{seed:<6}{b[0]*100:>8.1f}%{l[0]*100:>11.1f}%{(l[0]-b[0])*100:>+6.1f}"
            f"{b[1]*100:>8.1f}%{l[1]*100:>8.1f}%{b[2]*100:>8.1f}%{l[2]*100:>8.1f}%"
        )

    print(f"\n順位学習が上回った回数  回収率 {roi_win}/{trials} / 2車単的中 {hit_win}/{trials} / Top1 {top_win}/{trials}")
    print(f"\n{'目的関数':<14}{'回収率':>9}{'2車単的中':>10}{'Top1':>8}{'95%信頼区間':>20}")
    print("-" * 62)
    for obj, label in (("binary", "二値分類(現行)"), ("lambdarank", "順位学習")):
        v = agg[obj]
        r = sum(x[0] for x in v) / len(v)
        h = sum(x[1] for x in v) / len(v)
        t = sum(x[2] for x in v) / len(v)
        lo, hi = roi_ci(rets[obj])
        print(f"{label:<14}{r*100:>8.1f}%{h*100:>9.1f}%{t*100:>7.1f}%{f'{lo*100:.1f}〜{hi*100:.1f}%':>20}")


if __name__ == "__main__":
    main()
