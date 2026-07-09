from __future__ import annotations

"""保存済みレースを最新のAIロジックで予想し直し、予想と特徴量を上塗りする。

新しく追加した特徴量(バンク傾向・ライン位置別成績・級班・ナイター/天気など)は
過去に保存したレースには入っていないため、公開ページを取り直して再計算する。
各レースの古い予想は削除して新しい予想へ置き換える(答え合わせも最新版になる)。

python scripts\\repredict_saved.py --limit 600 --delay 0.35
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
from keirin_ai.storage import attach_line_partner_stats, connect, learning_status, race_key, save_race
from keirin_ai.winticket_state import enrich_race_from_state


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-predict saved races with the latest model and overwrite predictions.")
    parser.add_argument("--limit", type=int, default=600)
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--since", default="", help="この日付(YYYY-MM-DD)以降に開催されたレースだけ再予想する")
    parser.add_argument("--no-retrain", action="store_true", help="再学習をスキップする")
    parser.add_argument("--out", default="data/repredict_log.json")
    args = parser.parse_args()

    updated, skipped = [], []
    status = {}
    with connect() as conn:
        rows = _target_races(conn, args.limit, args.since)
        total = len(rows)
        for idx, row in enumerate(rows, start=1):
            url = row["source_url"]
            try:
                html = fetch_url(url)
                race = parse_winticket_racecard(html, url)
                race = enrich_race_from_state(race, html)
                if len(race.get("entrants", [])) < 2:
                    skipped.append({"race_key": row["race_key"], "reason": "no entrants"})
                    continue
                attach_line_partner_stats(conn, race)
                prediction = predict_race(race)
                key = race_key(race)
                # 古い予想を消してから保存 = 答え合わせが最新モデルの予想を参照する
                conn.execute("delete from predictions where race_key=?", (key,))
                save_race(conn, race, prediction)
                top = (prediction.get("rankings") or [{}])[0]
                updated.append({"race_key": key, "top_car": top.get("car_no")})
            except Exception as exc:
                skipped.append({"race_key": row["race_key"], "reason": str(exc)[:120]})
            if idx % 25 == 0 or idx == total:
                print(f"{idx}/{total} re-predicted (updated {len(updated)} / skip {len(skipped)})", flush=True)
            if idx < total:
                time.sleep(max(0.2, args.delay))
        status = learning_status(conn)

    model = {}
    if not args.no_retrain and updated:
        model = train_win_model()

    payload = {
        "checked": len(rows),
        "updated_count": len(updated),
        "skipped_count": len(skipped),
        "skipped": skipped[:80],
        "status": status,
        "model_name": model.get("name"),
        "model_metrics": model.get("metrics", {}),
        "model_training": model.get("training", {}),
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("checked", "updated_count", "skipped_count", "model_name", "model_metrics")}, ensure_ascii=False))


def _target_races(conn, limit: int, since: str):
    rows = conn.execute(
        "select race_key, source_url, race_date from races "
        "where source_url like 'https://www.winticket.jp/%' order by fetched_at desc"
    ).fetchall()
    if since:
        rows = [row for row in rows if _iso_date(row["race_date"]) >= since]
    return rows[:limit]


def _iso_date(raw: str | None) -> str:
    import re

    raw = str(raw or "")
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw) or re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", raw)
    if not m:
        return "0000-00-00"
    return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"


if __name__ == "__main__":
    main()
