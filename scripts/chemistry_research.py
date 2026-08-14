"""「この人とは連携が悪い」「この選手は番手が上手い」を測れるか調べる。

既にある partner_top3_rate は「同ラインを組んだ2人のどちらかが3着内に入った割合」。
これはラインの強さをほぼそのまま測っているだけで、連携の良し悪しとは別物の
可能性が高い。強い2人が組めば連携が悪くても数字は上がる。

ここで確かめること:
  1) ペアの共走回数はそもそも足りているか(足りなければ特徴量として死んでいる)
  2) partner_top3_rate は個人の強さを差し引いても残る情報を持っているか
  3) 「番手が上手い」のような個人差は、ライン内の位置別成績で測れるか

出力は数字だけ。効果なしならそう書く。
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


def _json_or(raw, fallback):
    try:
        return json.loads(raw) if raw else fallback
    except Exception:
        return fallback


def load(conn):
    """結果とラインが揃ったレースを、選手単位の行にして返す。"""
    rows = conn.execute(
        """
        select e.race_key, e.car_no, e.player_id, e.name, e.racing_score,
               e.finish_position, r.lineup_json, r.race_date
        from entries e join races r on r.race_key = e.race_key
        where e.finish_position is not null and e.player_id != ''
              and r.lineup_json is not null
        """
    ).fetchall()
    races = defaultdict(list)
    for row in rows:
        races[row["race_key"]].append(dict(row))

    out = []
    for key, members in races.items():
        lineup = _json_or(members[0]["lineup_json"], [])
        pos_of = {}
        line_of = {}
        for li, line in enumerate(lineup):
            if not isinstance(line, list):
                continue
            for pos, car in enumerate(line):
                pos_of[int(car)] = pos
                line_of[int(car)] = li
        if not pos_of:
            continue
        scores = [float(m["racing_score"] or 0) for m in members]
        mean = sum(scores) / len(scores) if scores else 0.0
        for m in members:
            car = int(m["car_no"])
            if car not in pos_of:
                continue
            out.append(
                {
                    "race_key": key,
                    "date": m["race_date"] or "",
                    "car": car,
                    "player_id": str(m["player_id"]),
                    "name": m["name"] or "",
                    "score": float(m["racing_score"] or 0),
                    "score_rel": float(m["racing_score"] or 0) - mean,
                    "finish": int(m["finish_position"]),
                    "pos": pos_of[car],
                    "line": line_of[car],
                    "line_size": sum(1 for c in pos_of if line_of[c] == line_of[car]),
                    "field": len(members),
                }
            )
    return out


def q1_pair_coverage(rows):
    """ペアの共走回数。2回以上組んだ相手がどれだけいるか。"""
    pair_races = defaultdict(set)
    for r in rows:
        pass
    by_race = defaultdict(list)
    for r in rows:
        by_race[r["race_key"]].append(r)
    for key, members in by_race.items():
        for a in members:
            for b in members:
                if a["player_id"] < b["player_id"] and a["line"] == b["line"]:
                    pair_races[(a["player_id"], b["player_id"])].add(key)

    counts = sorted((len(v) for v in pair_races.values()), reverse=True)
    total_pairings = sum(counts)
    print("=== 1) ペアの共走回数 ===")
    print(f"同ラインを組んだ延べ回数: {total_pairings}")
    print(f"異なるペア数: {len(counts)}")
    for n in (1, 2, 3, 5, 10):
        k = sum(1 for c in counts if c >= n)
        share = sum(c for c in counts if c >= n) / total_pairings * 100 if total_pairings else 0
        print(f"  {n}回以上組んだペア: {k:5d} ({k/len(counts)*100:4.1f}%) / 延べの{share:4.1f}%")
    print(f"最多共走: {counts[0] if counts else 0}回")
    return pair_races


def q2_pair_signal(rows, pair_races):
    """連携を「残差」で測る。

    2人の強さ(score_rel の和)から期待される3着内率と、実際の3着内率の差。
    強さで説明できる分を引いた残りが「連携」。ペアごとの残差が翌回にも
    続くなら本物、続かないならただのばらつき。
    """
    by_race = defaultdict(list)
    for r in rows:
        by_race[r["race_key"]].append(r)

    # ペアごとの出来事を時系列で並べる
    events = defaultdict(list)
    for key, members in by_race.items():
        date = members[0]["date"]
        for a in members:
            for b in members:
                if a["player_id"] < b["player_id"] and a["line"] == b["line"]:
                    hit = 1 if (a["finish"] <= 3 or b["finish"] <= 3) else 0
                    strength = a["score_rel"] + b["score_rel"]
                    events[(a["player_id"], b["player_id"])].append((date, hit, strength))

    flat = [(h, s) for evs in events.values() for _, h, s in evs]
    base = sum(h for h, _ in flat) / len(flat)
    # 強さ→3着内率の単回帰(ロジットではなく単純な線形で十分)
    ms = sum(s for _, s in flat) / len(flat)
    var = sum((s - ms) ** 2 for _, s in flat) or 1.0
    cov = sum((h - base) * (s - ms) for h, s in flat)
    beta = cov / var

    def expected(strength):
        return min(0.99, max(0.01, base + beta * (strength - ms)))

    print("\n=== 2) 連携は「強さを差し引いても」残るか ===")
    print(f"同ライン3着内率の全体: {base*100:.1f}%  (強さ1点あたり {beta*100:+.2f}pt)")

    # 前半の残差と後半の残差が一致するか(split-half)
    firsts, seconds = [], []
    for pair, evs in events.items():
        evs = sorted(evs)
        if len(evs) < 4:
            continue
        half = len(evs) // 2
        a = [h - expected(s) for _, h, s in evs[:half]]
        b = [h - expected(s) for _, h, s in evs[half:]]
        firsts.append(sum(a) / len(a))
        seconds.append(sum(b) / len(b))

    print(f"共走4回以上のペア: {len(firsts)}組")
    if len(firsts) >= 30:
        ma, mb = sum(firsts) / len(firsts), sum(seconds) / len(seconds)
        va = sum((x - ma) ** 2 for x in firsts) ** 0.5
        vb = sum((x - mb) ** 2 for x in seconds) ** 0.5
        r = sum((x - ma) * (y - mb) for x, y in zip(firsts, seconds)) / (va * vb or 1.0)
        print(f"前半の残差 vs 後半の残差 の相関: {r:+.3f}")
        print("  → 0付近なら「連携の良し悪し」は再現せず、ただのばらつき")
    else:
        print("  → 共走4回以上のペアが少なすぎて検証不能")


def q3_position_skill(rows):
    """個人差: ライン内の位置(先頭/番手/3番手)ごとの得手不得手。"""
    print("\n=== 3) 個人差: ライン内の位置別の巧拙 ===")
    # 位置ごとの全体3着内率(基準)
    base = defaultdict(lambda: [0, 0])
    for r in rows:
        if r["pos"] > 2:
            continue
        base[r["pos"]][0] += 1
        base[r["pos"]][1] += 1 if r["finish"] <= 3 else 0
    for pos in sorted(base):
        n, hit = base[pos]
        label = {0: "先頭", 1: "番手", 2: "3番手"}[pos]
        print(f"  {label}: {n:5d}走 3着内 {hit/n*100:4.1f}%")

    # 選手ごとに「番手のときの残差」が再現するか
    per = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["pos"] > 2:
            continue
        n, hit = base[r["pos"]]
        exp = hit / n
        # 強さの差も引く
        per[r["player_id"]][r["pos"]].append((r["date"], (1 if r["finish"] <= 3 else 0) - exp - 0.02 * r["score_rel"]))

    firsts, seconds, n_players = [], [], 0
    for pid, byp in per.items():
        evs = sorted(byp.get(1, []))
        if len(evs) < 10:
            continue
        n_players += 1
        half = len(evs) // 2
        firsts.append(sum(v for _, v in evs[:half]) / half)
        seconds.append(sum(v for _, v in evs[half:]) / (len(evs) - half))

    print(f"\n  番手を10回以上こなした選手: {n_players}人")
    if n_players >= 30:
        ma, mb = sum(firsts) / len(firsts), sum(seconds) / len(seconds)
        va = sum((x - ma) ** 2 for x in firsts) ** 0.5
        vb = sum((x - mb) ** 2 for x in seconds) ** 0.5
        r = sum((x - ma) * (y - mb) for x, y in zip(firsts, seconds)) / (va * vb or 1.0)
        print(f"  前半 vs 後半 の相関: {r:+.3f}")
        print("  → プラスなら「番手が上手い選手」は実在し、特徴量にできる")
    else:
        print("  → 検証不能")


def main():
    with connect() as conn:
        rows = load(conn)
    print(f"対象: {len({r['race_key'] for r in rows})}レース / {len(rows)}出走\n")
    pairs = q1_pair_coverage(rows)
    q2_pair_signal(rows, pairs)
    q3_position_skill(rows)


if __name__ == "__main__":
    main()
