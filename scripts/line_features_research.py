from __future__ import annotations

"""ライン(隊列)の力学を表す特徴量を試作し、効果があるか先に検証する。

競輪は個人競技ではなくラインの勝負なのに、現行モデルのライン特徴は
「先頭選手の競走得点によるライン順位」程度しかない。
番手選手の実力、ライン間の力量差、主導権を取れるかといった、
実際に着順を左右する要素が入っていない。

方針: 本体(features.py)へ配線する前に、ここで作って効果を測る。
効かない特徴量を本番に入れて再学習の面倒を増やさないため。

python scripts/line_features_research.py --db <path>
"""

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.features import FEATURE_NAMES  # noqa: E402
from scripts.ablation_study import MARK_FEATURES, roi_ci, train_eval  # noqa: E402

# 追加を検討するライン特徴量
LINE_FEATURES = [
    "ln_solo",            # 単騎(ラインを組んでいない)
    "ln_pos",             # ライン内の位置(0=先頭)
    "ln_size_rel",        # 自ラインの長さ / 最長ラインの長さ
    "ln_own_avg_score",   # 自ライン全員の平均競走得点
    "ln_score_gap",       # 自ライン先頭 − 他ライン最強先頭 の得点差
    "ln_is_top_line",     # 自ラインが最強ラインか
    "ln_leader_back",     # 自ライン先頭のバック回数(主導権を取れるか)
    "ln_leader_escape",   # 自ライン先頭が逃げ脚質か
    "ln_selfpower_ratio", # レース内の自力型(逃・両)の比率=主導権争いの激しさ
    "ln_line_count",      # ライン数
    "ln_second_of_top",   # 最強ラインの番手(いちばん美味しい位置とされる)
    "ln_score_in_line",   # 自分の得点 − 自ライン平均(ライン内での強さ)
]

# 調査で裏付けの取れた知見にもとづく追加案
# - 主導権(バック取得)ラインは先頭のバック数・ホーム数・ライン長・番手の得点差・
#   番手が先頭より年上か の5変数で56.7%の精度で予測できる(遠山027, 3,442件)
# - 競輪は絶対値でなく「レース内の相対値」で効く
# - 番手が先頭を差す「裏スジ」は全レースの15.53%で起こり、無視できない
LINE_FEATURES_V2 = LINE_FEATURES + [
    "ln_leader_home",      # 自ライン先頭のホーム回数(主導権の代理変数)
    "ln_bante_score_diff", # 番手の得点 − レース中央値(遠山027のG指数値)
    "ln_bante_older",      # 番手が先頭より年上か(同上・有効変数)
    "ln_score_z",          # 自分の競走得点のレース内z-score
    "ln_win_rate_z",       # 自分の勝率のレース内z-score
]


def _norm_date(raw):
    if not raw:
        return ""
    m = re.search(r"(\d{4})[年\-/](\d{1,2})[月\-/](\d{1,2})", str(raw))
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else ""


def _json(raw, default=None):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def build_line_features(lineup: list[list[int]], riders: dict[int, dict]) -> dict[int, dict]:
    """1レース分の隊列から、各車のライン特徴を作る。"""
    # 隊列に載っていない車は単騎として扱う
    lines: list[list[int]] = []
    seen: set[int] = set()
    for raw in lineup or []:
        line = [int(c) for c in raw if int(c) in riders and int(c) not in seen]
        if line:
            lines.append(line)
            seen.update(line)
    for car in riders:
        if car not in seen:
            lines.append([car])
    if not lines:
        return {}

    def score(car: int) -> float:
        return float(riders[car].get("racing_score") or 0.0)

    def back(car: int) -> float:
        # features_json 内の back_count は 12回で1.0に正規化済み
        return float((riders[car].get("feats") or {}).get("back_count") or 0.0)

    leader_scores = [score(l[0]) for l in lines]
    best_leader = max(leader_scores) if leader_scores else 0.0
    max_len = max(len(l) for l in lines)
    self_power = sum(1 for c in riders if str(riders[c].get("style") or "") in ("逃", "両"))
    top_line_idx = leader_scores.index(best_leader) if leader_scores else -1

    def home(car: int) -> float:
        return float((riders[car].get("feats") or {}).get("home_count") or 0.0)

    def age(car: int) -> float:
        return float(riders[car].get("age") or 0.0)

    def win_rate(car: int) -> float:
        return float((riders[car].get("feats") or {}).get("win_rate") or 0.0)

    # レース内での相対化(競輪は絶対値でなく相対勝負)
    all_scores = [score(c) for c in riders]
    med = sorted(all_scores)[len(all_scores) // 2] if all_scores else 0.0
    mean_s = sum(all_scores) / len(all_scores) if all_scores else 0.0
    sd_s = (sum((s - mean_s) ** 2 for s in all_scores) / len(all_scores)) ** 0.5 if all_scores else 0.0
    all_wr = [win_rate(c) for c in riders]
    mean_w = sum(all_wr) / len(all_wr) if all_wr else 0.0
    sd_w = (sum((w - mean_w) ** 2 for w in all_wr) / len(all_wr)) ** 0.5 if all_wr else 0.0

    out: dict[int, dict] = {}
    for idx, line in enumerate(lines):
        avg = sum(score(c) for c in line) / len(line)
        # 自ラインを除いた他ラインの最強先頭
        others = [leader_scores[i] for i in range(len(lines)) if i != idx]
        rival_best = max(others) if others else leader_scores[idx]
        bante = line[1] if len(line) > 1 else None
        for pos, car in enumerate(line):
            out[car] = {
                "ln_leader_home": home(line[0]),
                "ln_bante_score_diff": ((score(bante) - med) / 10.0) if bante else 0.0,
                "ln_bante_older": (1.0 if (bante and age(bante) > age(line[0])) else 0.0),
                "ln_score_z": ((score(car) - mean_s) / sd_s) if sd_s > 0 else 0.0,
                "ln_win_rate_z": ((win_rate(car) - mean_w) / sd_w) if sd_w > 0 else 0.0,
            }
            out[car].update({
                "ln_solo": 1.0 if len(line) == 1 else 0.0,
                "ln_pos": min(pos, 3) / 3.0,
                "ln_size_rel": len(line) / max_len if max_len else 0.0,
                # 競走得点は概ね50〜120。74を中心に10で割って揃える(既存のracing_scoreと同じ流儀)
                "ln_own_avg_score": (avg - 74.0) / 10.0,
                "ln_score_gap": (score(line[0]) - rival_best) / 10.0,
                "ln_is_top_line": 1.0 if idx == top_line_idx else 0.0,
                "ln_leader_back": back(line[0]),
                "ln_leader_escape": 1.0 if str(riders[line[0]].get("style") or "") == "逃" else 0.0,
                "ln_selfpower_ratio": self_power / max(1, len(riders)),
                "ln_line_count": min(len(lines), 5) / 5.0,
                "ln_second_of_top": 1.0 if (idx == top_line_idx and pos == 1) else 0.0,
                "ln_score_in_line": (score(car) - avg) / 10.0,
            })
    return out


def load_rows(db: str) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    races = {
        r["race_key"]: r
        for r in conn.execute(
            "select race_key, race_date, lineup_json, result_json, payouts_json from races"
        ).fetchall()
    }
    ents = conn.execute(
        """
        select race_key, car_no, racing_score, style, age, features_json, finish_position, is_win
        from entries
        where finish_position is not null and features_json is not null
        """
    ).fetchall()
    conn.close()

    by_race: dict[str, list] = {}
    for e in ents:
        by_race.setdefault(e["race_key"], []).append(e)

    rows = []
    for key, members in by_race.items():
        race = races.get(key)
        if not race:
            continue
        date = _norm_date(race["race_date"])
        if not date:
            continue
        riders = {
            int(m["car_no"] or 0): {
                "racing_score": m["racing_score"],
                "style": m["style"],
                "age": m["age"],
                "feats": _json(m["features_json"], {}) or {},
            }
            for m in members
        }
        line_feats = build_line_features(_json(race["lineup_json"], []) or [], riders)
        for m in members:
            car = int(m["car_no"] or 0)
            feats = _json(m["features_json"], {}) or {}
            feats.update(line_feats.get(car, {}))
            rows.append(
                {
                    "race_key": key,
                    "car_no": car,
                    "features": feats,
                    "label": int(m["is_win"] or 0),
                    "date": date,
                    "payouts": race["payouts_json"],
                    "result": race["result_json"],
                }
            )
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "keirin_learning.sqlite3"))
    args = ap.parse_args()

    rows = load_rows(args.db)
    dates = sorted({r["date"] for r in rows})
    print(f"教師: {len({r['race_key'] for r in rows}):,}レース / {len(rows):,}行")
    print(f"期間: {dates[0]} 〜 {dates[-1]}\n")

    no_mark = [f for f in FEATURE_NAMES if f not in MARK_FEATURES]
    variants = [
        ("現行(全45)", FEATURE_NAMES),
        ("印なし", no_mark),
        ("印なし + ライン特徴", no_mark + LINE_FEATURES),
        ("印なし + ライン特徴v2", no_mark + LINE_FEATURES_V2),
        ("現行 + ライン特徴v2", FEATURE_NAMES + LINE_FEATURES_V2),
    ]

    # 分割日を複数試し、平均で評価する(1回の分割で判断しない)
    cuts = [dates[int(len(dates) * f)] for f in (0.55, 0.62, 0.70, 0.78, 0.85)]
    print(f"{'条件':<24}{'特徴':>5}{'Top1':>8}{'2車単的中':>10}{'回収率':>9}")
    print("-" * 58)
    summary = {}
    for label, names in variants:
        accs, hits, rois, rets = [], [], [], []
        for cut in cuts:
            res = train_eval(rows, names, cut)
            if not res:
                continue
            accs.append(res["top1"])
            hits.append(res["exacta_hit"])
            rois.append(res["roi"])
            rets.extend(res["returns"])
        if not rois:
            continue
        summary[label] = (sum(rois) / len(rois), rets)
        print(
            f"{label:<24}{len(names):>5}{sum(accs)/len(accs)*100:>7.1f}%"
            f"{sum(hits)/len(hits)*100:>9.1f}%{sum(rois)/len(rois)*100:>8.1f}%"
        )
    print("\n※ 5通りの分割日で学習・検証した平均。1回の分割の当たり外れを均すため。")

    print(f"\n{'条件':<24}{'回収率95%信頼区間':>24}{'判定':>14}")
    print("-" * 64)
    for label, (roi, rets) in summary.items():
        lo, hi = roi_ci(rets)
        verdict = "100%超と言える" if lo > 1.0 else "偶然の範囲"
        print(f"{label:<24}{f'{lo*100:.1f}% 〜 {hi*100:.1f}%':>24}{verdict:>14}")


if __name__ == "__main__":
    main()
