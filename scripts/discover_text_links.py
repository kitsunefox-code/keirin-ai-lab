from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.sources import fetch_url


DEFAULT_PATTERNS = [
    "/news/",
    "/column/",
    "/race/",
    "/keirin/",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Discover public text links for tag-only learning.")
    parser.add_argument("--url", required=True)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--pattern", action="append", default=[])
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    html = fetch_url(args.url)
    base_domain = urlparse(args.url).netloc
    patterns = args.pattern or DEFAULT_PATTERNS
    links = []
    seen = set()
    for href in re.findall(r'href=["\']([^"\']+)["\']', html):
        url = urljoin(args.url, href.replace("&amp;", "&"))
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            continue
        if parsed.netloc != base_domain:
            continue
        if not any(pattern in parsed.path for pattern in patterns):
            continue
        clean = parsed._replace(query="", fragment="").geturl()
        if clean in seen:
            continue
        seen.add(clean)
        links.append(clean)
        if len(links) >= args.limit:
            break

    payload = {"source": args.url, "count": len(links), "links": links}
    if args.out:
        out = ROOT / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(links) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
