from __future__ import annotations

"""EXデータを全項目使う効果を測る。

WINTICKETのEXデータは6項目あるが、現状の使い方は情報を大きく捨てている:
  exSplitLine   保有率76.7% → **未使用**(最も充実しているのに)
  exLeftBehind  保有率59.0% → ex_left_behind として使用
  exSpurt       保有率39.4% ┐
  exThrust      保有率38.7% ├→ 3つまとめて max を取り ex_attack 1個に潰している
  exSnatch      保有率23.3% ┘
  exCompete     保有率10.8% → **未使用**

問題点:
1. exSplitLine と exCompete を丸ごと捨てている
2. 攻撃系3項目を max で1つに潰し、どの攻め方が得意かの区別を失っている
3. 欠損を0で埋めているため、モデルが「成功率0%」と「データなし」を
   区別できない。保有率が4割前後の項目では致命的

対策として、全項目を個別に渡し、欠損か否かのフラグも併せて渡す。

python scripts/ex_research.py --db <path>
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
from scripts.ablation_study import roi_ci  # noqa: E402
from scripts.class_model_research import _groups, PARAMS  # noqa: E402
from scripts.line_features_research import _json, _norm_date  # noqa: E402
from scripts.ranking_research import evaluate, relevance  # noqa: E402

# EXの生項目(いずれも0〜100の成功率/発生率)
EX_FIELDS = ["exSplitLine", "exLeftBehind", "exSpurt", "exThrust", "exSnatch", "exCompete"]

EX_FEATURES = [
    "exf_split_line", "exf_split_line_has",
    "exf_left_behind", "exf_left_behind_has",
    "exf_spurt", "exf_spurt_has",
    "exf_thrust", "exf_thrust_has",
    "exf_snatch", "exf_snatch_has",
    "exf_compete", "exf_compete_has",
    "exf_attack_max",     # 攻撃系の最大(従来の ex_attack 相当)
    "exf_attack_count",   # 攻撃系のうちデータがある項目数
    "exf_split_line_z",   # レース内相対(誰が離れやすい/ちぎりやすいか)
]

_SHORT = {
    "exSplitLine": "split_line",
    "exLeftBehind": "left_behind",
    "exSpurt": "spurt",
    "exThrust": "thrust",
    "exSnatch": "snatch",
    "exCompete": "compete",
}


def build_ex_features(ex_by_car: dict[int, dict]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    raw_split: dict[int, float] = {}

    for car, ex in ex_by_car.items():
        ex = ex if isinstance(ex, dict) else {}
        row: dict[str, float] = {}
        for field in EX_FIELDS:
            short = _SHORT[field]
            value = ex.get(field)
            has = value is not None
            row[f"exf_{short}"] = (float(value) / 100.0) if has else 0.0
            row[f"exf_{short}_has"] = 1.0 if has else 0.0
        attack = [ex.get(f) for f in ("exSpurt", "exThrust", "exSnatch")]
        present = [float(v) for v in attack if v is not None]
        row["exf_attack_max"] = (max(present) / 100.0) if present else 0.0
        row["exf_attack_count"] = len(present) / 3.0
        out[car] = row
        raw_split[car] = row["exf_split_line"]

    vals = list(raw_split.values())
    mean = sum(vals) / len(vals) if vals else 0.0
    sd = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5 if vals else 0.0
    for car in out:
        out[car]["exf_split_line_z"] = ((raw_split[car] - mean) / sd) if sd > 0 else 0.0
    return out


def load_rows(db: str) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    races = {
        r["race_key"]: r
        for r in conn.execute("select race_key, race_date, result_json, payouts_json from races").fetchall()
    }
    # ex は predictions.ranking_json にしか無い
    ex_by_race: dict[str, dict[int, dict]] = {}
    for r in conn.execute("select race_key, ranking_json from predictions"):
        ranking = _json(r["ranking_json"], []) or []
        m = {int(e.get("car_no") or 0): (e.get("ex") or {}) for e in ranking if e.get("car_no")}
        if m:
            ex_by_race.setdefault(r["race_key"], m)

    ents = conn.execute(
        """
        select race_key, car_no, features_json, finish_position, is_win
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
        ex_map = ex_by_race.get(key) or {}
        feats_ex = build_ex_features({int(m["car_no"] or 0): ex_map.get(int(m["car_no"] or 0), {}) for m in members})
        for m in members:
            car = int(m["car_no"] or 0)
            feats = _json(m["features_json"], {}) or {}
            feats.update(feats_ex.get(car, {n: 0.0 for n in EX_FEATURES}))
            rows.append(
                {
                    "race_key": key,
                    "car_no": car,
                    "features": feats,
                    "label": int(m["is_win"] or 0),
                    "finish": int(m["finish_position"] or 99),
                    "date": date,
                    "payouts": race["payouts_json"],
                    "result": race["result_json"],
                }
            )
    return rows


def run(rows, names, cuts, seed):
    accs, hits, rois, rets = [], [], [], []
    for cut in cuts:
        tr = sorted([r for r in rows if r["date"] < cut], key=lambda r: (r["race_key"], r["car_no"]))
        va = [r for r in rows if r["date"] >= cut]
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
        rois.append(res["roi"])
        rets.extend(res["returns"])
    return sum(accs) / len(accs), sum(hits) / len(hits), sum(rois) / len(rois), rets


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "keirin_learning.sqlite3"))
    args = ap.parse_args()

    rows = load_rows(args.db)
    dates = sorted({r["date"] for r in rows})
    cuts = [dates[int(len(dates) * f)] for f in (0.55, 0.62, 0.70, 0.78, 0.85)]
    base = [f for f in FEATURE_NAMES if f not in MARK_FEATURE_NAMES] + LINE_FEATURE_NAMES

    print(f"教師: {len({r['race_key'] for r in rows}):,}レース / {len(rows):,}行\n")

    print("=== 各EX項目の保有率と、1着率との関係 ===")
    print(f"{'項目':<18}{'保有率':>8}{'データ無し':>10}{'低い群':>9}{'高い群':>9}")
    print("-" * 56)
    for field in EX_FIELDS:
        short = _SHORT[field]
        has = [r for r in rows if r["features"].get(f"exf_{short}_has")]
        non = [r for r in rows if not r["features"].get(f"exf_{short}_has")]
        if not has:
            continue
        vals = sorted(r["features"][f"exf_{short}"] for r in has)
        med = vals[len(vals) // 2]
        lo = [r["label"] for r in has if r["features"][f"exf_{short}"] <= med]
        hi = [r["label"] for r in has if r["features"][f"exf_{short}"] > med]
        f_non = f"{sum(r['label'] for r in non)/len(non)*100:.1f}%" if non else "-"
        f_lo = f"{sum(lo)/len(lo)*100:.1f}%" if lo else "-"
        f_hi = f"{sum(hi)/len(hi)*100:.1f}%" if hi else "-"
        print(f"{field:<18}{len(has)/len(rows)*100:>7.1f}%{f_non:>10}{f_lo:>9}{f_hi:>9}")

    print(f"\n{'条件':<24}{'Top1':>8}{'2車単的中':>10}{'回収率':>9}{'95%信頼区間':>20}")
    print("-" * 72)
    summary = {}
    for label, ns in [("現行(EX2特徴)", base), ("+ EX全項目", base + EX_FEATURES)]:
        vals = []
        T: list[int] = []
        for seed in (1, 7, 42, 123, 2026):
            a, h, r, t = run(rows, ns, cuts, seed)
            vals.append((a, h, r))
            T.extend(t)
        summary[label] = vals
        lo, hi = roi_ci(T)
        print(
            f"{label:<24}{sum(v[0] for v in vals)/len(vals)*100:>7.1f}%"
            f"{sum(v[1] for v in vals)/len(vals)*100:>9.1f}%"
            f"{sum(v[2] for v in vals)/len(vals)*100:>8.1f}%{f'{lo*100:.1f}〜{hi*100:.1f}%':>20}"
        )

    a, b = summary["現行(EX2特徴)"], summary["+ EX全項目"]
    print(
        f"\n上回った回数  回収率 {sum(1 for x, y in zip(b, a) if x[2] > y[2])}/{len(a)}"
        f" / 2車単的中 {sum(1 for x, y in zip(b, a) if x[1] > y[1])}/{len(a)}"
        f" / Top1 {sum(1 for x, y in zip(b, a) if x[0] > y[0])}/{len(a)}"
    )


if __name__ == "__main__":
    main()
