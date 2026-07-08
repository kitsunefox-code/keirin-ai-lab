from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from keirin_ai.capital_plan import build_capital_plan_payload
from keirin_ai.forecast_view import build_today_forecast_payload
from keirin_ai.predictor import predict_race
from keirin_ai.storage import connect, learning_status


DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "app" / "static-api"


def write_json(name: str, payload: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    with connect() as conn:
        today = build_today_forecast_payload(conn, DATA_DIR)
        status = learning_status(conn)
        today["learning_status"] = status
        capital = build_capital_plan_payload(
            conn,
            DATA_DIR,
            start_amount=1000,
            target_amount=3000,
            max_races=2,
            live_odds=False,
            max_live_fetch=0,
        )

    sample_race = json.loads((DATA_DIR / "sample_race.json").read_text(encoding="utf-8"))
    write_json("today.json", today)
    write_json("sample.json", {"ok": True, "race": sample_race, "prediction": predict_race(sample_race)})
    write_json("learn-status.json", {"ok": True, "status": status})
    write_json("capital-plan.json", capital)


if __name__ == "__main__":
    main()
