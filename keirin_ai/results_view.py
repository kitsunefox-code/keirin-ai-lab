from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))


def build_results_payload(conn: sqlite3.Connection, date: str | None = None, data_dir: Path | str | None = None) -> dict:
    dates = _available_dates(conn)
    if not dates:
        return {"ok": True, "date": None, "dates": [], "summary": _summary([]), "venues": []}

    target = _normalize_date(date) if date else None
    if target not in dates:
        target = dates[0]

    time_map = _start_time_map(data_dir)
    races = [_race_report(conn, row, time_map) for row in _races_on(conn, target)]
    races = [race for race in races if race is not None]
    venues: dict[str, list[dict]] = {}
    for race in races:
        venues.setdefault(race["venue"] or "未設定", []).append(race)
    venue_list = [
        {"venue": venue, "races": sorted(items, key=lambda r: (r["start_time"] or "99:99", r["race_no"] or 0))}
        for venue, items in venues.items()
    ]
    venue_list.sort(key=lambda v: v["races"][0]["start_time"] or "99:99")

    return {
        "ok": True,
        "date": target,
        "dates": dates[:14],
        "summary": _summary(races),
        "venues": venue_list,
    }


def _available_dates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        select distinct race_date from races
        where race_date is not null and race_date != ''
          and race_key in (select race_key from predictions)
        """
    ).fetchall()
    dates = {iso for row in rows if (iso := _normalize_date(row["race_date"]))}
    return sorted(dates, reverse=True)


def _races_on(conn: sqlite3.Connection, iso_date: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        select race_key, venue, race_no, race_date, source_url, result_json
        from races
        where race_key in (select race_key from predictions)
        order by race_no
        """
    ).fetchall()
    return [row for row in rows if _normalize_date(row["race_date"]) == iso_date]


def _start_time_map(data_dir: Path | str | None) -> dict[str, str]:
    if not data_dir:
        return {}
    mapping: dict[str, str] = {}
    for path in sorted(Path(data_dir).glob("forecast_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in payload.get("forecasts", []):
            key = item.get("race_key")
            start = item.get("start_time")
            if key and start:
                mapping[key] = start
    return mapping


def _race_report(conn: sqlite3.Connection, row: sqlite3.Row, time_map: dict[str, str]) -> dict | None:
    # 最初の予想 = レース前にAIが出した答え。再学習後の上書きは評価に使わない。
    pred = conn.execute(
        """
        select ranking_json, tickets_json, created_at from predictions
        where race_key=? order by id asc limit 1
        """,
        (row["race_key"],),
    ).fetchone()
    if not pred:
        return None
    ranking = _json_or(pred["ranking_json"], [])
    tickets = _json_or(pred["tickets_json"], [])
    if not ranking:
        return None

    top = ranking[0]
    top_pick = {
        "car_no": top.get("car_no"),
        "name": top.get("name") or "",
        "probability": round(float(top.get("win_probability") or top.get("probability") or 0), 4),
    }
    ticket_labels = [_ticket_label(t) for t in tickets[:3]]
    ticket_labels = [label for label in ticket_labels if label]

    start_time = time_map.get(row["race_key"], "")
    result = _json_or(row["result_json"], None)
    order = (result or {}).get("finish_order") or []

    if not order:
        status = "pending"
        hits = {"honmei": None, "in_top3": None, "trifecta": None, "trifecta_label": None}
    else:
        actual3 = "-".join(str(car) for car in order[:3])
        trifecta_label = actual3 if actual3 in ticket_labels else None
        honmei = int(top_pick["car_no"] or 0) == int(order[0])
        in_top3 = int(top_pick["car_no"] or 0) in [int(c) for c in order[:3]]
        hits = {
            "honmei": honmei,
            "in_top3": in_top3,
            "trifecta": trifecta_label is not None,
            "trifecta_label": trifecta_label,
        }
        if trifecta_label:
            status = "hit_trifecta"
        elif honmei:
            status = "hit_honmei"
        elif in_top3:
            status = "in_top3"
        else:
            status = "miss"

    return {
        "race_key": row["race_key"],
        "venue": row["venue"] or "",
        "race_no": row["race_no"],
        "start_time": start_time,
        "url": row["source_url"] or "",
        "top_pick": top_pick,
        "tickets": ticket_labels,
        "result_order": order[:3],
        "status": status,
        "hits": hits,
    }


def _summary(races: list[dict]) -> dict:
    settled = [race for race in races if race["status"] != "pending"]
    honmei = sum(1 for race in settled if race["hits"]["honmei"])
    in_top3 = sum(1 for race in settled if race["hits"]["in_top3"])
    trifecta = sum(1 for race in settled if race["hits"]["trifecta"])
    return {
        "races": len(races),
        "settled": len(settled),
        "pending": len(races) - len(settled),
        "honmei_hits": honmei,
        "honmei_rate": round(honmei / len(settled), 4) if settled else None,
        "in_top3_hits": in_top3,
        "in_top3_rate": round(in_top3 / len(settled), 4) if settled else None,
        "trifecta_hits": trifecta,
        "trifecta_rate": round(trifecta / len(settled), 4) if settled else None,
    }


def _normalize_date(raw: str | None) -> str | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if not match:
        match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", raw)
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def _ticket_label(ticket) -> str:
    if isinstance(ticket, str):
        return ticket
    if isinstance(ticket, dict):
        return ticket.get("label") or "-".join(str(c) for c in ticket.get("cars", []))
    return ""


def _json_or(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default
