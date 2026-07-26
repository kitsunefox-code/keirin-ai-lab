from __future__ import annotations

"""値付け研究に使えるデータがどれだけ貯まったかを日次で見る。

エッジ(市場に対する優位)を統計的に主張するには、当たりの本数が要る。
的中率3%・オッズ30倍前後の買い方だと、数千点の試行がないと
「勝てた」のか「たまたま当たった」のか区別できない。
このスクリプトは、その進捗を毎日確認するための道具。
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    from keirin_ai.storage import connect, ensure_odds_snapshot_table

    with connect() as conn:
        conn.row_factory = sqlite3.Row
        ensure_odds_snapshot_table(conn)

        total_races = conn.execute("select count(*) from races").fetchone()[0]
        covered = conn.execute("select count(distinct race_key) from odds_snapshots").fetchone()[0]
        snaps = conn.execute("select count(*) from odds_snapshots").fetchone()[0]
        near = conn.execute(
            "select count(distinct race_key) from odds_snapshots where minutes_to_post is not null and minutes_to_post <= 15"
        ).fetchone()[0]
        usable = conn.execute(
            """
            select count(distinct o.race_key)
            from odds_snapshots o
            join races r on r.race_key = o.race_key
            where r.payouts_json is not null and r.result_json is not null
            """
        ).fetchone()[0]

        print("=== オッズ盤の蓄積状況 ===")
        print(f"レース総数            : {total_races:,}")
        print(f"オッズ盤あり          : {covered:,} ({covered/max(1,total_races)*100:.1f}%)")
        print(f"スナップショット総枚数: {snaps:,} (1レース平均 {snaps/max(1,covered):.1f}枚)")
        print(f"締切15分以内の盤あり  : {near:,}")
        print(f"研究に使える(結果+払戻揃い): {usable:,}")

        rows = conn.execute(
            """
            select r.race_date as d,
                   count(distinct o.race_key) as races,
                   count(*) as snaps
            from odds_snapshots o
            join races r on r.race_key = o.race_key
            group by r.race_date
            order by r.race_date desc
            limit 10
            """
        ).fetchall()
        if rows:
            print("\n=== 直近の日別 ===")
            print(f"{'日付':<16}{'レース':>8}{'枚数':>8}")
            for row in rows:
                print(f"{row['d'] or '':<16}{row['races']:>8}{row['snaps']:>8}")

        # 統計的にエッジを主張するのに必要な規模の目安
        print("\n=== 判定に必要な規模の目安 ===")
        print("的中率3%・平均30倍の買い方で「回収率110%」を偶然と区別するには、")
        print("おおよそ 3,000〜5,000点(= 1,000〜1,700レース)の試行が必要。")
        remain = max(0, 1200 - usable)
        if remain:
            print(f"現在 {usable:,}レース。あと約 {remain:,}レース(1日80Rなら約 {remain/80:.0f}日)。")
        else:
            print(f"現在 {usable:,}レース。検証に着手できる規模に到達している。")


if __name__ == "__main__":
    main()
