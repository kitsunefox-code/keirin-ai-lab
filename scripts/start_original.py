from __future__ import annotations

"""オリジナル運用(株式運用型・1万円×10レース)を自動開始する。

毎朝の自動更新から呼ばれる。冪等:
- 今日のオリジナルが既に稼働中なら何もしない
- 前日のセッションが残っていれば「日付変更」で停止してから開始
- 予定10レースを確定してplanに保存する
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.bankroll import (
    STYLES,
    BankrollConfig,
    active_session,
    build_original_plan,
    set_session_plan,
    start_compare_sessions,
    start_session,
    stop_session,
)
from keirin_ai.storage import connect

JST = timezone(timedelta(hours=9))


def main() -> None:
    from keirin_ai.bankroll import active_compare_sessions

    today = datetime.now(JST).date().isoformat()
    with connect() as conn:
        session = active_session(conn)
        if session and session["session_date"] == today and session["config"].get("style") == "original":
            # メインは稼働中。比較セッション(堅実/バランス/冒険)が無ければ同じ予定で作る。
            plan = session.get("plan") or {}
            start = int(session["config"].get("start_amount") or 10000)
            compares = 0
            if plan.get("slots") and not active_compare_sessions(conn):
                compares = len(start_compare_sessions(conn, plan, start))
            print(json.dumps({"ok": True, "action": "already-running", "session_id": session["id"], "compare_started": compares}, ensure_ascii=False))
            return
        if session:
            stop_session(conn, session["id"], "自動開始のため停止(日付変更/別スタイル)")

        style = STYLES["original"]
        start = int(style.get("default_start") or 10000)
        config = BankrollConfig.from_style("original", start, start * 100)
        session_id = start_session(conn, config)
        plan = build_original_plan(conn, ROOT / "data", int(style.get("race_limit") or 10))
        set_session_plan(conn, session_id, plan)
        # 同じ予定10レースで堅実・バランス・冒険を並行運用(比較用)
        compare_ids = start_compare_sessions(conn, plan, start)
        print(
            json.dumps(
                {
                    "ok": True,
                    "action": "started",
                    "session_id": session_id,
                    "start_amount": start,
                    "plan_races": len(plan.get("slots", [])),
                    "compare_started": len(compare_ids),
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
