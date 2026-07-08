from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.predictor import predict_race
from keirin_ai.sources import fetch_url, parse_winticket_racecard


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a public WINTICKET racecard URL.")
    parser.add_argument("url")
    parser.add_argument("--out", default="data/latest_race.json")
    args = parser.parse_args()

    html = fetch_url(args.url)
    race = parse_winticket_racecard(html, args.url)
    payload = {"race": race, "prediction": predict_race(race)}

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {out}")


if __name__ == "__main__":
    main()
