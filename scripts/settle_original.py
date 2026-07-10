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
    choose_bet_type,
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


def _build_tickets(ranking: list[dict], budget: int, bet_type: str, points: int = 4) -> tuple[list[dict], int]:
    """券種に応じた買い目に残高予算を配分する。

    2車単: 軸(1位)1着固定 × 相手(2位・3位)の2点。
    3連単: 軸1着固定のスジを points 点まで(1着=1位, 2着=2/3位, 3着=2〜5位)。
      堅実3点 / バランス5点 / 冒険6点 / オリジナル自信時4点。
    """
    cars = [int(r.get("car_no") or 0) for r in ranking[:5]]
    total_units = budget // UNIT
    if len(cars) < 3 or 0 in cars[:3]:
        return [], 0

    if bet_type == "trifecta":
        r1 = cars[0]
        seconds = [cars[1], cars[2]]
        thirds = [c for c in cars[1:5] if c]
        combos = []
        # 近い順(番手→3番手)にスジを積む
        for b in seconds:
            for c in thirds:
                combo = (r1, b, c)
                if len(set(combo)) == 3 and combo not in combos:
                    combos.append(combo)
        combos = combos[: max(2, points)]
        base_w = [0.30, 0.22, 0.18, 0.14, 0.10, 0.06]
        weights = base_w[: len(combos)]
    else:
        combos = [(cars[0], cars[1]), (cars[0], cars[2])]
        weights = [0.62, 0.38]

    if total_units < len(combos):
        # 点数を買えるだけに絞る(最低2点)
        keep = max(2, total_units)
        combos = combos[:keep]
        weights = weights[:keep]
    if total_units < len(combos) or len(combos) < 2:
        return [], 0

    wsum = sum(weights) or 1.0
    units = [max(1, int(total_units * w / wsum)) for w in weights]
    # 端数を本線へ寄せる
    diff = total_units - sum(units)
    if diff > 0:
        units[0] += diff
    tickets = []
    for combo, u in zip(combos, units):
        tickets.append(
            {
                "label": "-".join(str(c) for c in combo),
                "cars": list(combo),
                "stake": u * UNIT,
                "bet_type": bet_type,
            }
        )
    return tickets, sum(t["stake"] for t in tickets)


def _style_bet(config: dict, ranking: list, order: list, budget: int):
    """セッションのスタイルに応じて (bet_type, need, tickets, total_stake) を決める。

    オリジナル(main): 自信レースは3連単スジ4点、それ以外は2車単2点。
    堅実/バランス/冒険(compare): 常に3連単スジ、点数はスタイル固有(3/5/6)。
    """
    style_key = config.get("style") or "original"
    if style_key == "original":
        top_prob = float((ranking[0].get("win_probability") if ranking else 0) or 0)
        bet_type = choose_bet_type(top_prob)
        points = 4
    else:
        bet_type = "trifecta"
        points = {"kenjitsu": 3, "balance": 5, "bouken": 6}.get(style_key, 4)
    need = 3 if bet_type == "trifecta" else 2
    if not ranking or len(order) < need:
        return bet_type, need, [], 0
    tickets, total_stake = _build_tickets(ranking, budget, bet_type, points=points)
    return bet_type, need, tickets, total_stake


def _settle_session(conn, session: dict) -> dict:
    """1セッションの予定レースを実結果+確定オッズで決済し、集計を返す。"""
    config = session["config"]
    slots = (session.get("plan") or {}).get("slots") or []
    settled = skipped = held = 0
    if not slots:
        final = session_state(conn, session)
        return {"session_id": session["id"], "style": config.get("style"), "settled": 0, "skipped": 0,
                "balance": final["balance"], "profit": final["profit"], "wins": final["wins"], "losses": final["losses"]}

    recorded = {
        row["race_key"]
        for row in conn.execute("select race_key from bankroll_bets where session_id=?", (session["id"],)).fetchall()
    }
    for slot in slots:
        race_key = slot.get("race_key")
        if not race_key or race_key in recorded:
            continue
        order, payouts = _race_result(conn, race_key)
        if not order or len(order) < 2:
            break  # 複利の順序を守るため未確定で停止
        ranking = _first_ranking(conn, race_key)
        state = session_state(conn, session)
        budget = int(state["balance"] * config["per_race_cap_pct"] / 100 // UNIT * UNIT)
        race_meta = {
            "race_key": race_key,
            "venue": slot.get("venue") or "",
            "race_no": slot.get("race_no"),
            "start_time": slot.get("start_time") or "",
            "url": slot.get("url") or "",
        }
        bet_type, need, tickets, total_stake = _style_bet(config, ranking, order, budget)
        if not tickets:
            if bet_type == "trifecta" and len(order) < 3:
                break
            record_skip(conn, session["id"], race_meta, "予算内で必要点数を買えず見送り")
            recorded.add(race_key)
            skipped += 1
            continue
        actual = order[:need]
        win_ticket = next((t for t in tickets if t["cars"] == actual), None)
        payoff_odds = (payouts or {}).get(bet_type)
        if win_ticket is not None and payoff_odds is None:
            held += 1
            break  # 的中だがオッズ未取得 → 捏造しない
        proposal = {**race_meta, "tickets": tickets, "total_stake": total_stake, "bet_type": bet_type}
        bet_id = commit_bet(conn, session["id"], proposal)
        if win_ticket is not None:
            payout = int(round(win_ticket["stake"] * float(payoff_odds)))
            record_result(conn, bet_id, "won", max(1, payout))
        else:
            record_result(conn, bet_id, "lost", 0)
        recorded.add(race_key)
        settled += 1

    final = session_state(conn, session)
    return {"session_id": session["id"], "style": config.get("style"), "settled": settled, "skipped": skipped,
            "held_no_odds": held, "balance": final["balance"], "profit": final["profit"],
            "wins": final["wins"], "losses": final["losses"]}


def main() -> None:
    from keirin_ai.bankroll import active_compare_sessions

    with connect() as conn:
        session = active_session(conn)
        sessions = ([session] if session else []) + active_compare_sessions(conn)
        if not sessions:
            print(json.dumps({"ok": True, "action": "no-active-session"}, ensure_ascii=False))
            return
        results = [_settle_session(conn, s) for s in sessions]
    print(json.dumps({"ok": True, "sessions": results}, ensure_ascii=False))


if __name__ == "__main__":
    main()
