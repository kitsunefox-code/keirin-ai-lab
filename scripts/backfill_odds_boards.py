from __future__ import annotations

"""過去レースの全通りオッズ盤をまとめて取り込む。

値付けの歪みを探す研究には全通りのオッズが要るが、
発走前スナップショットは29.6%しか貯まっておらず、
「あと数週間待ち」の状態だった。

ところがWINTICKETは過去レースのオッズ盤をそのまま残している
(2ヶ月前のレースでも2車単42点が完全に取得できることを確認)。
待たずに過去分を埋められる。

重要な但し書き:
ここで取れるのは**確定後のオッズ**であって、発走前に見えていた
オッズではない。日本の公営競技はパリミュチュエル方式なので
払戻は確定オッズで行われ、精算の値としては正しい。
ただし「買う時点でこの値は分からない」ため、
確定オッズで買い目を選ぶ検証は現実より有利になる。
研究の第一歩としては使えるが、実運用の判断には
発走前スナップショット(snapshot_odds.py)の蓄積が別途必要。

区別のため odds_snapshots.minutes_to_post に -1 を入れて
「確定後に取得したもの」と分かるようにする。

python scripts/backfill_odds_boards.py --limit 50 --delay 0.5
python scripts/backfill_odds_boards.py --limit 3000 --delay 0.4   # 全部
"""

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.odds import odds_url_from_racecard
from keirin_ai.sources import fetch_url
from keirin_ai.storage import connect, ensure_odds_snapshot_table
from keirin_ai.winticket_state import state_queries

JST = timezone(timedelta(hours=9))
# 確定後に取得したオッズであることを示す目印
POST_RACE_MARKER = -1


def _normalize(odds_list) -> list[dict]:
    rows = []
    for row in odds_list or []:
        key = row.get("key")
        odds = row.get("odds")
        if isinstance(key, list) and len(key) == 2 and odds:
            try:
                rows.append(
                    {
                        "key": f"{int(key[0])}-{int(key[1])}",
                        "odds": float(odds),
                        "pop": int(row.get("popularityOrder") or 0),
                    }
                )
            except (TypeError, ValueError):
                continue
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill full odds boards for past races.")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--delay", type=float, default=0.5)
    args = ap.parse_args()

    saved = skipped = failed = 0
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        ensure_odds_snapshot_table(conn)
        # 結果が確定していて、まだ盤を持っていないレースを古い順に
        targets = conn.execute(
            """
            select r.race_key, r.source_url, r.race_date
            from races r
            where r.result_json is not null
              and coalesce(r.source_url, '') <> ''
              and r.race_key not in (select race_key from odds_snapshots)
            order by r.race_date
            limit ?
            """,
            (args.limit,),
        ).fetchall()

        for idx, race in enumerate(targets, start=1):
            try:
                html = fetch_url(odds_url_from_racecard(race["source_url"]))
                odds = state_queries(html).get("FETCH_KEIRIN_RACE_ODDS", {})
                exacta = _normalize(odds.get("exacta"))
                if not exacta:
                    skipped += 1
                else:
                    snapshot = {
                        "exacta": exacta,
                        "taken_at": datetime.now(JST).isoformat(timespec="seconds"),
                        "source": "post-race-backfill",
                    }
                    conn.execute(
                        """
                        insert or ignore into odds_snapshots (race_key, taken_at, minutes_to_post, exacta_json)
                        values (?, ?, ?, ?)
                        """,
                        (
                            race["race_key"],
                            snapshot["taken_at"],
                            POST_RACE_MARKER,
                            json.dumps(exacta, ensure_ascii=False),
                        ),
                    )
                    # 妙味ボードが読む最新オッズは、事前スナップショットが
                    # 既にある場合は上書きしない(事前の値の方が現実的なため)
                    conn.execute(
                        "update races set latest_odds_json=? where race_key=? and latest_odds_json is null",
                        (json.dumps(snapshot, ensure_ascii=False), race["race_key"]),
                    )
                    saved += 1
                    if saved % 50 == 0:
                        conn.commit()
            except Exception:
                failed += 1
            if idx < len(targets):
                time.sleep(max(0.2, args.delay))
        conn.commit()
        total = conn.execute("select count(distinct race_key) from odds_snapshots").fetchone()[0]

    print(
        json.dumps(
            {"targets": len(targets), "saved": saved, "no_odds": skipped, "failed": failed, "races_covered": total},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
