from __future__ import annotations

"""本日の出走選手のJKA公式プロフィール(今期得点・直近4ヶ月成績・級班履歴)を取り込む。

毎朝の自動更新から呼ばれる。7日以内に取得済みの選手はスキップ(公式サイトに優しく)。

python scripts\\backfill_player_profiles.py --limit 250 --delay 0.5
"""

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.jka import fetch_player_profile
from keirin_ai.storage import connect, ensure_player_profile_table, save_player_profile

JST = timezone(timedelta(hours=9))


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch official JKA player profiles for today's entrants.")
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--max-age-days", type=int, default=7)
    args = parser.parse_args()

    today = datetime.now(JST)
    stale_before = (today - timedelta(days=args.max_age_days)).isoformat(timespec="seconds")

    saved = skipped = failed = 0
    with connect() as conn:
        ensure_player_profile_table(conn)
        # 今日の出走選手を最優先(race_dateは「2026年7月11日」形式)。枠が余れば直近の出走選手も。
        date_pattern = f"%{today.year}年{today.month}月{today.day}日%"
        today_ids = [
            row["player_id"]
            for row in conn.execute(
                """
                select distinct e.player_id
                from entries e
                join races r on r.race_key = e.race_key
                where e.player_id is not null and e.player_id != ''
                  and r.race_date like ?
                """,
                (date_pattern,),
            ).fetchall()
        ]
        recent_ids = [
            row["player_id"]
            for row in conn.execute(
                """
                select distinct e.player_id
                from entries e
                join races r on r.race_key = e.race_key
                where e.player_id is not null and e.player_id != ''
                  and r.fetched_at >= datetime('now', '-2 days')
                """
            ).fetchall()
        ]
        ordered = today_ids + [pid for pid in recent_ids if pid not in set(today_ids)]
        fresh = {
            row["player_id"]
            for row in conn.execute(
                "select player_id from player_profiles where fetched_at >= ?", (stale_before,)
            ).fetchall()
        }
        targets = [pid for pid in ordered if pid not in fresh][: args.limit]

        for idx, pid in enumerate(targets, start=1):
            try:
                profile = fetch_player_profile(pid)
                if profile:
                    save_player_profile(conn, pid, profile, today.isoformat(timespec="seconds"))
                    saved += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
            if idx % 25 == 0 or idx == len(targets):
                print(f"{idx}/{len(targets)} profiles saved={saved} failed={failed}", flush=True)
            if idx < len(targets):
                time.sleep(max(0.3, args.delay))
        skipped = len(ordered) - len(targets)

    print(json.dumps({"targets": len(targets), "saved": saved, "skipped_fresh": skipped, "failed": failed}, ensure_ascii=False))


if __name__ == "__main__":
    main()
