from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.learner import train_win_model
from keirin_ai.predictor import predict_race
from keirin_ai.sources import fetch_url, parse_winticket_racecard
from keirin_ai.storage import connect, result_from_order, save_race


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a result order and retrain.")
    parser.add_argument("url", help="WINTICKET public racecard URL")
    parser.add_argument("--order", required=True, help="Finish order, e.g. 3,7,1,2,5,4,6")
    args = parser.parse_args()

    order = [int(part.strip()) for part in args.order.split(",") if part.strip()]
    html = fetch_url(args.url)
    race = parse_winticket_racecard(html, args.url)
    race["result"] = result_from_order(order, source="manual")
    prediction = predict_race(race)

    with connect() as conn:
        key = save_race(conn, race, prediction)
    model = train_win_model()
    print(json.dumps({"saved": key, "order": order, "model": model["training"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
