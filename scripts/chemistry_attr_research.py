"""ペア個別が測れない以上、「どういう組み合わせか」という属性で測る。

個別ペアは84%が生涯1回しか組んでおらず(最多6回)、選手単位も中央値8走・
最多20走しかない。個人やペアそのものを学習するのは不可能。
一方で「同県ペア」「年齢が離れたペア」のような属性なら数千件に集約できる。

各属性について、ライン内の隣接ペアの3着内率を、2人の相対的な強さで
補正したうえで比較する。差が出なければ効果なしと記録する。
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.storage import connect

CLASS_RANK = {"S": 3, "A": 2, "B": 1}


def _json_or(raw, fallback):
    try:
        return json.loads(raw) if raw else fallback
    except Exception:
        return fallback


def _class_rank(text: str) -> int:
    t = (text or "").strip().upper()
    for key, val in CLASS_RANK.items():
        if t.startswith(key):
            return val
    return 0


def load_pairs(conn):
    rows = conn.execute(
        """
        select e.race_key, e.car_no, e.player_id, e.prefecture, e.class, e.age, e.term,
               e.racing_score, e.style, e.finish_position, r.lineup_json
        from entries e join races r on r.race_key = e.race_key
        where e.finish_position is not null and r.lineup_json is not null
        """
    ).fetchall()
    by_race = defaultdict(list)
    for row in rows:
        by_race[row["race_key"]].append(dict(row))

    pairs = []
    for key, members in by_race.items():
        lineup = _json_or(members[0]["lineup_json"], [])
        line_of, pos_of = {}, {}
        for li, line in enumerate(lineup):
            if not isinstance(line, list):
                continue
            for pos, car in enumerate(line):
                line_of[int(car)] = li
                pos_of[int(car)] = pos
        if not line_of:
            continue
        scores = [float(m["racing_score"] or 0) for m in members]
        mean = sum(scores) / len(scores) if scores else 0.0
        by_car = {int(m["car_no"]): m for m in members}

        for car, li in line_of.items():
            pos = pos_of[car]
            nxt = [c for c, l in line_of.items() if l == li and pos_of[c] == pos + 1]
            if not nxt:
                continue
            a, b = by_car.get(car), by_car.get(nxt[0])
            if not a or not b:
                continue
            pairs.append(
                {
                    "hit": 1 if (a["finish_position"] <= 3 or b["finish_position"] <= 3) else 0,
                    "strength": (float(a["racing_score"] or 0) - mean) + (float(b["racing_score"] or 0) - mean),
                    "same_pref": 1 if (a["prefecture"] and a["prefecture"] == b["prefecture"]) else 0,
                    "age_gap": abs(int(a["age"] or 0) - int(b["age"] or 0)) if a["age"] and b["age"] else None,
                    "term_gap": abs(int(a["term"] or 0) - int(b["term"] or 0)) if a["term"] and b["term"] else None,
                    "class_gap": abs(_class_rank(a["class"]) - _class_rank(b["class"])),
                    "styles": f"{(a['style'] or '?')[:2]}+{(b['style'] or '?')[:2]}",
                    "bante_older": 1 if (a["age"] and b["age"] and int(b["age"]) > int(a["age"])) else 0,
                }
            )
    return pairs


def report(pairs, label, bucket_fn):
    """属性別に、強さで補正した3着内率を出す。"""
    flat = [(p["hit"], p["strength"]) for p in pairs]
    base = sum(h for h, _ in flat) / len(flat)
    ms = sum(s for _, s in flat) / len(flat)
    var = sum((s - ms) ** 2 for _, s in flat) or 1.0
    beta = sum((h - base) * (s - ms) for h, s in flat) / var

    groups = defaultdict(list)
    for p in pairs:
        key = bucket_fn(p)
        if key is None:
            continue
        expected = base + beta * (p["strength"] - ms)
        groups[key].append((p["hit"], expected))

    print(f"\n--- {label} ---")
    for key in sorted(groups, key=lambda k: (isinstance(k, str), k)):
        vals = groups[key]
        if len(vals) < 80:
            continue
        actual = sum(h for h, _ in vals) / len(vals)
        exp = sum(e for _, e in vals) / len(vals)
        diff = (actual - exp) * 100
        # 差の目安誤差(二項)
        se = (actual * (1 - actual) / len(vals)) ** 0.5 * 100
        flag = "★" if abs(diff) > 2 * se else ""
        print(f"  {str(key):<12} n={len(vals):5d}  実測{actual*100:5.1f}%  強さ期待{exp*100:5.1f}%  差{diff:+5.1f}pt (±{2*se:.1f}) {flag}")


def main():
    with connect() as conn:
        pairs = load_pairs(conn)
    print(f"ライン内の隣接ペア: {len(pairs)}件  (★=強さで説明できない差)")

    report(pairs, "同県ペアか", lambda p: "同県" if p["same_pref"] else "他県")
    report(pairs, "年齢差", lambda p: None if p["age_gap"] is None else ("0-2歳" if p["age_gap"] <= 2 else "3-6歳" if p["age_gap"] <= 6 else "7-12歳" if p["age_gap"] <= 12 else "13歳以上"))
    report(pairs, "期別差(世代差)", lambda p: None if p["term_gap"] is None else ("0-4期" if p["term_gap"] <= 4 else "5-14期" if p["term_gap"] <= 14 else "15-29期" if p["term_gap"] <= 29 else "30期以上"))
    report(pairs, "級班差", lambda p: f"{p['class_gap']}段階")
    report(pairs, "番手が年上か", lambda p: "番手が年上" if p["bante_older"] else "番手が年下")
    report(pairs, "脚質の組み合わせ", lambda p: p["styles"])


if __name__ == "__main__":
    main()
