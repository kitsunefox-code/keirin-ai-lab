from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.learner import train_win_model
from keirin_ai.predictor import predict_race
from keirin_ai.sources import fetch_url, parse_winticket_racecard
from keirin_ai.storage import connect, learning_status, save_race
from scripts.forecast_winticket_after import discover_candidates


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill all earlier days of currently discovered WINTICKET events.")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--delay", type=float, default=0.7)
    parser.add_argument("--max-pages", type=int, default=240)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--out", default="data/backfill/current_events_log.json")
    args = parser.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date()
    candidates = discover_candidates(target)
    saved = []
    skipped = []
    scanned = 0

    with connect() as conn:
        for event in candidates:
            for day_index in range(1, int(event["day_index"]) + 1):
                for race_no in range(1, 13):
                    if scanned >= args.max_pages:
                        break
                    race_key = f"winticket:{event['cup_id']}:{day_index}:{race_no}"
                    if args.skip_existing and race_exists(conn, race_key):
                        skipped.append({"race_key": race_key, "reason": "already saved"})
                        continue
                    url = (
                        f"https://www.winticket.jp/keirin/{event['venue']}/racecard/"
                        f"{event['cup_id']}/{day_index}/{race_no}"
                    )
                    scanned += 1
                    try:
                        html = fetch_url(url)
                        race = parse_winticket_racecard(html, url)
                        entrant_count = len(race.get("entrants", []))
                        if entrant_count < 5:
                            skipped.append({"url": url, "reason": f"entrants={entrant_count}"})
                            continue
                        prediction = predict_race(race)
                        key = save_race(conn, race, prediction)
                        saved.append(
                            {
                                "race_key": key,
                                "url": url,
                                "venue": race.get("venue"),
                                "race_no": race.get("race_no"),
                                "date": race.get("date"),
                                "start_time": race.get("start_time"),
                                "entrants": entrant_count,
                                "has_result": bool(race.get("result")),
                            }
                        )
                    except Exception as exc:
                        skipped.append({"url": url, "reason": str(exc)})
                    time.sleep(max(0.2, args.delay))
                if scanned >= args.max_pages:
                    break
            if scanned >= args.max_pages:
                break
        status = learning_status(conn)

    model = train_win_model()
    payload = {
        "target_date": args.date,
        "candidates": candidates,
        "scanned": scanned,
        "saved_count": len(saved),
        "result_count": sum(1 for item in saved if item["has_result"]),
        "saved": saved,
        "skipped_count": len(skipped),
        "skipped": skipped[:80],
        "status": status,
        "model_training": model.get("training", {}),
        "model_metrics": model.get("metrics", {}),
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def race_exists(conn, race_key: str) -> bool:
    row = conn.execute("select 1 from races where race_key=? limit 1", (race_key,)).fetchone()
    return row is not None


if __name__ == "__main__":
    main()
