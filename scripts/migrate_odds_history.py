from __future__ import annotations

"""既存の races.latest_odds_json を odds_snapshots 履歴へ取り込む(1回だけ実行)。

これまでは1レース1枚しか残っていなかったが、その1枚も研究の材料になるため
履歴テーブルへ移す。冪等: 同じ (race_key, taken_at) は無視されるので何度実行してもよい。
"""

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.storage import connect, ensure_odds_snapshot_table, save_odds_snapshot_history

JST = timezone(timedelta(hours=9))


def main() -> None:
    moved = skipped = 0
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        ensure_odds_snapshot_table(conn)
        rows = conn.execute(
            "select race_key, latest_odds_json from races where latest_odds_json is not null"
        ).fetchall()
        for row in rows:
            try:
                snap = json.loads(row["latest_odds_json"])
            except Exception:
                skipped += 1
                continue
            if not snap.get("exacta") or not snap.get("taken_at"):
                skipped += 1
                continue
            # 取得時刻しか残っていないため、発走までの残り分数は不明(None)とする
            save_odds_snapshot_history(conn, row["race_key"], snap, None)
            moved += 1
        total = conn.execute("select count(*) from odds_snapshots").fetchone()[0]
        races = conn.execute("select count(distinct race_key) from odds_snapshots").fetchone()[0]
    print(
        json.dumps(
            {"moved": moved, "skipped": skipped, "snapshots_total": total, "races_covered": races},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
