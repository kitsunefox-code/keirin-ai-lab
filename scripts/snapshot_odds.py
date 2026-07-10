from __future__ import annotations

"""発走前の2車単オッズをスナップショットして races.latest_odds_json に保存する。

妙味ボード(期待値=較正済みAI確率×実オッズ)の材料。live更新(15分ごと)から呼ばれ、
これから発走するレース(既定: 75分先まで)だけを対象に軽く取得する。

python scripts\\snapshot_odds.py --window 75 --limit 16
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.odds import odds_url_from_racecard
from keirin_ai.sources import fetch_url
from keirin_ai.storage import connect, save_race_odds_snapshot
from keirin_ai.winticket_state import state_queries

JST = timezone(timedelta(hours=9))


def _today_forecasts() -> list[dict]:
    """今日の forecast_*.json から race_key / url / start_time を拾う。"""
    stamp = datetime.now(JST).strftime("%Y%m%d")
    files = sorted((ROOT / "data").glob(f"forecast_{stamp}_*.json"))
    if not files:
        return []
    try:
        payload = json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return []
    return payload.get("forecasts") or []


def _start_dt(start_time: str) -> datetime | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(start_time or "").strip())
    if not m:
        return None
    now = datetime.now(JST)
    return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)


def _normalize_exacta(odds_list) -> list[dict]:
    rows = []
    for row in odds_list or []:
        key = row.get("key")
        odds = row.get("odds")
        if isinstance(key, list) and len(key) == 2 and odds:
            try:
                rows.append({"key": f"{int(key[0])}-{int(key[1])}", "odds": float(odds)})
            except (TypeError, ValueError):
                continue
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Snapshot pre-race exacta odds for EV board.")
    parser.add_argument("--window", type=int, default=75, help="何分先の発走まで対象にするか")
    parser.add_argument("--limit", type=int, default=16)
    parser.add_argument("--delay", type=float, default=0.4)
    args = parser.parse_args()

    now = datetime.now(JST)
    horizon = now + timedelta(minutes=args.window)
    targets = []
    for race in _today_forecasts():
        start = _start_dt(race.get("start_time"))
        if not start or not race.get("url") or not race.get("race_key"):
            continue
        # 発走2分前〜window分先: まだ買えるレースだけ
        if now - timedelta(minutes=2) <= start <= horizon:
            targets.append(race)
    targets.sort(key=lambda r: r.get("start_time") or "99:99")
    targets = targets[: args.limit]

    saved = skipped = 0
    with connect() as conn:
        for idx, race in enumerate(targets, start=1):
            try:
                html = fetch_url(odds_url_from_racecard(race["url"]))
                odds = state_queries(html).get("FETCH_KEIRIN_RACE_ODDS", {})
                exacta = _normalize_exacta(odds.get("exacta"))
                if exacta:
                    save_race_odds_snapshot(
                        conn,
                        race["race_key"],
                        {"exacta": exacta, "taken_at": datetime.now(JST).isoformat(timespec="seconds")},
                    )
                    saved += 1
                else:
                    skipped += 1
            except Exception:
                skipped += 1
            if idx < len(targets):
                time.sleep(max(0.2, args.delay))

    print(json.dumps({"targets": len(targets), "saved": saved, "skipped": skipped}, ensure_ascii=False))


if __name__ == "__main__":
    main()
