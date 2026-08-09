from __future__ import annotations

"""決まり手(逃・捲・差・マーク)を特徴量にして効果を測る。

出走表の stats には各選手の決まり手回数(escape/makuri/sashi/mark)が
保有率100%で入っているのに、これまで一切使っていなかった。
先行調査では決まり手の分布は級班で激変し(チャレンジの逃げ38.8%に対し
S級9車立ては12.5%)、着順を左右する最重要因子のひとつとされる。

ここでは本体へ入れる前に、効果があるかを検証する。

python scripts/kimarite_research.py --db <path>
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.features import FEATURE_NAMES, LINE_FEATURE_NAMES, MARK_FEATURE_NAMES  # noqa: E402
from scripts.ablation_study import roi_ci, train_eval  # noqa: E402
from scripts.line_features_research import _json, _norm_date  # noqa: E402

KIMARITE_FEATURES = [
    "km_nige_rate",     # 逃げ比率(自分の決まり手のうち)
    "km_makuri_rate",   # 捲り比率
    "km_sashi_rate",    # 差し比率
    "km_mark_rate",     # マーク比率
    "km_jiriki",        # 自力度 =(逃+捲)/全体。前を取りにいく選手か
    "km_total",         # 決まり手の総数(データの厚み)
    "km_jiriki_z",      # 自力度のレース内相対
    "km_nige_z",        # 逃げ比率のレース内相対
]


def build_kimarite(stats_by_car: dict[int, dict]) -> dict[int, dict]:
    """決まり手の回数から比率を作り、レース内で相対化する。"""
    raw = {}
    for car, s in stats_by_car.items():
        nige = float(s.get("escape") or 0.0)
        makuri = float(s.get("makuri") or 0.0)
        sashi = float(s.get("sashi") or 0.0)
        mark = float(s.get("mark") or 0.0)
        total = nige + makuri + sashi + mark
        if total <= 0:
            raw[car] = {"nige": 0.0, "makuri": 0.0, "sashi": 0.0, "mark": 0.0, "jiriki": 0.0, "total": 0.0}
        else:
            raw[car] = {
                "nige": nige / total,
                "makuri": makuri / total,
                "sashi": sashi / total,
                "mark": mark / total,
                "jiriki": (nige + makuri) / total,
                "total": min(total, 20.0) / 20.0,
            }

    jir = [v["jiriki"] for v in raw.values()]
    nig = [v["nige"] for v in raw.values()]

    def z(vals, x):
        m = sum(vals) / len(vals) if vals else 0.0
        sd = (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5 if vals else 0.0
        return (x - m) / sd if sd > 0 else 0.0

    return {
        car: {
            "km_nige_rate": v["nige"],
            "km_makuri_rate": v["makuri"],
            "km_sashi_rate": v["sashi"],
            "km_mark_rate": v["mark"],
            "km_jiriki": v["jiriki"],
            "km_total": v["total"],
            "km_jiriki_z": z(jir, v["jiriki"]),
            "km_nige_z": z(nig, v["nige"]),
        }
        for car, v in raw.items()
    }


def load_rows(db: str) -> list[dict]:
    """entries の特徴量に、predictions の stats から作った決まり手特徴を足す。"""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    races = {
        r["race_key"]: r
        for r in conn.execute(
            "select race_key, race_date, result_json, payouts_json from races"
        ).fetchall()
    }
    # stats は predictions.ranking_json にしか無い(entriesには列が無い)
    stats_by_race: dict[str, dict[int, dict]] = {}
    for r in conn.execute("select race_key, ranking_json from predictions"):
        ranking = _json(r["ranking_json"], []) or []
        m = {}
        for e in ranking:
            car = int(e.get("car_no") or 0)
            if car:
                m[car] = e.get("stats") or {}
        if m:
            stats_by_race.setdefault(r["race_key"], m)

    ents = conn.execute(
        """
        select race_key, car_no, features_json, is_win
        from entries where finish_position is not null and features_json is not null
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
        stats = stats_by_race.get(key) or {}
        km = build_kimarite({int(m["car_no"] or 0): stats.get(int(m["car_no"] or 0), {}) for m in members})
        for m in members:
            car = int(m["car_no"] or 0)
            feats = _json(m["features_json"], {}) or {}
            feats.update(km.get(car, {}))
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
    covered = sum(1 for r in rows if r["features"].get("km_total", 0) > 0)
    print(f"教師: {len({r['race_key'] for r in rows}):,}レース / {len(rows):,}行")
    print(f"決まり手データあり: {covered:,}行 ({covered/max(1,len(rows))*100:.1f}%)")
    print(f"期間: {dates[0]} 〜 {dates[-1]}\n")

    no_mark = [f for f in FEATURE_NAMES if f not in MARK_FEATURE_NAMES]
    base = no_mark + LINE_FEATURE_NAMES
    variants = [
        ("現行(印なし+ライン)", base),
        ("+ 決まり手", base + KIMARITE_FEATURES),
    ]

    cuts = [dates[int(len(dates) * f)] for f in (0.55, 0.62, 0.70, 0.78, 0.85)]
    print(f"{'条件':<24}{'特徴':>5}{'Top1':>8}{'2車単的中':>10}{'回収率':>9}{'95%信頼区間':>20}")
    print("-" * 78)
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
        lo, hi = roi_ci(rets)
        print(
            f"{label:<24}{len(names):>5}{sum(accs)/len(accs)*100:>7.1f}%"
            f"{sum(hits)/len(hits)*100:>9.1f}%{sum(rois)/len(rois)*100:>8.1f}%"
            f"{f'{lo*100:.1f}〜{hi*100:.1f}%':>20}"
        )

    # 決まり手そのものの効き方を確認する
    print("\n=== 自力度(逃+捲の比率)と1着率の関係 ===")
    buckets: dict[str, list] = {}
    for r in rows:
        j = r["features"].get("km_jiriki", 0.0)
        if r["features"].get("km_total", 0) <= 0:
            continue
        key = "0.0-0.2" if j < 0.2 else "0.2-0.4" if j < 0.4 else "0.4-0.6" if j < 0.6 else "0.6-0.8" if j < 0.8 else "0.8-1.0"
        buckets.setdefault(key, []).append(r["label"])
    print(f"{'自力度':<12}{'件数':>8}{'1着率':>8}")
    for k in sorted(buckets):
        v = buckets[k]
        print(f"{k:<12}{len(v):>8}{sum(v)/len(v)*100:>7.1f}%")


if __name__ == "__main__":
    main()
