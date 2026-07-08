from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "backfill"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a safe backfill plan. This does not scrape pages.")
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    current_year = date.today().year
    start_year = current_year - max(1, args.years) + 1
    months = [
        {
            "year": year,
            "month": month,
            "official_schedule_url": f"https://keirin.jp/pc/raceschedule?year={year}&month={month:02d}",
            "status": "planned",
        }
        for year in range(start_year, current_year + 1)
        for month in range(1, 13)
    ]
    plan = {
        "created_for": f"{args.years}_years",
        "policy": {
            "official_results": "prefer official/public schedules and results",
            "comments": "store feature tags and short excerpts only",
            "columns": "store URL/title/tags/fingerprint, not full body text",
            "rate_limit_seconds": 2.0,
            "manual_review_required_for": ["paid pages", "login pages", "bulk WINTICKET/netkeirin crawling"],
        },
        "months": months,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = Path(args.out) if args.out else OUT_DIR / f"plan_{start_year}_{current_year}.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
