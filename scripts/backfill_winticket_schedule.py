from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.learner import train_win_model
from keirin_ai.predictor import predict_race
from keirin_ai.sources import fetch_url, parse_winticket_racecard
from keirin_ai.storage import connect, learning_status, save_race


KEIRIN_SCHEDULE_URL = "https://keirin.jp/pc/raceschedule?scym={month:02d}&scyy={year:04d}"

VENUE_SLUGS = {
    "11": "hakodate",
    "12": "aomori",
    "13": "iwakidaira",
    "21": "yahiko",
    "22": "maebashi",
    "23": "toride",
    "24": "utsunomiya",
    "25": "omiya",
    "26": "seibuen",
    "27": "keiokaku",
    "28": "tachikawa",
    "31": "matsudo",
    "34": "kawasaki",
    "35": "hiratsuka",
    "36": "odawara",
    "37": "ito",
    "38": "shizuoka",
    "42": "nagoya",
    "43": "gifu",
    "44": "ogaki",
    "45": "toyohashi",
    "46": "toyama",
    "47": "matsusaka",
    "48": "yokkaichi",
    "51": "fukui",
    "53": "nara",
    "54": "mukomachi",
    "55": "wakayama",
    "56": "kishiwada",
    "61": "tamano",
    "62": "hiroshima",
    "63": "hofu",
    "71": "takamatsu",
    "73": "komatsushima",
    "74": "kochi",
    "75": "matsuyama",
    "81": "kokura",
    "83": "kurume",
    "84": "takeo",
    "85": "sasebo",
    "86": "beppu",
    "87": "kumamoto",
}

VENUE_NAMES = {
    "11": "函館",
    "12": "青森",
    "13": "いわき平",
    "21": "弥彦",
    "22": "前橋",
    "23": "取手",
    "24": "宇都宮",
    "25": "大宮",
    "26": "西武園",
    "27": "京王閣",
    "28": "立川",
    "31": "松戸",
    "34": "川崎",
    "35": "平塚",
    "36": "小田原",
    "37": "伊東",
    "38": "静岡",
    "42": "名古屋",
    "43": "岐阜",
    "44": "大垣",
    "45": "豊橋",
    "46": "富山",
    "47": "松阪",
    "48": "四日市",
    "51": "福井",
    "53": "奈良",
    "54": "向日町",
    "55": "和歌山",
    "56": "岸和田",
    "61": "玉野",
    "62": "広島",
    "63": "防府",
    "71": "高松",
    "73": "小松島",
    "74": "高知",
    "75": "松山",
    "81": "小倉",
    "83": "久留米",
    "84": "武雄",
    "85": "佐世保",
    "86": "別府",
    "87": "熊本",
}


@dataclass(frozen=True)
class Event:
    start_date: date
    venue_code: str
    venue_name: str
    grade: str
    max_day_index: int

    @property
    def cup_id(self) -> str:
        return f"{self.start_date:%Y%m%d}{self.venue_code}"

    @property
    def slug(self) -> str:
        return VENUE_SLUGS[self.venue_code]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill WINTICKET racecards by reading official KEIRIN.JP monthly schedules."
    )
    parser.add_argument("--start", required=True, help="First month or date, e.g. 2026-06 or 2026-06-01.")
    parser.add_argument("--end", required=True, help="Last month or date, e.g. 2026-07 or 2026-07-07.")
    parser.add_argument("--delay", type=float, default=0.45)
    parser.add_argument("--max-pages", type=int, default=300)
    parser.add_argument("--max-days", type=int, default=6)
    parser.add_argument("--venue-codes", default="", help="Comma-separated venue codes to include, e.g. 42,45,55.")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--out", default="data/backfill/schedule_backfill_log.json")
    args = parser.parse_args()

    start = parse_partial_date(args.start, end_of_month=False)
    end = parse_partial_date(args.end, end_of_month=True)
    allowed_venues = parse_venue_codes(args.venue_codes)
    events = [
        event
        for event in discover_events(start, end, args.max_days)
        if not allowed_venues or event.venue_code in allowed_venues
    ]

    saved: list[dict] = []
    skipped: list[dict] = []
    scanned = 0

    with connect() as conn:
        for event in events:
            for day_index in range(1, event.max_day_index + 1):
                for race_no in range(1, 13):
                    if scanned >= args.max_pages:
                        break
                    race_key = f"winticket:{event.cup_id}:{day_index}:{race_no}"
                    if args.skip_existing and race_exists(conn, race_key):
                        skipped.append({"race_key": race_key, "reason": "already saved"})
                        continue
                    url = (
                        f"https://www.winticket.jp/keirin/{event.slug}/racecard/"
                        f"{event.cup_id}/{day_index}/{race_no}"
                    )
                    scanned += 1
                    try:
                        html = fetch_url(url)
                        race = parse_winticket_racecard(html, url)
                        entrant_count = len(race.get("entrants", []))
                        if entrant_count < 5:
                            skipped.append({"url": url, "reason": f"entrants={entrant_count}"})
                            continue
                        prediction = predict_race(race)
                        key = save_race(conn, race, prediction)
                        saved.append(
                            {
                                "race_key": key,
                                "url": url,
                                "venue": race.get("venue") or event.venue_name,
                                "race_no": race.get("race_no") or race_no,
                                "date": race.get("date"),
                                "start_time": race.get("start_time"),
                                "entrants": entrant_count,
                                "has_result": bool(race.get("result")),
                            }
                        )
                    except Exception as exc:
                        skipped.append({"url": url, "reason": str(exc)})
                    time.sleep(max(0.2, args.delay))
                if scanned >= args.max_pages:
                    break
            if scanned >= args.max_pages:
                break
        status = learning_status(conn)

    model = train_win_model()
    payload = {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "venue_codes": sorted(allowed_venues),
        "event_count": len(events),
        "events": [event.__dict__ | {"start_date": event.start_date.isoformat()} for event in events],
        "scanned": scanned,
        "saved_count": len(saved),
        "result_count": sum(1 for item in saved if item["has_result"]),
        "saved": saved,
        "skipped_count": len(skipped),
        "skipped": skipped[:120],
        "status": status,
        "model_training": model.get("training", {}),
        "model_metrics": model.get("metrics", {}),
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def discover_events(start: date, end: date, max_days: int) -> list[Event]:
    events: dict[tuple[date, str], Event] = {}
    for year, month in iter_months(start, end):
        html = fetch_url(KEIRIN_SCHEDULE_URL.format(year=year, month=month))
        for event in extract_calendar_events(html, year, month, max_days):
            if event.start_date < start or event.start_date > end:
                continue
            key = (event.start_date, event.venue_code)
            previous = events.get(key)
            if previous and previous.max_day_index >= event.max_day_index:
                continue
            events[key] = event
    return sorted(events.values(), key=lambda item: (item.start_date, item.venue_code))


def extract_calendar_events(html: str, year: int, month: int, max_days: int) -> list[Event]:
    events: list[Event] = []
    month_days = days_in_month(year, month)
    for row in re.findall(r'<tr class="tr_h">(.*?)</tr>', html, re.S):
        venue_match = re.search(r"jocd=(\d{2})", row)
        if not venue_match:
            continue
        venue_code = venue_match.group(1)
        if venue_code not in VENUE_SLUGS:
            continue

        day = 1
        cells = re.findall(r"<td\b([^>]*)>(.*?)</td>", row, re.S)
        for attrs, body in cells[1:]:
            if "td_day" not in attrs:
                continue
            span_match = re.search(r'colspan=["\']?(\d+)', attrs)
            span = int(span_match.group(1)) if span_match else 1
            if "bk_kaisai" in attrs and "/pc/racelist" in body and day <= month_days:
                events.append(
                    Event(
                        start_date=date(year, month, day),
                        venue_code=venue_code,
                        venue_name=VENUE_NAMES.get(venue_code, venue_code),
                        grade=parse_grade(body),
                        max_day_index=min(max_days, max(1, span)),
                    )
                )
            day += span
            if day > month_days:
                break
    return events


def parse_grade(html_fragment: str) -> str:
    match = re.search(r"/grade/ico_([a-z0-9]+)\.png", html_fragment)
    return match.group(1).upper() if match else ""


def parse_partial_date(value: str, end_of_month: bool) -> date:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.strptime(value, "%Y-%m-%d").date()
    if not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("--start/--end must be YYYY-MM or YYYY-MM-DD")
    year, month = [int(part) for part in value.split("-")]
    if not end_of_month:
        return date(year, month, 1)
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return date.fromordinal(date(next_year, next_month, 1).toordinal() - 1)


def parse_yyyymmdd(value: str) -> date | None:
    if not re.fullmatch(r"\d{8}", value):
        return None
    return datetime.strptime(value, "%Y%m%d").date()


def parse_venue_codes(value: str) -> set[str]:
    codes = {item.strip().zfill(2) for item in value.split(",") if item.strip()}
    unknown = sorted(code for code in codes if code not in VENUE_SLUGS)
    if unknown:
        raise ValueError(f"Unknown venue codes: {', '.join(unknown)}")
    return codes


def days_in_month(year: int, month: int) -> int:
    next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return date(next_year, next_month, 1).toordinal() - date(year, month, 1).toordinal()


def iter_months(start: date, end: date):
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        yield year, month
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def race_exists(conn, race_key: str) -> bool:
    row = conn.execute("select 1 from races where race_key=? limit 1", (race_key,)).fetchone()
    return row is not None


if __name__ == "__main__":
    main()
