from __future__ import annotations

"""保存済みレースのWINTICKET raceresultページから、確定結果・決まり手・
レース後インタビューを回収して学習とレース後談話DB(player_form)を更新する。

python scripts\\collect_raceresults.py --limit 80 --delay 0.6
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.learner import train_win_model
from keirin_ai.predictor import predict_race
from keirin_ai.sources import fetch_url, parse_winticket_racecard
from keirin_ai.storage import connect, learning_status, save_player_form, save_race
from keirin_ai.winticket_state import enrich_race_from_state, raceresult_url_from_racecard


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect finished-race results and post-race interviews from WINTICKET.")
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--delay", type=float, default=0.6)
    parser.add_argument("--include-finished", action="store_true", help="結果保存済みでも談話回収のため再訪する")
    parser.add_argument("--out", default="data/raceresult_collect_log.json")
    args = parser.parse_args()

    updated, comments, skipped = [], 0, []
    with connect() as conn:
        rows = _target_races(conn, args.limit, args.include_finished)
        for idx, row in enumerate(rows, start=1):
            url = row["source_url"]
            try:
                # 出走表ページで選手情報、raceresultページで結果とレース後談話を取る
                card_html = fetch_url(url)
                race = parse_winticket_racecard(card_html, url)
                race = enrich_race_from_state(race, card_html)
                if not race.get("entrants"):
                    skipped.append({"race_key": row["race_key"], "reason": "no entrants"})
                    continue
                time.sleep(max(0.2, args.delay / 2))
                result_html = fetch_url(raceresult_url_from_racecard(url))
                race = enrich_race_from_state(race, result_html)
                if not race.get("result"):
                    skipped.append({"race_key": row["race_key"], "reason": "result not decided"})
                    continue
                prediction = predict_race(race)
                key = save_race(conn, race, prediction)
                comments += save_player_form(conn, _form_rows(key, race))
                updated.append({"race_key": key, "finish_order": race["result"].get("finish_order", [])})
            except Exception as exc:
                skipped.append({"race_key": row["race_key"], "reason": str(exc)[:120]})
            if idx < len(rows):
                time.sleep(max(0.2, args.delay))
        status = learning_status(conn)

    model = train_win_model()
    payload = {
        "checked": len(rows),
        "updated_count": len(updated),
        "player_form_saved": comments,
        "updated": updated,
        "skipped_count": len(skipped),
        "skipped": skipped[:80],
        "status": status,
        "model_training": model.get("training", {}),
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("checked", "updated_count", "player_form_saved", "skipped_count")}, ensure_ascii=False))


def _target_races(conn, limit: int, include_finished: bool):
    where = "source_url like 'https://www.winticket.jp/%'"
    if not include_finished:
        where += " and (result_json is null or result_json = '')"
    return conn.execute(
        f"""
        select race_key, source_url from races
        where {where}
        order by fetched_at desc
        limit ?
        """,
        (limit,),
    ).fetchall()


def _form_rows(race_key: str, race: dict) -> list[dict]:
    finish_by_car = {}
    factor_by_car = {}
    for item in race.get("results_detail") or []:
        finish_by_car[item["car_no"]] = item.get("order")
        factor_by_car[item["car_no"]] = item.get("factor") or ""
    positions = (race.get("result") or {}).get("positions") or {}
    rows = []
    for entrant in race.get("entrants", []):
        player_id = entrant.get("player_id")
        if not player_id:
            continue
        car_no = int(entrant.get("car_no") or 0)
        finish = finish_by_car.get(car_no) or positions.get(str(car_no))
        rows.append(
            {
                "player_id": player_id,
                "race_key": race_key,
                "name": entrant.get("name"),
                "race_date": race.get("date"),
                "finish": finish,
                "factor": factor_by_car.get(car_no, ""),
                "post_comment": entrant.get("post_race_comment") or "",
            }
        )
    return rows


if __name__ == "__main__":
    main()
