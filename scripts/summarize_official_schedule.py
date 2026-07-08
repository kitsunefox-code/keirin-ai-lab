from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.backfill_winticket_schedule import discover_events, parse_partial_date


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize official KEIRIN.JP schedule events.")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    start = parse_partial_date(args.start, end_of_month=False)
    end = parse_partial_date(args.end, end_of_month=True)
    events = discover_events(start, end, max_days=6)

    by_year = Counter(event.start_date.strftime("%Y") for event in events)
    by_month = Counter(event.start_date.strftime("%Y-%m") for event in events)
    by_venue = Counter(event.venue_code for event in events)
    by_grade = Counter(event.grade or "unknown" for event in events)

    payload = {
        "created_at": date.today().isoformat(),
        "range": f"{start.isoformat()}..{end.isoformat()}",
        "total_events": len(events),
        "by_year": dict(sorted(by_year.items())),
        "by_month": dict(sorted(by_month.items())),
        "by_venue_code": dict(sorted(by_venue.items())),
        "by_grade": dict(sorted(by_grade.items())),
        "source": "https://keirin.jp/pc/raceschedule",
    }

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ["range", "total_events", "by_year", "by_grade"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
