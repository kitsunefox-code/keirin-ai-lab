from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.learner import train_win_model
from keirin_ai.predictor import predict_race
from keirin_ai.sources import fetch_url, parse_winticket_racecard
from keirin_ai.storage import connect, learning_status, save_race


def main() -> None:
    parser = argparse.ArgumentParser(description="Revisit saved races and learn newly available results.")
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--include-all", action="store_true", help="Also revisit races that already have results.")
    parser.add_argument("--out", default="data/result_update_log.json")
    args = parser.parse_args()

    updated = []
    skipped = []
    with connect() as conn:
        races = pending_races(conn, args.limit, args.include_all)
        for idx, row in enumerate(races, start=1):
            url = row["source_url"]
            try:
                html = fetch_url(url)
                race = parse_winticket_racecard(html, url)
                if not race.get("entrants"):
                    skipped.append({"race_key": row["race_key"], "reason": "no entrants"})
                    continue
                if not race.get("result"):
                    skipped.append({"race_key": row["race_key"], "reason": "result unavailable"})
                    continue
                prediction = predict_race(race)
                key = save_race(conn, race, prediction)
                updated.append(
                    {
                        "race_key": key,
                        "url": url,
                        "finish_order": race["result"].get("finish_order", []),
                    }
                )
            except Exception as exc:
                skipped.append({"race_key": row["race_key"], "reason": str(exc)})
            if idx < len(races):
                time.sleep(max(0.2, args.delay))

        status = learning_status(conn)

    model = train_win_model()
    payload = {
        "checked": len(races),
        "updated": updated,
        "skipped": skipped,
        "status": status,
        "model_training": model.get("training", {}),
        "model_metrics": model.get("metrics", {}),
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def pending_races(conn: sqlite3.Connection, limit: int, include_all: bool) -> list[sqlite3.Row]:
    where = "source_url like 'https://www.winticket.jp/%'"
    if not include_all:
        where += " and (result_json is null or result_json = '')"
    return conn.execute(
        f"""
        select race_key, source_url, title, race_date, fetched_at
        from races
        where {where}
        order by fetched_at desc
        limit ?
        """,
        (limit,),
    ).fetchall()


if __name__ == "__main__":
    main()
