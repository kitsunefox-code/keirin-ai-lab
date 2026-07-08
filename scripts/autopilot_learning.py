from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.learner import train_win_model
from keirin_ai.predictor import predict_race
from keirin_ai.sources import fetch_url, parse_winticket_racecard
from keirin_ai.storage import connect, learning_status, save_race, save_source_document
from keirin_ai.text_learning import extract_text_features, is_learnable_document


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a safe learning pass: text tags, result updates, retrain.")
    parser.add_argument("--source-file", default="data/learning_sources.txt")
    parser.add_argument("--result-limit", type=int, default=40)
    parser.add_argument("--delay", type=float, default=0.7)
    parser.add_argument("--out", default="data/autopilot_learning_log.json")
    args = parser.parse_args()

    log = {
        "started_at": now(),
        "text_learning": [],
        "result_updates": [],
        "skipped": [],
    }

    with connect() as conn:
        for url in read_urls(ROOT / args.source_file):
            try:
                html = fetch_url(url)
                doc = extract_text_features(html, url, kind=kind_from_url(url))
                ok, reason = is_learnable_document(doc)
                if not ok:
                    log["skipped"].append({"url": url, "stage": "text", "reason": reason})
                    continue
                doc_id = save_source_document(conn, doc)
                log["text_learning"].append(
                    {
                        "id": doc_id,
                        "url": url,
                        "title": doc.get("title"),
                        "tags": sorted(doc.get("tags", {}).keys()),
                        "signal_score": doc.get("signal_score"),
                    }
                )
            except Exception as exc:
                log["skipped"].append({"url": url, "stage": "text", "reason": str(exc)})
            time.sleep(max(0.2, args.delay))

        for row in pending_winticket_races(conn, args.result_limit):
            try:
                html = fetch_url(row["source_url"])
                race = parse_winticket_racecard(html, row["source_url"])
                if not race.get("entrants"):
                    log["skipped"].append({"race_key": row["race_key"], "stage": "result", "reason": "no entrants"})
                    continue
                if not race.get("result"):
                    log["skipped"].append({"race_key": row["race_key"], "stage": "result", "reason": "result unavailable"})
                    continue
                prediction = predict_race(race)
                key = save_race(conn, race, prediction)
                log["result_updates"].append(
                    {
                        "race_key": key,
                        "url": row["source_url"],
                        "finish_order": race["result"].get("finish_order", []),
                    }
                )
            except Exception as exc:
                log["skipped"].append({"race_key": row["race_key"], "stage": "result", "reason": str(exc)})
            time.sleep(max(0.2, args.delay))

        log["status_before_training"] = learning_status(conn)

    model = train_win_model()
    with connect() as conn:
        log["status_after_training"] = learning_status(conn)
    log["model_training"] = model.get("training", {})
    log["model_metrics"] = model.get("metrics", {})
    log["finished_at"] = now()

    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(log, ensure_ascii=False, indent=2))


def pending_winticket_races(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        select race_key, source_url
        from races
        where source_url like 'https://www.winticket.jp/%'
          and (result_json is null or result_json = '')
        order by fetched_at desc
        limit ?
        """,
        (limit,),
    ).fetchall()


def read_urls(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def kind_from_url(url: str) -> str:
    if "raceschedule" in url:
        return "official_schedule"
    if "/news" in url:
        return "news_index"
    if "winticket.jp" in url:
        return "race_portal"
    if "keirin.jp" in url:
        return "official_portal"
    return "portal"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


if __name__ == "__main__":
    main()
