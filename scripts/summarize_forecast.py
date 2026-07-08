from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Print compact forecast rows.")
    parser.add_argument("path", nargs="?", default="data/today_after_1500_forecast.json")
    args = parser.parse_args()

    data = json.loads(Path(args.path).read_text(encoding="utf-8"))
    print(f"count {len(data.get('forecasts', []))}")
    for race in data.get("forecasts", []):
        top = " / ".join(
            f"{runner.get('car_no')}{runner.get('name')}"
            for runner in race.get("top3", [])
            if runner
        )
        tickets = ", ".join(race.get("tickets", [])[:3])
        print(f"{race.get('start_time')} {race.get('venue')} {race.get('race_no')}R | {top} | {tickets}")


if __name__ == "__main__":
    main()
