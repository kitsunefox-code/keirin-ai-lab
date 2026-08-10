from __future__ import annotations

"""過去レースの「段階」(予選/準決勝/決勝/一般/選抜など)と勝ち上がり条件を埋める。

WINTICKETの race.raceType3 に段階が入っているのに、これまで class を
優先して捨てていた。決勝や勝ち上がりのかかった一戦と、何もかからない
敗者戦・一般戦とでは選手の本気度が違う——競輪では常識でありながら
数値化されにくく、そのぶんモデルにも市場にも織り込まれにくい。

python scripts/backfill_race_stage.py --limit 100 --delay 0.4
python scripts/backfill_race_stage.py --limit 3000 --delay 0.4   # 全部
"""

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.sources import fetch_url
from keirin_ai.storage import connect
from keirin_ai.winticket_state import state_queries


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--delay", type=float, default=0.4)
    args = ap.parse_args()

    saved = skipped = failed = 0
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        targets = conn.execute(
            """
            select race_key, source_url from races
            where coalesce(source_url, '') <> ''
              and coalesce(race_stage, '') = ''
            order by race_date desc
            limit ?
            """,
            (args.limit,),
        ).fetchall()

        for idx, race in enumerate(targets, start=1):
            try:
                q = state_queries(fetch_url(race["source_url"]))
                meta = (q.get("FETCH_KEIRIN_RACE") or {}).get("race") or {}
                stage = str(meta.get("raceType3") or "")
                if not stage:
                    skipped += 1
                else:
                    conn.execute(
                        """
                        update races set race_stage=?, advancement_text=?, is_grade_race=?
                        where race_key=?
                        """,
                        (
                            stage,
                            str(meta.get("advancementConditionText") or ""),
                            1 if meta.get("isGradeRace") else 0,
                            race["race_key"],
                        ),
                    )
                    saved += 1
                    if saved % 50 == 0:
                        conn.commit()
            except Exception:
                failed += 1
            if idx < len(targets):
                time.sleep(max(0.2, args.delay))
        conn.commit()
        done = conn.execute("select count(*) from races where coalesce(race_stage,'') <> ''").fetchone()[0]

    print(json.dumps({"targets": len(targets), "saved": saved, "no_stage": skipped, "failed": failed, "total_with_stage": done}, ensure_ascii=False))


if __name__ == "__main__":
    main()
