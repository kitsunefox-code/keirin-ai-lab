from __future__ import annotations

"""オリジナル運用(株式運用型・複利)の自動決済。

毎日の自動更新から呼ばれる。稼働中セッションの「予定レース」を start_time 順に見て、
結果が確定したレースだけを AI の実際の予想(2車単・軸1着固定の相手上位2点)で
ペーパー購入 → 実際の着順と確定オッズ(払戻)で勝敗・払戻を記録し、残高を複利で増減させる。

方針(重要):
- 的中判定・払戻は「実際の着順」と「確定オッズ(races.payouts_json)」だけを使う。捏造・水増しはしない。
- 勝った買い目のオッズが未取得のレースは決済せず保留(次回 backfill 後に決済)。ズレた記録を残さない。
- 冪等: 既に記録済みのレースは飛ばす。start_time 順に、未確定レースに当たったら停止(複利の順序を守る)。
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
    UNIT,
    active_session,
    commit_bet,
    record_result,
    record_skip,
    session_state,
)
from keirin_ai.storage import connect

JST = timezone(timedelta(hours=9))


def _first_ranking(conn, race_key: str) -> list[dict] | None:
    """そのレースに最初に保存された予想(レース前のAIの答え)の車順。"""
    row = conn.execute(
        "select ranking_json from predictions where race_key=? order by id asc limit 1",
        (race_key,),
    ).fetchone()
    if not row or not row["ranking_json"]:
        return None
    try:
        ranking = json.loads(row["ranking_json"])
    except Exception:
        return None
    return ranking or None


def _race_result(conn, race_key: str) -> tuple[list[int] | None, dict | None]:
    row = conn.execute(
        "select result_json, payouts_json from races where race_key=?",
        (race_key,),
    ).fetchone()
    if not row:
        return None, None
    order = None
    if row["result_json"]:
        try:
            order = [int(c) for c in (json.loads(row["result_json"]) or {}).get("finish_order", [])]
        except Exception:
            order = None
    payouts = None
    if row["payouts_json"]:
        try:
            payouts = json.loads(row["payouts_json"])
        except Exception:
            payouts = None
    return order, payouts


def _exacta_tickets(ranking: list[dict], budget: int, weights) -> tuple[list[dict], int]:
    """軸(1位)1着固定 × 相手(2位・3位)の2車単2点に、残高予算を配分する。"""
    cars = [int(r.get("car_no") or 0) for r in ranking[:3]]
    if len(cars) < 3 or 0 in cars:
        return [], 0
    combos = [(cars[0], cars[1]), (cars[0], cars[2])]
    total_units = budget // UNIT
    if total_units < 2:
        return [], 0
    w = [float(pair[1]) for pair in weights[:2]] or [0.5, 0.3]
    wsum = sum(w) or 1.0
    units0 = max(1, int(total_units * w[0] / wsum))
    units1 = max(1, total_units - units0)
    tickets = []
    for (a, b), units, role in zip(combos, (units0, units1), ("本命", "対抗")):
        tickets.append(
            {
                "role": role,
                "label": f"{a}-{b}",
                "cars": [a, b],
                "stake": units * UNIT,
                "bet_type": "exacta",
            }
        )
    total_stake = sum(t["stake"] for t in tickets)
    return tickets, total_stake


def main() -> None:
    settled = skipped = held = 0
    with connect() as conn:
        session = active_session(conn)
        if not session:
            print(json.dumps({"ok": True, "action": "no-active-session"}, ensure_ascii=False))
            return
        config = session["config"]
        style = STYLES.get(config.get("style") or "original") or STYLES["original"]
        weights = style.get("weights") or [("本命", 0.5), ("対抗", 0.3)]
        plan = session.get("plan") or {}
        slots = plan.get("slots") or []
        if not slots:
            print(json.dumps({"ok": True, "action": "no-plan"}, ensure_ascii=False))
            return

        # 既に記録済みのレースを把握(冪等)
        recorded = {
            row["race_key"]
            for row in conn.execute(
                "select race_key from bankroll_bets where session_id=?", (session["id"],)
            ).fetchall()
        }

        for slot in slots:
            race_key = slot.get("race_key")
            if not race_key or race_key in recorded:
                continue
            order, payouts = _race_result(conn, race_key)
            if not order or len(order) < 2:
                # まだ結果が出ていない → 複利の順序を守るためここで停止
                break

            ranking = _first_ranking(conn, race_key)
            state = session_state(conn, session)
            balance = state["balance"]
            budget = int(balance * config["per_race_cap_pct"] / 100 // UNIT * UNIT)

            race_meta = {
                "race_key": race_key,
                "venue": slot.get("venue") or "",
                "race_no": slot.get("race_no"),
                "start_time": slot.get("start_time") or "",
                "url": slot.get("url") or "",
            }

            tickets, total_stake = ([], 0)
            if ranking:
                tickets, total_stake = _exacta_tickets(ranking, budget, weights)
            if not tickets:
                record_skip(conn, session["id"], race_meta, "予算内で2点を買えず見送り")
                recorded.add(race_key)
                skipped += 1
                continue

            actual2 = [order[0], order[1]]
            win_ticket = next((t for t in tickets if t["cars"] == actual2), None)
            payoff_odds = (payouts or {}).get("exacta")

            if win_ticket is not None and payoff_odds is None:
                # 的中したのに確定オッズ未取得 → 捏造しないため決済保留(次回に回す)
                held += 1
                break

            proposal = {**race_meta, "tickets": tickets, "total_stake": total_stake, "bet_type": "exacta"}
            bet_id = commit_bet(conn, session["id"], proposal)
            if win_ticket is not None:
                payout = int(round(win_ticket["stake"] * float(payoff_odds)))
                record_result(conn, bet_id, "won", max(1, payout))
            else:
                record_result(conn, bet_id, "lost", 0)
            recorded.add(race_key)
            settled += 1

        final = session_state(conn, session)
    print(
        json.dumps(
            {
                "ok": True,
                "session_id": session["id"],
                "settled": settled,
                "skipped": skipped,
                "held_no_odds": held,
                "balance": final["balance"],
                "profit": final["profit"],
                "wins": final["wins"],
                "losses": final["losses"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
