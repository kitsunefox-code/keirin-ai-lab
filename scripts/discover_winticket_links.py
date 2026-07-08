from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.sources import fetch_url


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover public WINTICKET racecard links from a page.")
    parser.add_argument("--url", default="https://www.winticket.jp/keirin/")
    parser.add_argument("--contains", default="")
    args = parser.parse_args()

    html = fetch_url(args.url)
    links = sorted(
        {
            urljoin(args.url, match)
            for match in re.findall(r'href="([^"]*?/keirin/[^"]*?racecard[^"]*?)"', html)
        }
    )
    if args.contains:
        links = [link for link in links if args.contains in link]
    print(json.dumps({"source": args.url, "count": len(links), "links": links}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
