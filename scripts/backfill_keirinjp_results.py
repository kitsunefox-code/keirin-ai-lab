from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.learner import train_win_model
from keirin_ai.storage import connect, learning_status, result_from_order
from scripts.backfill_winticket_schedule import KEIRIN_SCHEDULE_URL, VENUE_SLUGS, days_in_month


BASE_URL = "https://keirin.jp"
USER_AGENT = "KeirinAILab/0.1 research prototype; low-frequency personal use"


def main() -> None:
    parser = argparse.ArgumentParser(description="Attach official KEIRIN.JP results to saved WINTICKET races.")
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--venue-codes", default="", help="Comma-separated venue codes to include.")
    parser.add_argument("--out", default="data/backfill/keirinjp_results_log.json")
    args = parser.parse_args()

    allowed_venues = parse_venue_codes(args.venue_codes)
    client = KeirinJpClient()
    updated: list[dict] = []
    skipped: list[dict] = []

    with connect() as conn:
        rows = pending_races(conn, args.limit, allowed_venues)
        for idx, row in enumerate(rows, start=1):
            race_key = row["race_key"]
            parsed = parse_winticket_key(race_key)
            if not parsed:
                skipped.append({"race_key": race_key, "reason": "unsupported race_key"})
                continue
            try:
                order = client.result_order(
                    parsed["start_date"],
                    parsed["venue_code"],
                    parsed["day_index"],
                    parsed["race_no"],
                )
                if len(order) < 2:
                    skipped.append({"race_key": race_key, "reason": "official result unavailable"})
                    continue
                save_official_result(conn, race_key, order)
                updated.append(
                    {
                        "race_key": race_key,
                        "order": order,
                        "source": "KEIRIN.JP PJ0326",
                    }
                )
            except Exception as exc:
                skipped.append({"race_key": race_key, "reason": str(exc)})
            if idx < len(rows):
                time.sleep(max(0.2, args.delay))
        status = learning_status(conn)

    model = train_win_model()
    payload = {
        "checked": len(rows),
        "updated_count": len(updated),
        "updated": updated,
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


class KeirinJpClient:
    def __init__(self) -> None:
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))
        self.schedule_cache: dict[tuple[int, int], str] = {}
        self.event_cache: dict[tuple[str, str], str] = {}
        self.day_cache: dict[tuple[str, str, int], tuple[dict, dict]] = {}
        self.result_cache: dict[tuple[str, str, int, int], list[int]] = {}

    def result_order(self, start_date, venue_code: str, day_index: int, race_no: int) -> list[int]:
        cache_key = (start_date.isoformat(), venue_code, day_index, race_no)
        if cache_key in self.result_cache:
            return self.result_cache[cache_key]

        pc0201, _pj0305 = self.racelist_json(start_date, venue_code, day_index)
        races = pc0201.get("C0201data", {}).get("C0201race", [])
        if race_no < 1 or race_no > len(races):
            return []
        race = races[race_no - 1]
        if str(race.get("rcvKekka") or "") != "1":
            return []

        text = self.post(
            "/pc/racelive",
            {"encp": race.get("encParaR"), "disp": "PJ0326"},
            referer=f"{BASE_URL}/pc/racelist",
        )
        data = extract_jsondata(text, "PJ0326")
        order = order_from_pj0326(data)
        self.result_cache[cache_key] = order
        return order

    def racelist_json(self, start_date, venue_code: str, day_index: int) -> tuple[dict, dict]:
        cache_key = (start_date.isoformat(), venue_code, day_index)
        if cache_key in self.day_cache:
            return self.day_cache[cache_key]

        event_encp = self.event_encp(start_date, venue_code)
        text = self.post(
            "/pc/racelist",
            {"encp": event_encp, "disp": "PJ0305"},
            referer=schedule_url(start_date.year, start_date.month),
        )
        pc0201 = extract_jsondata(text, "PC0201")
        if day_index > 1:
            days = pc0201.get("C0201data", {}).get("C0201kaisai", [])
            if day_index > len(days):
                raise ValueError(f"official day_index unavailable: {day_index}")
            day_encp = days[day_index - 1].get("encParaK")
            text = self.post(
                "/pc/racelist",
                {"encp": day_encp, "disp": "PJ0305"},
                referer=f"{BASE_URL}/pc/racelist",
            )
            pc0201 = extract_jsondata(text, "PC0201")
        pj0305 = extract_jsondata(text, "PJ0305")
        self.day_cache[cache_key] = (pc0201, pj0305)
        return pc0201, pj0305

    def event_encp(self, start_date, venue_code: str) -> str:
        cache_key = (start_date.isoformat(), venue_code)
        if cache_key in self.event_cache:
            return self.event_cache[cache_key]

        html = self.schedule_html(start_date.year, start_date.month)
        row = next(
            (item for item in re.findall(r'<tr class="tr_h">(.*?)</tr>', html, re.S) if f"jocd={venue_code}" in item),
            "",
        )
        if not row:
            raise ValueError(f"schedule venue not found: {venue_code}")

        day = 1
        month_days = days_in_month(start_date.year, start_date.month)
        cells = re.findall(r"<td\b([^>]*)>(.*?)</td>", row, re.S)
        for attrs, body in cells[1:]:
            if "td_day" not in attrs:
                continue
            span_match = re.search(r'colspan=["\']?(\d+)', attrs)
            span = int(span_match.group(1)) if span_match else 1
            if day == start_date.day and "bk_kaisai" in attrs and "/pc/racelist" in body:
                encp_match = re.search(r'data-pprm-encp="([^"]+)"', body)
                if not encp_match:
                    raise ValueError("schedule encp not found")
                self.event_cache[cache_key] = encp_match.group(1)
                return encp_match.group(1)
            day += span
            if day > month_days:
                break
        raise ValueError(f"schedule event not found: {start_date.isoformat()} {venue_code}")

    def schedule_html(self, year: int, month: int) -> str:
        cache_key = (year, month)
        if cache_key not in self.schedule_cache:
            self.schedule_cache[cache_key] = self.get(schedule_url(year, month))
        return self.schedule_cache[cache_key]

    def get(self, url: str) -> str:
        req = Request(url, headers={"User-Agent": USER_AGENT})
        with self.opener.open(req, timeout=20) as response:
            return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")

    def post(self, path: str, params: dict, referer: str) -> str:
        clean = {key: value for key, value in params.items() if value is not None}
        req = Request(
            BASE_URL + path,
            data=urlencode(clean).encode(),
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": referer,
                "Origin": BASE_URL,
            },
        )
        with self.opener.open(req, timeout=20) as response:
            return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")


def pending_races(conn, limit: int, allowed_venues: set[str]):
    rows = conn.execute(
        """
        select race_key, source_url
        from races
        where source_url like 'https://www.winticket.jp/%'
          and (result_json is null or result_json = '')
        order by race_key
        limit ?
        """,
        (limit * 4,),
    ).fetchall()
    filtered = []
    for row in rows:
        parsed = parse_winticket_key(row["race_key"])
        if not parsed:
            continue
        if allowed_venues and parsed["venue_code"] not in allowed_venues:
            continue
        filtered.append(row)
        if len(filtered) >= limit:
            break
    return filtered


def parse_winticket_key(race_key: str) -> dict | None:
    match = re.fullmatch(r"winticket:(\d{8})(\d{2}):(\d+):(\d+)", race_key or "")
    if not match:
        return None
    start_text, venue_code, day_index, race_no = match.groups()
    return {
        "start_date": datetime.strptime(start_text, "%Y%m%d").date(),
        "venue_code": venue_code,
        "day_index": int(day_index),
        "race_no": int(race_no),
    }


def extract_jsondata(html: str, key: str) -> dict:
    patterns = [
        rf"jsonData\['{re.escape(key)}'\]\s*=\s*(\{{.*?\}});",
        rf'jsonData\["{re.escape(key)}"\]\s*=\s*(\{{.*?\}});',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.S)
        if match:
            return json.loads(match.group(1))
    raise ValueError(f"jsonData[{key}] not found")


def order_from_pj0326(data: dict) -> list[int]:
    rows = []
    for item in data.get("tyakujyunItemSubData") or []:
        tyaku = str(item.get("tyaku") or "")
        if not tyaku.isdigit():
            continue
        syaban = str(item.get("syaban") or "")
        if not syaban.isdigit():
            continue
        rows.append((int(tyaku), int(syaban)))
    rows.sort(key=lambda item: item[0])
    return [car_no for _pos, car_no in rows]


def save_official_result(conn, race_key: str, order: list[int]) -> None:
    result = result_from_order(order, source="keirin.jp")
    conn.execute("update races set result_json=? where race_key=?", (json.dumps(result, ensure_ascii=False), race_key))
    for pos, car_no in enumerate(order, start=1):
        conn.execute(
            "update entries set finish_position=?, is_win=? where race_key=? and car_no=?",
            (pos, 1 if pos == 1 else 0, race_key, car_no),
        )
    conn.commit()


def parse_venue_codes(value: str) -> set[str]:
    codes = {item.strip().zfill(2) for item in value.split(",") if item.strip()}
    unknown = sorted(code for code in codes if code not in VENUE_SLUGS)
    if unknown:
        raise ValueError(f"Unknown venue codes: {', '.join(unknown)}")
    return codes


def schedule_url(year: int, month: int) -> str:
    return KEIRIN_SCHEDULE_URL.format(year=year, month=month)


if __name__ == "__main__":
    main()
