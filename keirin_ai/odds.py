from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from keirin_ai.sources import fetch_url


EX_LABELS = {
    "exSpurt": "EXスパート",
    "exThrust": "EX突き抜け",
    "exLeftBehind": "EX置かれ",
    "exSplitLine": "EXライン分断",
    "exSnatch": "EX奪取",
    "exCompete": "EX競り",
}


def odds_url_from_racecard(racecard_url: str) -> str:
    parsed = urlparse(racecard_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 6 and parts[0] == "keirin" and parts[2] == "racecard":
        slug = parts[1]
        cup_id, day_index, race_no = parts[3], parts[4], parts[5]
        return f"{parsed.scheme}://{parsed.netloc}/keirin/{slug}/odds/{cup_id}/{day_index}/{race_no}"
    if len(parts) >= 6 and parts[0] == "keirin" and parts[2] == "odds":
        return racecard_url
    raise ValueError("WINTICKETの出走表URLからオッズURLを作れません")


def fetch_live_odds(racecard_url: str, timeout: int = 10) -> dict:
    odds_url = odds_url_from_racecard(racecard_url)
    html = fetch_url(odds_url, timeout=timeout)
    parsed = parse_winticket_odds(html, odds_url)
    parsed["source_url"] = odds_url
    parsed["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return parsed


def parse_winticket_odds(html_text: str, source_url: str = "") -> dict:
    state = _preloaded_state(html_text)
    trifecta_rows = _find_first_list(state, "trifecta", require_odds=True)
    odds_map = {}
    for row in trifecta_rows:
        if not isinstance(row, dict):
            continue
        key = row.get("key")
        if not isinstance(key, list) or len(key) != 3:
            continue
        label = "-".join(str(int(part)) for part in key)
        odds_map[label] = {
            "label": label,
            "odds": _float_or_none(row.get("odds")),
            "odds_str": str(row.get("oddsStr") or row.get("odds") or ""),
            "popularity": row.get("popularityOrder"),
            "absent": bool(row.get("absent")),
            "unit_price": row.get("unitPrice") or 100,
        }

    race_data = _find_race_data(state)
    ex_records = _extract_ex_records(race_data)
    return {
        "ok": True,
        "source_url": source_url,
        "trifecta_count": len(odds_map),
        "trifecta": odds_map,
        "ex_records": ex_records,
        "status": "live" if odds_map else "unavailable",
    }


def ex_signals_for_cars(ex_records: list[dict], cars: list[int], limit: int = 3) -> list[str]:
    by_car = {int(item.get("car_no") or 0): item for item in ex_records}
    signals: list[str] = []
    for car in cars:
        record = by_car.get(int(car))
        if not record:
            continue
        best = _best_ex(record.get("ex") or {})
        if not best:
            continue
        key, value = best
        name = record.get("name") or ""
        signals.append(
            f"{car}{name}: {EX_LABELS.get(key, key)} {value.get('percentage', 0)}% ({value.get('succeeded', 0)}/{value.get('total', 0)})"
        )
        if len(signals) >= limit:
            break
    return signals


def _preloaded_state(html_text: str) -> dict:
    prefix = "window.__PRELOADED_STATE__ = "
    start = html_text.find(prefix)
    if start < 0:
        return {}
    script_end = html_text.find("</script>", start)
    if script_end < 0:
        return {}
    body = html_text[start + len(prefix) : script_end].strip()
    json_text = _first_balanced_json(body)
    if not json_text:
        return {}
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        return {}


def _first_balanced_json(text: str) -> str:
    start = text.find("{")
    if start < 0:
        return ""
    level = 0
    in_string = False
    escape = False
    for index, char in enumerate(text[start:], start=start):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            level += 1
        elif char == "}":
            level -= 1
            if level == 0:
                return text[start : index + 1]
    return ""


def _find_first_list(value: Any, key: str, require_odds: bool = False) -> list:
    if isinstance(value, dict):
        candidate = value.get(key)
        if isinstance(candidate, list) and candidate:
            if not require_odds or any(isinstance(item, dict) and item.get("odds") is not None for item in candidate):
                return candidate
        for child in value.values():
            found = _find_first_list(child, key, require_odds=require_odds)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_first_list(child, key, require_odds=require_odds)
            if found:
                return found
    return []


def _find_race_data(value: Any) -> dict:
    if isinstance(value, dict):
        if all(isinstance(value.get(key), list) for key in ("entries", "players", "records")):
            return value
        for child in value.values():
            found = _find_race_data(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_race_data(child)
            if found:
                return found
    return {}


def _extract_ex_records(race_data: dict) -> list[dict]:
    entries = race_data.get("entries") or []
    players = race_data.get("players") or []
    records = race_data.get("records") or []
    number_by_player = {
        str(entry.get("playerId")): entry.get("number")
        for entry in entries
        if entry.get("playerId") and entry.get("number") is not None
    }
    name_by_player = {str(player.get("id")): player.get("name") for player in players if player.get("id")}
    output = []
    for record in records:
        player_id = str(record.get("playerId") or "")
        car_no = number_by_player.get(player_id)
        if car_no is None:
            continue
        ex = {key: record.get(key) for key in EX_LABELS if isinstance(record.get(key), dict)}
        output.append(
            {
                "car_no": int(car_no),
                "player_id": player_id,
                "name": name_by_player.get(player_id) or "",
                "style": record.get("style") or "",
                "race_point": record.get("racePoint"),
                "comment": record.get("comment") or "",
                "ex": ex,
            }
        )
    return sorted(output, key=lambda item: item["car_no"])


def _best_ex(ex: dict) -> tuple[str, dict] | None:
    candidates = [
        (key, value)
        for key, value in ex.items()
        if isinstance(value, dict) and int(value.get("total") or 0) > 0
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: (float(item[1].get("percentage") or 0), int(item[1].get("total") or 0)))


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
