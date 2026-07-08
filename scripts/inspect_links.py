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
    parser = argparse.ArgumentParser(description="Print links from a public page.")
    parser.add_argument("url")
    parser.add_argument("--limit", type=int, default=120)
    args = parser.parse_args()

    html = fetch_url(args.url)
    links = []
    seen = set()
    for href in re.findall(r"""href=["']([^"']+)["']""", html):
        url = urljoin(args.url, href.replace("&amp;", "&"))
        if url in seen:
            continue
        seen.add(url)
        links.append(url)
        if len(links) >= args.limit:
            break
    print(json.dumps({"source": args.url, "html_length": len(html), "count": len(links), "links": links}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
