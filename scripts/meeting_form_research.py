from __future__ import annotations

"""「今この開催での調子」を特徴量にする。

競輪は3〜4日間の開催で同じ面子が繰り返し走る。実測では
開催×選手の83.2%が同一開催で2走以上している。

既存の成績特徴は直近4ヶ月の平均で、いわば「体温」ではなく「平熱」。
一方、同じ開催の初日・2日目にどう走ったかは、
  ・同じバンク(周長・カント・風)
  ・同じ時期のコンディション
  ・一部は同じ相手
という条件を揃えた最新の観測で、はるかに鋭いはず。
既存の予想AIが静的な期別成績に頼っているのに対し、ここは差がつきうる。

リーク対策: 使うのは「同じ開催で、今より前の日に走ったレース」だけ。
race_key = winticket:<開催ID>:<日>:<R> の構造を使って厳密に前だけを見る。

python scripts/meeting_form_research.py --db <path>
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

MEETING_FEATURES = [
    "mt_has",          # この開催で既に走ったか
    "mt_races",        # 何走したか
    "mt_avg_finish",   # 平均着順(1着=1.0 〜 9着=0.0 に変換)
    "mt_best_finish",  # 最高着順
    "mt_top3_rate",    # 3着内率
    "mt_won",          # 勝ったことがあるか
    "mt_last_finish",  # 直近1走の着順
    "mt_avg_finish_z", # 平均着順のレース内相対(誰がこの開催で走れているか)
]


def _parse_key(race_key: str):
    """winticket:<開催ID>:<日>:<R> を (開催ID, 日, R) へ。"""
    parts = str(race_key).split(":")
    if len(parts) < 4:
        return None
    try:
        return parts[1], int(parts[2]), int(parts[3])
    except (TypeError, ValueError):
        return None


def _norm_finish(finish: int, field: int = 9) -> float:
    """着順を 1着=1.0, 最下位=0.0 の尺度へ。"""
    return max(0.0, min(1.0, (field - finish) / max(1, field - 1)))


def load_rows(db: str) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    races = {
        r["race_key"]: r
        for r in conn.execute("select race_key, race_date, result_json, payouts_json from races").fetchall()
    }
    ents = conn.execute(
        """
        select race_key, car_no, player_id, features_json, finish_position, is_win
        from entries where finish_position is not null and features_json is not null
        """
    ).fetchall()
    conn.close()

    # 開催ごと・選手ごとに「いつ何着だったか」を並べる
    history: dict[tuple[str, str], list[tuple[int, int, int]]] = {}
    for e in ents:
        parsed = _parse_key(e["race_key"])
        pid = str(e["player_id"] or "")
        if not parsed or not pid:
            continue
        cup, day, rno = parsed
        history.setdefault((cup, pid), []).append((day, rno, int(e["finish_position"] or 99)))
    for v in history.values():
        v.sort()

    rows = []
    by_race: dict[str, list] = {}
    for e in ents:
        by_race.setdefault(e["race_key"], []).append(e)

    for key, members in by_race.items():
        race = races.get(key)
        parsed = _parse_key(key)
        if not race or not parsed:
            continue
        date = _norm_date(race["race_date"])
        if not date:
            continue
        cup, day, rno = parsed

        # まず各選手の「この開催での過去成績」を出す
        stats: dict[int, dict] = {}
        for m in members:
            pid = str(m["player_id"] or "")
            past = [
                f for (d, r, f) in history.get((cup, pid), []) if (d, r) < (day, rno)
            ]
            car = int(m["car_no"] or 0)
            if not past:
                stats[car] = {
                    "mt_has": 0.0, "mt_races": 0.0, "mt_avg_finish": 0.5, "mt_best_finish": 0.5,
                    "mt_top3_rate": 0.0, "mt_won": 0.0, "mt_last_finish": 0.5,
                }
            else:
                norm = [_norm_finish(f) for f in past]
                stats[car] = {
                    "mt_has": 1.0,
                    "mt_races": min(len(past), 4) / 4.0,
                    "mt_avg_finish": sum(norm) / len(norm),
                    "mt_best_finish": max(norm),
                    "mt_top3_rate": sum(1 for f in past if f <= 3) / len(past),
                    "mt_won": 1.0 if any(f == 1 for f in past) else 0.0,
                    "mt_last_finish": norm[-1],
                }
        # レース内で相対化する(この開催で走れているのは誰か)
        avgs = [s["mt_avg_finish"] for s in stats.values()]
        mean = sum(avgs) / len(avgs) if avgs else 0.5
        sd = (sum((a - mean) ** 2 for a in avgs) / len(avgs)) ** 0.5 if avgs else 0.0
        for car, s in stats.items():
            s["mt_avg_finish_z"] = ((s["mt_avg_finish"] - mean) / sd) if sd > 0 else 0.0

        for m in members:
            car = int(m["car_no"] or 0)
            feats = _json(m["features_json"], {}) or {}
            feats.update(stats.get(car, {n: 0.0 for n in MEETING_FEATURES}))
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

    has = sum(1 for r in rows if r["features"].get("mt_has"))
    print(f"教師: {len({r['race_key'] for r in rows}):,}レース / {len(rows):,}行")
    print(f"開催内の過去成績あり: {has:,} ({has/max(1,len(rows))*100:.1f}%)\n")

    print("=== この開催での平均着順 vs 今回の1着率 ===")
    b: dict[str, list] = {}
    for r in rows:
        if not r["features"].get("mt_has"):
            b.setdefault("(初日など実績なし)", []).append(r["label"])
            continue
        a = r["features"]["mt_avg_finish"]
        k = "0.0-0.3(下位)" if a < 0.3 else "0.3-0.5" if a < 0.5 else "0.5-0.7" if a < 0.7 else "0.7-1.0(上位)"
        b.setdefault(k, []).append(r["label"])
    for k in sorted(b):
        v = b[k]
        print(f"  {k:<18}{len(v):>7}件  1着率 {sum(v)/len(v)*100:>5.1f}%")

    print(f"\n{'条件':<22}{'Top1':>8}{'2車単的中':>10}{'回収率':>9}{'95%信頼区間':>20}")
    print("-" * 70)
    summary = {}
    for label, ns in [("現行", base), ("+ 開催内の調子", base + MEETING_FEATURES)]:
        vals = []
        T: list[int] = []
        for seed in (1, 7, 42, 123, 2026):
            a, h, r, t = run(rows, ns, cuts, seed)
            vals.append((a, h, r))
            T.extend(t)
        summary[label] = vals
        lo, hi = roi_ci(T)
        print(
            f"{label:<22}{sum(v[0] for v in vals)/len(vals)*100:>7.1f}%"
            f"{sum(v[1] for v in vals)/len(vals)*100:>9.1f}%"
            f"{sum(v[2] for v in vals)/len(vals)*100:>8.1f}%{f'{lo*100:.1f}〜{hi*100:.1f}%':>20}"
        )

    a, b2 = summary["現行"], summary["+ 開催内の調子"]
    print(
        f"\n上回った回数  回収率 {sum(1 for x, y in zip(b2, a) if x[2] > y[2])}/{len(a)}"
        f" / 2車単的中 {sum(1 for x, y in zip(b2, a) if x[1] > y[1])}/{len(a)}"
        f" / Top1 {sum(1 for x, y in zip(b2, a) if x[0] > y[0])}/{len(a)}"
    )


if __name__ == "__main__":
    main()
