from __future__ import annotations

"""レースに「何がかかっているか」で、予想の当たりやすさが変わるかを調べる。

競輪では、決勝や勝ち上がりのかかった一戦と、何もかからない敗者戦とで
選手の本気度が違う——というのは現場の常識でありながら数値化されにくい。
数値化されにくいものは、モデルにも市場にも織り込まれにくい。

調べるのは2つ:
  ① 段階を特徴量に足すとモデルが良くなるか
  ② 段階によって「当たりやすさ」自体が違うか
     → ②は買うレースの選別に直接使える。当たらない種類のレースを
       避けるだけで、予想精度を変えずに成績が上がる。

python scripts/motivation_research.py --db <path>
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

MOTIVATION_FEATURES = [
    "st_final",        # 決勝(最高の動機づけ)
    "st_semifinal",    # 準決勝(決勝進出がかかる)
    "st_heat",         # 予選(勝ち上がりがかかる)
    "st_special",      # 特選(上位選手だが勝ち上がりは無いことが多い)
    "st_general",      # 一般・選抜(いわゆる敗者戦。かかるものが少ない)
    "st_has_advance",  # 勝ち上がり条件が設定されているか
    "st_grade_race",   # G1/G2/G3などのグレードレースか
]


def classify_stage(stage: str) -> str:
    """段階をまとめる。表記ゆれ(チ予選=チャレンジ予選 など)を吸収する。"""
    s = str(stage or "")
    if "決勝" in s:
        return "決勝"
    if "準決" in s:
        return "準決勝"
    if "予選" in s:
        return "予選"
    if "特選" in s:
        return "特選"
    if "選抜" in s:
        return "選抜"
    if "一般" in s:
        return "一般"
    return "その他"


def stage_features(stage: str, advance: str, grade: int) -> dict[str, float]:
    g = classify_stage(stage)
    return {
        "st_final": 1.0 if g == "決勝" else 0.0,
        "st_semifinal": 1.0 if g == "準決勝" else 0.0,
        "st_heat": 1.0 if g == "予選" else 0.0,
        "st_special": 1.0 if g == "特選" else 0.0,
        "st_general": 1.0 if g in ("一般", "選抜") else 0.0,
        "st_has_advance": 1.0 if str(advance or "").strip() else 0.0,
        "st_grade_race": 1.0 if grade else 0.0,
    }


def load_rows(db: str) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    races = {
        r["race_key"]: r
        for r in conn.execute(
            """
            select race_key, race_date, result_json, payouts_json,
                   coalesce(race_stage,'') as race_stage,
                   coalesce(advancement_text,'') as advancement_text,
                   coalesce(is_grade_race,0) as is_grade_race,
                   coalesce(race_class_official,'') as cls
            from races
            """
        ).fetchall()
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
        feats = _json(e["features_json"], {}) or {}
        feats.update(stage_features(race["race_stage"], race["advancement_text"], race["is_grade_race"]))
        rows.append(
            {
                "race_key": e["race_key"],
                "car_no": int(e["car_no"] or 0),
                "features": feats,
                "label": int(e["is_win"] or 0),
                "finish": int(e["finish_position"] or 99),
                "date": date,
                "stage": classify_stage(race["race_stage"]),
                "has_stage": bool(str(race["race_stage"]).strip()),
                "cls": race["cls"],
                "payouts": race["payouts_json"],
                "result": race["result_json"],
            }
        )
    return rows


def run(rows, names, cuts, seed, subset=None):
    accs, hits, rois = [], [], []
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
        rois.append(res["roi"])
    if not accs:
        return None
    return sum(accs) / len(accs), sum(hits) / len(hits), sum(rois) / len(rois)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "keirin_learning.sqlite3"))
    args = ap.parse_args()

    rows = load_rows(args.db)
    staged = [r for r in rows if r["has_stage"]]
    dates = sorted({r["date"] for r in rows})
    cuts = [dates[int(len(dates) * f)] for f in (0.62, 0.70, 0.78, 0.85)]
    base = [f for f in FEATURE_NAMES if f not in MARK_FEATURE_NAMES] + LINE_FEATURE_NAMES

    print(f"教師: {len({r['race_key'] for r in rows}):,}レース / {len(rows):,}行")
    print(f"段階が判明: {len({r['race_key'] for r in staged}):,}レース\n")

    if not staged:
        print("段階の取込がまだ終わっていません。")
        return

    print("=== 段階ごとのレース数と、1着の堅さ ===")
    by_stage: dict[str, list] = {}
    for r in staged:
        by_stage.setdefault(r["stage"], []).append(r)
    print(f"{'段階':<10}{'レース':>8}{'1番人気的中の目安':>18}")
    print("-" * 40)
    for st, v in sorted(by_stage.items(), key=lambda x: -len(x[1])):
        races = len({r['race_key'] for r in v})
        # モデルではなく「実際の1着がどれだけ上位得点に集中するか」で堅さを見る
        wins = [r for r in v if r["label"] == 1]
        zs = [r["features"].get("ln_score_z", 0.0) for r in wins]
        avg = sum(zs) / len(zs) if zs else 0.0
        print(f"{st:<10}{races:>8}{avg:>17.2f}")
    print("(数値=勝った選手の competitive score のレース内z。高いほど強い選手が順当に勝つ=堅い)")

    print(f"\n=== 段階を特徴量に足す効果 ===")
    print(f"{'条件':<20}{'Top1':>8}{'2車単的中':>10}{'回収率':>9}")
    print("-" * 48)
    res = {}
    for label, ns in [("現行", base), ("+ 段階", base + MOTIVATION_FEATURES)]:
        vals = [run(staged, ns, cuts, s) for s in (1, 7, 42, 123, 2026)]
        vals = [v for v in vals if v]
        res[label] = vals
        print(
            f"{label:<20}{sum(v[0] for v in vals)/len(vals)*100:>7.1f}%"
            f"{sum(v[1] for v in vals)/len(vals)*100:>9.1f}%{sum(v[2] for v in vals)/len(vals)*100:>8.1f}%"
        )
    a, b = res["現行"], res["+ 段階"]
    print(
        f"  上回った回数  的中率 {sum(1 for x, y in zip(b, a) if x[1] > y[1])}/{len(a)}"
        f" / Top1 {sum(1 for x, y in zip(b, a) if x[0] > y[0])}/{len(a)}"
    )

    print(f"\n=== 段階ごとの「当たりやすさ」(買うレースの選別に使える) ===")
    print(f"{'段階':<10}{'Top1':>8}{'2車単的中':>10}{'回収率':>9}")
    print("-" * 40)
    for st in sorted(by_stage, key=lambda s: -len(by_stage[s])):
        if len({r["race_key"] for r in by_stage[st]}) < 60:
            continue
        vals = [run(staged, base, cuts, s, subset=lambda r, st=st: r["stage"] == st) for s in (1, 42, 2026)]
        vals = [v for v in vals if v]
        if not vals:
            continue
        print(
            f"{st:<10}{sum(v[0] for v in vals)/len(vals)*100:>7.1f}%"
            f"{sum(v[1] for v in vals)/len(vals)*100:>9.1f}%{sum(v[2] for v in vals)/len(vals)*100:>8.1f}%"
        )
    print("\n※ 回収率は高配当に振られるため参考値。判断は2車単的中率とTop1で行う。")


if __name__ == "__main__":
    main()
