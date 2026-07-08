from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.sources import fetch_url
from keirin_ai.storage import connect, learning_status, save_source_document
from keirin_ai.text_learning import extract_text_features, is_learnable_document


def main() -> None:
    parser = argparse.ArgumentParser(description="Learn allowed text pages as feature tags, not full article copies.")
    parser.add_argument("--url")
    parser.add_argument("--file")
    parser.add_argument("--kind", default="article")
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()

    urls = []
    if args.url:
        urls.append(args.url)
    if args.file:
        urls.extend(
            line.strip()
            for line in Path(args.file).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    if not urls:
        raise SystemExit("provide --url or --file")

    learned = []
    with connect() as conn:
        for idx, url in enumerate(urls, start=1):
            html = fetch_url(url)
            doc = extract_text_features(html, url, kind=args.kind)
            ok, reason = is_learnable_document(doc)
            if not ok:
                learned.append(
                    {
                        "id": None,
                        "url": url,
                        "title": doc["title"],
                        "tags": [],
                        "signal_score": doc["signal_score"],
                        "skipped": reason,
                    }
                )
                continue
            doc_id = save_source_document(conn, doc)
            learned.append(
                {
                    "id": doc_id,
                    "url": url,
                    "title": doc["title"],
                    "tags": sorted(doc["tags"].keys()),
                    "signal_score": doc["signal_score"],
                }
            )
            if idx < len(urls):
                time.sleep(max(0.5, args.delay))
        status = learning_status(conn)
    print(json.dumps({"learned": learned, "status": status}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
