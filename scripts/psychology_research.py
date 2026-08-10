from __future__ import annotations

"""「人の心理」に関わる3つの要因を検証する。

段階(何がかかっているか)で当たりやすさが2倍違ったのに続き、
同じ発想でまだ使っていない要因を調べる。

1. 地元選手
   出身県と開催場の所在県が一致する選手は、地元の期待を背負って走る。
   WINTICKETのvenueオブジェクトに prefecture があるので判定できる。

2. 選手個人の級班(A1/A2/A3/S1/S2)
   これまでレース単位の class_s / class_a しか使っておらず、
   **選手一人ひとりの級班を渡していなかった**。
   ただし級班別の生の勝率は出走構成に引きずられる(A3だけのチャレンジ戦では
   誰かが必ず勝つので見かけの勝率が上がる)。
   そこで「レース内で自分が何番目に上の級班か」という相対値にする。

3. ガールズ(L級)
   ラインを組まない競走なので、ライン特徴(ln_*)が意味を持たない。
   ガールズだけ別扱いにする価値があるかを見る。

python scripts/psychology_research.py --db <path>
"""

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402

from keirin_ai.features import FEATURE_NAMES, LINE_FEATURE_NAMES, MARK_FEATURE_NAMES  # noqa: E402
from scripts.class_model_research import _groups, PARAMS  # noqa: E402
from scripts.line_features_research import _json, _norm_date  # noqa: E402
from scripts.ranking_research import evaluate, relevance  # noqa: E402

PSY_FEATURES = [
    "ps_home",          # 地元(出身県=開催場の県)
    "ps_home_count",    # レース内の地元選手の割合
    "ps_class_value",   # 自分の級班の格(S1が最上位)
    "ps_class_z",       # 級班の格のレース内相対
    "ps_class_top",     # レース内で最上位の級班か
    "ps_class_bottom",  # レース内で最下位の級班か
]

# 級班の格。上ほど強い。ガールズ(L)は男子と別体系なので中庸に置く。
CLASS_VALUE = {"S1": 5.0, "S2": 4.0, "A1": 3.0, "A2": 2.0, "A3": 1.0, "L1": 2.5}


def build_psy(members: list[dict], venue_pref: str) -> dict[int, dict]:
    vals = []
    for m in members:
        vals.append(CLASS_VALUE.get(str(m["cls"] or "").strip().upper(), 0.0))
    known = [v for v in vals if v > 0]
    mean = sum(known) / len(known) if known else 0.0
    sd = (sum((v - mean) ** 2 for v in known) / len(known)) ** 0.5 if known else 0.0
    top = max(known) if known else 0.0
    bottom = min(known) if known else 0.0

    homes = [
        1.0 if (venue_pref and str(m["pref"] or "").strip() and str(m["pref"]).strip() in venue_pref) else 0.0
        for m in members
    ]
    home_ratio = sum(homes) / len(homes) if homes else 0.0

    # 級班が全員同じレース(チャレンジ戦など)では「最上位」に意味が無い。
    # 差があるレースでだけフラグを立てる。そうしないと全員が
    # 最上位かつ最下位になり、特徴量が壊れる。
    mixed = len({v for v in known}) > 1

    out = {}
    for m, v, h in zip(members, vals, homes):
        out[int(m["car_no"] or 0)] = {
            "ps_home": h,
            "ps_home_count": home_ratio,
            "ps_class_value": v / 5.0,
            "ps_class_z": ((v - mean) / sd) if (sd > 0 and v > 0) else 0.0,
            "ps_class_top": 1.0 if (mixed and v > 0 and v == top) else 0.0,
            "ps_class_bottom": 1.0 if (mixed and v > 0 and v == bottom) else 0.0,
        }
    return out


def load_rows(db: str) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    venue_pref = {
        str(r["venue_id"]): str(r["prefecture"] or "")
        for r in conn.execute("select venue_id, coalesce(prefecture,'') prefecture from venues")
    }
    races = {
        r["race_key"]: r
        for r in conn.execute(
            """
            select race_key, race_date, result_json, payouts_json,
                   coalesce(venue_id,'') venue_id, coalesce(race_class_official,'') cls
            from races
            """
        )
    }
    ents = conn.execute(
        """
        select race_key, car_no, coalesce(prefecture,'') pref, coalesce(class,'') cls,
               features_json, finish_position, is_win
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
        psy = build_psy(members, venue_pref.get(race["venue_id"], ""))
        for m in members:
            car = int(m["car_no"] or 0)
            feats = _json(m["features_json"], {}) or {}
            feats.update(psy.get(car, {n: 0.0 for n in PSY_FEATURES}))
            rows.append(
                {
                    "race_key": key,
                    "car_no": car,
                    "features": feats,
                    "label": int(m["is_win"] or 0),
                    "finish": int(m["finish_position"] or 99),
                    "date": date,
                    "is_girls": str(race["cls"]).startswith("L"),
                    "payouts": race["payouts_json"],
                    "result": race["result_json"],
                }
            )
    return rows


def run(rows, names, cuts, seed, subset=None):
    accs, hits = [], []
    for cut in cuts:
        tr = sorted([r for r in rows if r["date"] < cut], key=lambda r: (r["race_key"], r["car_no"]))
        va = [r for r in rows if r["date"] >= cut]
        if subset is not None:
            va = [r for r in va if subset(r)]
        if not tr or not va:
            continue
        X = np.array([[r["features"].get(n, 0.0) for n in names] for r in tr], dtype=float)
        Xv = np.array([[r["features"].get(n, 0.0) for n in names] for r in va], dtype=float)
        y = np.array([relevance(r["finish"]) for r in tr], dtype=float)
        m = lgb.train({**PARAMS, "seed": seed, "deterministic": True},
                      lgb.Dataset(X, label=y, group=_groups(tr)), num_boost_round=300)
        res = evaluate(va, m.predict(Xv))
        accs.append(res["top1"])
        hits.append(res["exacta_hit"])
    if not accs:
        return None
    return sum(accs) / len(accs), sum(hits) / len(hits)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "keirin_learning.sqlite3"))
    args = ap.parse_args()

    rows = load_rows(args.db)
    dates = sorted({r["date"] for r in rows})
    cuts = [dates[int(len(dates) * f)] for f in (0.62, 0.70, 0.78, 0.85)]
    base = [f for f in FEATURE_NAMES if f not in MARK_FEATURE_NAMES] + LINE_FEATURE_NAMES

    home = [r for r in rows if r["features"].get("ps_home")]
    print(f"教師: {len({r['race_key'] for r in rows}):,}レース / {len(rows):,}行")
    print(f"地元判定できた選手: {len(home):,} ({len(home)/max(1,len(rows))*100:.1f}%)\n")

    print("=== 1. 地元かどうかと1着率 ===")
    for flag, label in ((1.0, "地元"), (0.0, "地元でない")):
        v = [r["label"] for r in rows if r["features"].get("ps_home") == flag]
        if v:
            print(f"  {label:<10}{len(v):>7}件  1着率 {sum(v)/len(v)*100:>5.1f}%")

    print("\n=== 2. レース内での級班の相対位置と1着率 ===")
    for lab, key in (("最上位の級班", "ps_class_top"), ("最下位の級班", "ps_class_bottom")):
        v = [r["label"] for r in rows if r["features"].get(key) == 1.0]
        if v:
            print(f"  {lab:<12}{len(v):>7}件  1着率 {sum(v)/len(v)*100:>5.1f}%")
    v = [r["label"] for r in rows if r["features"].get("ps_class_top") == 0.0 and r["features"].get("ps_class_bottom") == 0.0]
    if v:
        print(f"  {'中間':<12}{len(v):>7}件  1着率 {sum(v)/len(v)*100:>5.1f}%")

    print(f"\n=== 3つを特徴量に足した効果 ===")
    print(f"{'条件':<20}{'Top1':>8}{'2車単的中':>10}")
    print("-" * 40)
    seeds = (1, 7, 42, 123, 2026)
    res = {}
    for label, ns in [("現行", base), ("+ 心理3要因", base + PSY_FEATURES)]:
        vals = [v for v in (run(rows, ns, cuts, s) for s in seeds) if v]
        res[label] = vals
        print(f"{label:<20}{sum(v[0] for v in vals)/len(vals)*100:>7.1f}%{sum(v[1] for v in vals)/len(vals)*100:>9.1f}%")
    a, b = res["現行"], res["+ 心理3要因"]
    print(
        f"  上回った回数  2車単的中 {sum(1 for x, y in zip(b, a) if x[1] > y[1])}/{len(a)}"
        f" / Top1 {sum(1 for x, y in zip(b, a) if x[0] > y[0])}/{len(a)}"
    )

    print(f"\n=== 3. ガールズ(L級)は別物か ===")
    girls = [r for r in rows if r["is_girls"]]
    print(f"ガールズ: {len({r['race_key'] for r in girls}):,}レース")
    if len({r["race_key"] for r in girls}) >= 100:
        print(f"{'条件':<26}{'Top1':>8}{'2車単的中':>10}")
        print("-" * 46)
        no_line = [f for f in FEATURE_NAMES if f not in MARK_FEATURE_NAMES]
        for label, ns in [
            ("現行(ライン特徴あり)", base),
            ("ライン特徴を外す", no_line),
            ("現行+心理3要因", base + PSY_FEATURES),
        ]:
            vals = [v for v in (run(rows, ns, cuts, s, subset=lambda r: r["is_girls"]) for s in (1, 42, 2026)) if v]
            if vals:
                print(f"{label:<26}{sum(v[0] for v in vals)/len(vals)*100:>7.1f}%{sum(v[1] for v in vals)/len(vals)*100:>9.1f}%")


if __name__ == "__main__":
    main()
