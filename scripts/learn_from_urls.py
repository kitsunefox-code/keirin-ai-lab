from __future__ import annotations

import argparse
import json
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
    parser = argparse.ArgumentParser(description="Fetch WINTICKET race URLs, store them, then retrain.")
    parser.add_argument("url_file", help="Text file with one racecard URL per line")
    parser.add_argument("--delay", type=float, default=2.0, help="Seconds between requests")
    args = parser.parse_args()

    urls = [
        line.strip()
        for line in Path(args.url_file).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    saved = []
    with connect() as conn:
        for idx, url in enumerate(urls, start=1):
            html = fetch_url(url)
            race = parse_winticket_racecard(html, url)
            prediction = predict_race(race)
            key = save_race(conn, race, prediction)
            saved.append({"race_key": key, "url": url, "has_result": bool(race.get("result"))})
            if idx < len(urls):
                time.sleep(max(0.5, args.delay))
        status = learning_status(conn)

    model = train_win_model()
    print(json.dumps({"saved": saved, "status": status, "model": model["training"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
