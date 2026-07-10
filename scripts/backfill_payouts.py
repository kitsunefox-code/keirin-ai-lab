from __future__ import annotations

"""結果確定レースの「勝ち組み合わせの確定オッズ(=100円あたり払戻)」を保存する。

回収率(ROI)の計算に使う。WINTICKETのオッズ確定ページから、実際の3連単/2車単の
決着に対応するオッズを引き、races.payouts_json に {"trifecta": x, "exacta": y} で保存。

python scripts\\backfill_payouts.py --limit 300 --since 2026-06-25
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.odds import odds_url_from_racecard
from keirin_ai.sources import fetch_url
from keirin_ai.storage import connect, save_race_payouts
from keirin_ai.winticket_state import state_queries


def _iso(raw: str | None) -> str:
    raw = str(raw or "")
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw) or re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", raw)
    if not m:
        return "0000-00-00"
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


def _winning_odds(odds_list, key: str) -> float | None:
    """確定オッズ配列から key(例 '4-5-7')のオッズを引く。"""
    if not isinstance(odds_list, list):
        return None
    for row in odds_list:
        k = row.get("key")
        if isinstance(k, list) and "-".join(str(int(x)) for x in k) == key:
            return row.get("odds")
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill confirmed trifecta/exacta payoff odds for ROI.")
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--since", default="", help="この日付以降のレースだけ(YYYY-MM-DD)")
    parser.add_argument("--refresh", action="store_true", help="保存済みも取り直す")
    args = parser.parse_args()

    saved = skipped = 0
    with connect() as conn:
        rows = conn.execute(
            """
            select race_key, source_url, race_date, result_json, payouts_json
            from races
            where result_json is not null and result_json != ''
              and source_url like 'https://www.winticket.jp/%'
            order by fetched_at desc
            """
        ).fetchall()
        targets = []
        for row in rows:
            if args.since and _iso(row["race_date"]) < args.since:
                continue
            if not args.refresh and row["payouts_json"]:
                continue
            targets.append(row)
            if len(targets) >= args.limit:
                break

        for idx, row in enumerate(targets, start=1):
            try:
                order = (json.loads(row["result_json"]) or {}).get("finish_order") or []
                if len(order) < 3:
                    skipped += 1
                    continue
                tri_key = "-".join(str(c) for c in order[:3])
                ex_key = "-".join(str(c) for c in order[:2])
                html = fetch_url(odds_url_from_racecard(row["source_url"]))
                odds = state_queries(html).get("FETCH_KEIRIN_RACE_ODDS", {})
                payouts = {}
                tri = _winning_odds(odds.get("trifecta"), tri_key)
                ex = _winning_odds(odds.get("exacta"), ex_key)
                if tri:
                    payouts["trifecta"] = float(tri)
                if ex:
                    payouts["exacta"] = float(ex)
                if payouts:
                    save_race_payouts(conn, row["race_key"], payouts)
                    saved += 1
                else:
                    skipped += 1
            except Exception:
                skipped += 1
            if idx % 25 == 0 or idx == len(targets):
                print(f"{idx}/{len(targets)} payouts saved={saved} skipped={skipped}", flush=True)
            if idx < len(targets):
                time.sleep(max(0.2, args.delay))

    print(json.dumps({"saved": saved, "skipped": skipped}, ensure_ascii=False))


if __name__ == "__main__":
    main()
