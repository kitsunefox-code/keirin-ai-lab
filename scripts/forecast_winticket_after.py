from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.predictor import predict_race
from keirin_ai.sources import fetch_url, parse_winticket_racecard
from keirin_ai.storage import attach_line_partner_stats, connect, latest_player_form, save_race
from keirin_ai.winticket_state import enrich_race_from_state


TOP_URL = "https://www.winticket.jp/keirin/"


def main() -> None:
    parser = argparse.ArgumentParser(description="Forecast WINTICKET public racecards after a given time.")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--after", default="15:00")
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--out", default="data/today_after_1500_forecast.json")
    parser.add_argument("--max-races", type=int, default=18)
    args = parser.parse_args()

    target = datetime.strptime(args.date, "%Y-%m-%d").date()
    candidates = discover_candidates(target)
    forecasts = []
    scanned = 0
    # 取りこぼしの理由を必ず残す。以前ここを黙って continue していたため、
    # 全レースが例外で落ちても「本日0レース」としか分からず原因究明が遅れた。
    dropped = {"fetch_error": 0, "few_entrants": 0, "before_after": 0, "predict_error": 0}
    errors: list[str] = []

    def note(kind: str, url: str, exc: BaseException) -> None:
        dropped[kind] += 1
        if len(errors) < 5:
            errors.append(f"{url} -> {type(exc).__name__}: {exc}")

    with connect() as conn:
        for base in candidates:
            for race_no in range(1, 13):
                if len(forecasts) >= args.max_races:
                    break
                url = f"https://www.winticket.jp/keirin/{base['venue']}/racecard/{base['cup_id']}/{base['day_index']}/{race_no}"
                try:
                    html = fetch_url(url)
                    race = parse_winticket_racecard(html, url)
                    race = enrich_race_from_state(race, html)
                    scanned += 1
                except Exception as exc:
                    note("fetch_error", url, exc)
                    continue
                if len(race.get("entrants", [])) < 5:
                    dropped["few_entrants"] += 1
                    continue
                start_time = race.get("start_time")
                if not start_time or start_time < args.after:
                    dropped["before_after"] += 1
                    continue
                try:
                    _attach_player_form(conn, race)
                    attach_line_partner_stats(conn, race)
                    prediction = predict_race(race)
                    key = save_race(conn, race, prediction)
                    forecasts.append(summarize_forecast(key, race, prediction))
                except Exception as exc:
                    note("predict_error", url, exc)
                    continue
                time.sleep(max(0.1, args.delay))
            if len(forecasts) >= args.max_races:
                break

    payload = {
        "target_date": args.date,
        "after": args.after,
        "scanned_pages": scanned,
        "dropped": dropped,
        "errors": errors,
        "candidates": candidates,
        "forecasts": sorted(forecasts, key=lambda item: (item.get("start_time") or "", item.get("venue") or "")),
    }
    if not forecasts and candidates:
        # 候補があるのに0件はほぼ常に不具合。Actionsのログで赤く出す。
        print(f"::error::予想0件。dropped={dropped} errors={errors}", file=sys.stderr)
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _attach_player_form(conn, race: dict) -> None:
    """前走のレース後談話・着順をDBから各選手へ補完する。"""
    ids = [str(e.get("player_id") or "") for e in race.get("entrants", [])]
    form = latest_player_form(conn, [pid for pid in ids if pid])
    for entrant in race.get("entrants", []):
        info = form.get(str(entrant.get("player_id") or ""))
        if not info:
            continue
        if not entrant.get("post_race_comment") and info.get("post_comment"):
            entrant["post_race_comment"] = info["post_comment"]
        if info.get("finish"):
            entrant["last_finish"] = info["finish"]
        if info.get("factor"):
            entrant["last_factor"] = info["factor"]


def discover_candidates(target: date) -> list[dict]:
    html = fetch_url(TOP_URL)
    cups: dict[tuple[str, str], dict] = {}
    for match in re.finditer(r"/keirin/([^/]+)/racecard/(\d{10})(?:/(\d+)/(\d+))?", html):
        venue, cup_id, day_text, _race_text = match.groups()
        start = datetime.strptime(cup_id[:8], "%Y%m%d").date()
        if start > target:
            continue
        day_index = int(day_text) if day_text else (target - start).days + 1
        if day_index < 1 or day_index > 6:
            continue
        cups[(venue, cup_id)] = {"venue": venue, "cup_id": cup_id, "day_index": day_index}
    return sorted(cups.values(), key=lambda item: (item["venue"], item["cup_id"]))


def summarize_forecast(race_key: str, race: dict, prediction: dict) -> dict:
    rankings = prediction.get("rankings", [])
    tickets = prediction.get("tickets", [])
    top = rankings[0] if rankings else {}
    second = rankings[1] if len(rankings) > 1 else {}
    third = rankings[2] if len(rankings) > 2 else {}
    return {
        "race_key": race_key,
        "venue": race.get("venue"),
        "title": race.get("title"),
        "race_no": race.get("race_no"),
        "race_class": race.get("race_class"),
        "start_time": race.get("start_time"),
        "url": race.get("source", {}).get("url"),
        "lineup": race.get("lineup", []),
        "top3": [
            _short_runner(top),
            _short_runner(second),
            _short_runner(third),
        ],
        "tickets": [ticket["label"] for ticket in tickets[:5]],
        "notes": prediction.get("race_notes", []),
    }


def _short_runner(row: dict) -> dict:
    if not row:
        return {}
    return {
        "car_no": row.get("car_no"),
        "name": row.get("name"),
        "probability": row.get("win_probability"),
        "score": row.get("model_score"),
        "emotion": row.get("emotion", {}).get("tone"),
        "reasons": row.get("reasons", []),
    }


if __name__ == "__main__":
    main()
