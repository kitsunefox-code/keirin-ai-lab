from __future__ import annotations

"""選手コメントから「今回の走り方の申告」を読み取って特徴量にする。

競輪の前検日コメントは定型で、内容がそのまま作戦の申告になっている:
  自力型  「自力です。」「先行します。」  → 主導権を取りにいく宣言
  追込型  「三神君。」「山田さん。」      → マークする相手の名指し

実測(4,254選手): 32.8%が自力を宣言、31.7%が誰かを名指し。
名指しのうち99.1%は同じレースの出走選手に対応した。

ここから作れるもの:
- 選手自身が申告したライン構造(主催者発表の並び予想とは独立した一次情報)
- 「何人から名指しされたか」= 出走者たちの総意による『主導権を取る人』
- 申告と発表並びの食い違い(食い違うレースは荒れやすい可能性)

既存モデルはこのコメントを感情スコア(強気/中立)に潰しており、
肝心の中身を捨てていた。

python scripts/comment_intent_research.py --db <path>
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

import numpy as np  # noqa: E402
import lightgbm as lgb  # noqa: E402

from keirin_ai.features import FEATURE_NAMES, LINE_FEATURE_NAMES, MARK_FEATURE_NAMES  # noqa: E402
from scripts.ablation_study import roi_ci  # noqa: E402
from scripts.class_model_research import _groups, PARAMS  # noqa: E402
from scripts.line_features_research import _json, _norm_date  # noqa: E402
from scripts.ranking_research import evaluate, relevance  # noqa: E402

INTENT_FEATURES = [
    "cm_jiriki",           # 自力・先行を宣言している
    "cm_marks",            # 誰かを名指ししている(追走宣言)
    "cm_marked_by",        # 何人から名指しされたか(出走者総意の「主導権を取る人」)
    "cm_marked_by_top",    # 名指しされた数がレース内最多か
    "cm_target_marked_by", # 自分が指した相手が何人から名指しされているか
    "cm_agrees_lineup",    # 申告した相手が発表並びの直前の選手と一致するか
    "cm_lineup_conflict",  # レース全体で申告と発表並びが食い違った人数の割合
    "cm_chain_pos",        # 申告から復元したライン内での位置
]

_NAME = re.compile(r"([一-龥ぁ-んァ-ヶ]{2,4})(君|さん|ちゃん)")
_JIRIKI = re.compile(r"自力|先行|突っ張|前々|逃げ|駆け")


def parse_intents(entrants: list[dict]) -> dict[int, dict]:
    """コメントから申告を読み取る。entrants = [{car_no, name, comment}, ...]"""
    names = {}
    for e in entrants:
        nm = str(e.get("name") or "").replace(" ", "").replace("　", "")
        if nm:
            names[nm] = int(e.get("car_no") or 0)

    declared: dict[int, int | None] = {}
    jiriki: dict[int, bool] = {}
    for e in entrants:
        car = int(e.get("car_no") or 0)
        cm = str(e.get("comment") or "")
        jiriki[car] = bool(_JIRIKI.search(cm))
        target = None
        for surname, _h in _NAME.findall(cm):
            for nm, c in names.items():
                if nm.startswith(surname) and c != car:
                    target = c
                    break
            if target:
                break
        declared[car] = target

    marked_by: dict[int, int] = {c: 0 for c in declared}
    for car, target in declared.items():
        if target in marked_by:
            marked_by[target] += 1
    best = max(marked_by.values()) if marked_by else 0

    return {
        "declared": declared,
        "jiriki": jiriki,
        "marked_by": marked_by,
        "best_marked": best,
    }


def build_intent_features(entrants: list[dict], lineup: list[list[int]]) -> dict[int, dict]:
    info = parse_intents(entrants)
    declared, jiriki, marked_by = info["declared"], info["jiriki"], info["marked_by"]
    best = info["best_marked"]

    # 発表並びでの「直前の選手」
    ahead: dict[int, int | None] = {}
    for line in lineup or []:
        for i, car in enumerate(line):
            ahead[int(car)] = int(line[i - 1]) if i > 0 else None

    conflicts = 0
    checked = 0
    for car, target in declared.items():
        if target is None or car not in ahead:
            continue
        checked += 1
        if ahead.get(car) != target:
            conflicts += 1
    conflict_ratio = conflicts / checked if checked else 0.0

    # 申告だけでラインを復元し、鎖の何番目かを出す
    chain_pos: dict[int, int] = {}
    for car in declared:
        pos = 0
        cur = car
        seen = {car}
        while declared.get(cur) is not None and declared[cur] not in seen:
            cur = declared[cur]
            seen.add(cur)
            pos += 1
            if pos > 5:
                break
        chain_pos[car] = pos

    out = {}
    for car in declared:
        target = declared[car]
        out[car] = {
            "cm_jiriki": 1.0 if jiriki.get(car) else 0.0,
            "cm_marks": 1.0 if target else 0.0,
            "cm_marked_by": min(marked_by.get(car, 0), 3) / 3.0,
            "cm_marked_by_top": 1.0 if (best > 0 and marked_by.get(car, 0) == best) else 0.0,
            "cm_target_marked_by": min(marked_by.get(target, 0), 3) / 3.0 if target else 0.0,
            "cm_agrees_lineup": 1.0 if (target is not None and ahead.get(car) == target) else 0.0,
            "cm_lineup_conflict": conflict_ratio,
            "cm_chain_pos": min(chain_pos.get(car, 0), 3) / 3.0,
        }
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
    # コメントは predictions.ranking_json にしか無い
    comments: dict[str, list[dict]] = {}
    for r in conn.execute("select race_key, ranking_json from predictions"):
        ranking = _json(r["ranking_json"], []) or []
        rows = [
            {"car_no": int(e.get("car_no") or 0), "name": e.get("name") or "", "comment": e.get("comment") or ""}
            for e in ranking
            if e.get("car_no")
        ]
        if rows:
            comments.setdefault(r["race_key"], rows)

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
        entrants = comments.get(key) or []
        lineup = _json(race["lineup_json"], []) or []
        intents = build_intent_features(entrants, lineup) if entrants else {}
        for m in members:
            car = int(m["car_no"] or 0)
            feats = _json(m["features_json"], {}) or {}
            feats.update(intents.get(car, {name: 0.0 for name in INTENT_FEATURES}))
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

    with_intent = sum(1 for r in rows if r["features"].get("cm_marks") or r["features"].get("cm_jiriki"))
    print(f"教師: {len({r['race_key'] for r in rows}):,}レース / {len(rows):,}行")
    print(f"申告を読み取れた行: {with_intent:,} ({with_intent/max(1,len(rows))*100:.1f}%)\n")

    # 申告と着順の関係を先に見る
    print("=== 何人から名指しされたか vs 1着率 ===")
    b: dict[int, list] = {}
    for r in rows:
        k = int(round(r["features"].get("cm_marked_by", 0.0) * 3))
        b.setdefault(k, []).append(r["label"])
    for k in sorted(b):
        v = b[k]
        print(f"  {k}人から名指し: {len(v):>6}件  1着率 {sum(v)/len(v)*100:>5.1f}%")

    print("\n=== 自力宣言 vs 1着率 ===")
    for flag in (0, 1):
        v = [r["label"] for r in rows if int(r["features"].get("cm_jiriki", 0)) == flag]
        if v:
            print(f"  {'自力宣言あり' if flag else '宣言なし':<12}: {len(v):>6}件  1着率 {sum(v)/len(v)*100:>5.1f}%")

    print(f"\n{'条件':<20}{'Top1':>8}{'2車単的中':>10}{'回収率':>9}{'95%信頼区間':>20}")
    print("-" * 68)
    summary = {}
    for label, ns in [("現行", base), ("+ 申告の読み取り", base + INTENT_FEATURES)]:
        A = H = R = 0.0
        T: list[int] = []
        seeds = (1, 7, 42, 123, 2026)
        vals = []
        for seed in seeds:
            a, h, r, t = run(rows, ns, cuts, seed)
            vals.append((a, h, r))
            T.extend(t)
        A = sum(v[0] for v in vals) / len(vals)
        H = sum(v[1] for v in vals) / len(vals)
        R = sum(v[2] for v in vals) / len(vals)
        summary[label] = vals
        lo, hi = roi_ci(T)
        print(f"{label:<20}{A*100:>7.1f}%{H*100:>9.1f}%{R*100:>8.1f}%{f'{lo*100:.1f}〜{hi*100:.1f}%':>20}")

    a, b2 = summary["現行"], summary["+ 申告の読み取り"]
    print(
        f"\n上回った回数  回収率 {sum(1 for x, y in zip(b2, a) if x[2] > y[2])}/{len(a)}"
        f" / 2車単的中 {sum(1 for x, y in zip(b2, a) if x[1] > y[1])}/{len(a)}"
        f" / Top1 {sum(1 for x, y in zip(b2, a) if x[0] > y[0])}/{len(a)}"
    )


if __name__ == "__main__":
    main()
