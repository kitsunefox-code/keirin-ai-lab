from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from keirin_ai.capital_plan import (
    _estimated_odds,
    _float,
    _future_forecasts,
    _hit_probability,
    _round_yen,
)
from keirin_ai.forecast_view import build_today_forecast_payload


JST = timezone(timedelta(hours=9))
UNIT = 100

# 資金管理の固定ルール。設定でも緩められない。
HARD_RULES = [
    "全額転がしは行いません(1レース投資は残高の上限%まで)",
    "負けた後の倍賭け(マーチンゲール)は行いません",
    "期待値が下限未満のレースは見送ります",
    "1点勝負はせず、本線・抑え・妙味に分散します",
    "自動購入は行いません。買い目コピーと購入前確認までです",
]


@dataclass
class BankrollConfig:
    start_amount: int = 1000
    target_amount: int = 3000
    per_race_cap_pct: int = 20
    daily_loss_limit_pct: int = 30
    max_consecutive_losses: int = 3
    min_ev: float = 1.0
    auto_buy: bool = False  # 初期実装では常にOFF。公式APIがないため自動購入は実装しない。

    def normalized(self) -> "BankrollConfig":
        start = max(300, int(self.start_amount or 0))
        target = max(start + UNIT, int(self.target_amount or 0))
        return BankrollConfig(
            start_amount=start,
            target_amount=target,
            per_race_cap_pct=max(5, min(50, int(self.per_race_cap_pct or 20))),
            daily_loss_limit_pct=max(10, min(80, int(self.daily_loss_limit_pct or 30))),
            max_consecutive_losses=max(1, min(10, int(self.max_consecutive_losses or 3))),
            min_ev=max(0.5, min(2.0, float(self.min_ev or 1.0))),
            auto_buy=False,
        )


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists bankroll_sessions (
            id integer primary key autoincrement,
            created_at text not null,
            session_date text not null,
            status text not null default 'active',
            stop_reason text,
            config_json text not null
        );

        create table if not exists bankroll_bets (
            id integer primary key autoincrement,
            session_id integer not null,
            race_key text,
            venue text,
            race_no integer,
            start_time text,
            url text,
            tickets_json text not null,
            total_stake integer not null,
            status text not null default 'pending',
            payout integer not null default 0,
            note text,
            created_at text not null,
            settled_at text
        );
        """
    )
    conn.commit()


def start_session(conn: sqlite3.Connection, config: BankrollConfig) -> int:
    ensure_tables(conn)
    config = config.normalized()
    now = datetime.now(JST)
    conn.execute(
        "update bankroll_sessions set status='stopped', stop_reason=coalesce(stop_reason, '新しいセッション開始') where status='active'"
    )
    cursor = conn.execute(
        "insert into bankroll_sessions (created_at, session_date, status, config_json) values (?, ?, 'active', ?)",
        (now.isoformat(timespec="seconds"), now.date().isoformat(), json.dumps(asdict(config), ensure_ascii=False)),
    )
    conn.commit()
    return int(cursor.lastrowid)


def stop_session(conn: sqlite3.Connection, session_id: int, reason: str) -> None:
    ensure_tables(conn)
    conn.execute(
        "update bankroll_sessions set status='stopped', stop_reason=? where id=? and status='active'",
        (reason or "手動停止", session_id),
    )
    conn.commit()


def active_session(conn: sqlite3.Connection) -> dict | None:
    ensure_tables(conn)
    row = conn.execute(
        "select * from bankroll_sessions where status='active' order by id desc limit 1"
    ).fetchone()
    if not row:
        return None
    return _session_row(row)


def latest_session(conn: sqlite3.Connection) -> dict | None:
    ensure_tables(conn)
    row = conn.execute("select * from bankroll_sessions order by id desc limit 1").fetchone()
    return _session_row(row) if row else None


def commit_bet(conn: sqlite3.Connection, session_id: int, proposal: dict) -> int:
    ensure_tables(conn)
    pending = conn.execute(
        "select id from bankroll_bets where session_id=? and status='pending'", (session_id,)
    ).fetchone()
    if pending:
        raise ValueError("結果待ちの購入記録があります。先に結果を入力してください。")
    tickets = proposal.get("tickets") or []
    total_stake = int(proposal.get("total_stake") or sum(int(t.get("stake") or 0) for t in tickets))
    if total_stake <= 0 or not tickets:
        raise ValueError("買い目と投資額が不正です。")
    cursor = conn.execute(
        """
        insert into bankroll_bets (
            session_id, race_key, venue, race_no, start_time, url,
            tickets_json, total_stake, status, created_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """,
        (
            session_id,
            proposal.get("race_key"),
            proposal.get("venue"),
            proposal.get("race_no"),
            proposal.get("start_time"),
            proposal.get("url"),
            json.dumps(tickets, ensure_ascii=False),
            total_stake,
            datetime.now(JST).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def record_skip(conn: sqlite3.Connection, session_id: int, race: dict, reason: str) -> None:
    ensure_tables(conn)
    conn.execute(
        """
        insert into bankroll_bets (
            session_id, race_key, venue, race_no, start_time, url,
            tickets_json, total_stake, status, note, created_at, settled_at
        ) values (?, ?, ?, ?, ?, ?, '[]', 0, 'skipped', ?, ?, ?)
        """,
        (
            session_id,
            race.get("race_key"),
            race.get("venue"),
            race.get("race_no"),
            race.get("start_time"),
            race.get("url"),
            reason or "見送り",
            datetime.now(JST).isoformat(timespec="seconds"),
            datetime.now(JST).isoformat(timespec="seconds"),
        ),
    )
    conn.commit()


def record_result(conn: sqlite3.Connection, bet_id: int, outcome: str, payout: int) -> dict:
    ensure_tables(conn)
    row = conn.execute("select * from bankroll_bets where id=?", (bet_id,)).fetchone()
    if not row:
        raise ValueError("購入記録が見つかりません。")
    if row["status"] != "pending":
        raise ValueError("この購入記録は精算済みです。")
    if outcome not in {"won", "lost"}:
        raise ValueError("結果は won / lost で指定してください。")
    payout = max(0, int(payout or 0)) if outcome == "won" else 0
    if outcome == "won" and payout <= 0:
        raise ValueError("的中時は払戻額を入力してください。")
    conn.execute(
        "update bankroll_bets set status=?, payout=?, settled_at=? where id=?",
        (outcome, payout, datetime.now(JST).isoformat(timespec="seconds"), bet_id),
    )
    conn.commit()

    session_row = conn.execute(
        "select * from bankroll_sessions where id=?", (row["session_id"],)
    ).fetchone()
    session = _session_row(session_row)
    state = session_state(conn, session)
    stop_reason = evaluate_stop(session["config"], state)
    if stop_reason and session["status"] == "active":
        stop_session(conn, session["id"], stop_reason)
    return {"state": state, "stop_reason": stop_reason}


def session_state(conn: sqlite3.Connection, session: dict) -> dict:
    bets = [
        _bet_row(row)
        for row in conn.execute(
            "select * from bankroll_bets where session_id=? order by id", (session["id"],)
        ).fetchall()
    ]
    config = session["config"]
    balance = config["start_amount"]
    settled_wins = 0
    settled_losses = 0
    pending = None
    consecutive_losses = 0
    for bet in bets:
        if bet["status"] == "pending":
            balance -= bet["total_stake"]
            pending = bet
        elif bet["status"] == "won":
            balance += bet["payout"] - bet["total_stake"]
            settled_wins += 1
            consecutive_losses = 0
        elif bet["status"] == "lost":
            balance -= bet["total_stake"]
            settled_losses += 1
            consecutive_losses += 1
    day_loss = max(0, config["start_amount"] - balance)
    return {
        "balance": balance,
        "profit": balance - config["start_amount"],
        "day_loss": day_loss,
        "day_loss_limit": _round_yen(config["start_amount"] * config["daily_loss_limit_pct"] / 100),
        "consecutive_losses": consecutive_losses,
        "wins": settled_wins,
        "losses": settled_losses,
        "skips": sum(1 for bet in bets if bet["status"] == "skipped"),
        "pending_bet": pending,
        "bets": bets,
        "target_progress": min(1.0, balance / max(1, config["target_amount"])),
    }


def evaluate_stop(config: dict, state: dict) -> str | None:
    # 停止条件は結果確定分(残高)で判定する。pending中は判定を保留しない。
    if state["balance"] >= config["target_amount"]:
        return f"目標達成({state['balance']}円 ≥ {config['target_amount']}円)"
    if state["day_loss"] >= state["day_loss_limit"] and state["day_loss_limit"] > 0:
        return f"1日損失上限({state['day_loss']}円 ≥ {state['day_loss_limit']}円)"
    if state["consecutive_losses"] >= config["max_consecutive_losses"]:
        return f"{state['consecutive_losses']}連敗のため停止"
    return None


def build_bankroll_payload(conn: sqlite3.Connection, data_dir: Path | str) -> dict:
    ensure_tables(conn)
    session = active_session(conn)
    payload: dict = {
        "ok": True,
        "generated_at_jst": datetime.now(JST).isoformat(timespec="seconds"),
        "rules": HARD_RULES,
        "auto_buy_available": False,
        "session": None,
        "state": None,
        "proposal": None,
        "judged_races": [],
    }
    if session is None:
        last = latest_session(conn)
        if last and last["status"] == "stopped":
            payload["last_session"] = {
                "stop_reason": last.get("stop_reason"),
                "config": last["config"],
                "state": session_state(conn, last),
            }
        return payload

    config = session["config"]
    state = session_state(conn, session)
    stop_reason = evaluate_stop(config, state)
    if stop_reason:
        stop_session(conn, session["id"], stop_reason)
        session["status"] = "stopped"
        session["stop_reason"] = stop_reason

    payload["session"] = session
    payload["state"] = state

    if session["status"] != "active":
        return payload
    if state["pending_bet"]:
        payload["message"] = "結果待ちの購入記録があります。レース確定後に結果を入力してください。"
        return payload

    judged, proposal = _judge_races(conn, data_dir, config, state)
    payload["judged_races"] = judged
    payload["proposal"] = proposal
    if proposal is None and not judged:
        payload["message"] = "本日はこれから発走する対象レースがありません。"
    return payload


def _judge_races(conn, data_dir, config: dict, state: dict) -> tuple[list[dict], dict | None]:
    now_jst = datetime.now(JST)
    today = build_today_forecast_payload(conn, data_dir)
    active, _ = _future_forecasts(today.get("forecasts", []), now_jst + timedelta(minutes=2))
    active.sort(key=lambda race: race.get("start_time") or "99:99")

    handled_keys = {bet["race_key"] for bet in state["bets"] if bet["race_key"]}
    budget = _race_budget(state["balance"], config)

    judged: list[dict] = []
    proposal: dict | None = None
    for race in active[:12]:
        if race.get("race_key") in handled_keys:
            continue
        verdict = _judge_race(race, budget, config)
        judged.append(verdict["summary"])
        if proposal is None and verdict["proposal"] is not None:
            proposal = verdict["proposal"]
            verdict["summary"]["is_next"] = True
    return judged, proposal


def _race_budget(balance: int, config: dict) -> int:
    cap = int(balance * config["per_race_cap_pct"] / 100 // UNIT * UNIT)
    return max(0, min(cap, balance))


def _judge_race(race: dict, budget: int, config: dict) -> dict:
    summary = {
        "race_key": race.get("race_key"),
        "venue": race.get("venue") or "",
        "race_no": race.get("race_no"),
        "start_time": race.get("start_time") or "",
        "confidence": (race.get("confidence") or {}).get("label") or "混戦",
        "decision": "skip",
        "reason": "",
        "ev": None,
        "is_next": False,
    }
    candidates = _race_tickets(race)
    if len(candidates) < 2:
        summary["reason"] = "買い目候補が不足(1点勝負は行わない)"
        return {"summary": summary, "proposal": None}
    if budget < UNIT * 2:
        summary["reason"] = f"1レース上限({config['per_race_cap_pct']}%)内で最低2点を買えません"
        return {"summary": summary, "proposal": None}

    allocation = _allocate(budget, candidates)
    total_stake = sum(t["stake"] for t in allocation)
    expected = sum(t["stake"] * t["odds"] * t["hit_probability"] for t in allocation)
    ev = expected / max(1, total_stake)
    summary["ev"] = round(ev, 3)
    if ev < config["min_ev"]:
        summary["reason"] = f"期待値不足(EV {ev:.2f} < {config['min_ev']:.2f})"
        return {"summary": summary, "proposal": None}

    summary["decision"] = "bet"
    summary["reason"] = f"EV {ev:.2f} / {len(allocation)}点分散"
    proposal = {
        "race_key": race.get("race_key"),
        "venue": race.get("venue") or "",
        "race_no": race.get("race_no"),
        "start_time": race.get("start_time") or "",
        "race_date": race.get("race_date") or "",
        "url": race.get("url") or "",
        "confidence": summary["confidence"],
        "budget": budget,
        "total_stake": total_stake,
        "ev": round(ev, 3),
        "expected_return": _round_yen(expected),
        "tickets": allocation,
        "top_pick": (race.get("top3") or [{}])[0],
        "scenario_headline": (race.get("scenario") or {}).get("headline") or "",
        "copy_text": _copy_text(race, allocation),
    }
    return {"summary": summary, "proposal": proposal}


def _race_tickets(race: dict) -> list[dict]:
    confidence = race.get("confidence") or {}
    rank = int(confidence.get("rank") or 1)
    has_signals = bool(race.get("comment_signals"))
    tickets = []
    for index, ticket in enumerate((race.get("tickets") or [])[:4]):
        label = ticket.get("label") if isinstance(ticket, dict) else str(ticket)
        if not label:
            continue
        score = _float(ticket.get("score") if isinstance(ticket, dict) else None, default=0.18)
        odds = _estimated_odds(race, score, index)
        tickets.append(
            {
                "label": label,
                "odds": odds,
                "hit_probability": _hit_probability(score, rank, has_signals),
                "ticket_rank": index + 1,
            }
        )
    return tickets


def _allocate(budget: int, candidates: list[dict]) -> list[dict]:
    main = candidates[0]
    cover = candidates[1]
    value = None
    if len(candidates) > 2:
        value = max(candidates[2:], key=lambda t: t["odds"] * t["hit_probability"])

    if value is not None and budget >= UNIT * 3:
        roles = [("本線", main, 0.5), ("抑え", cover, 0.3), ("妙味", value, 0.2)]
    else:
        roles = [("本線", main, 0.65), ("抑え", cover, 0.35)]

    total_units = budget // UNIT
    allocation = []
    used_units = 0
    for role, ticket, weight in roles:
        units = max(1, int(total_units * weight))
        allocation.append(
            {
                "role": role,
                "label": ticket["label"],
                "stake": units * UNIT,
                "odds": ticket["odds"],
                "hit_probability": ticket["hit_probability"],
                "projected_return": _round_yen(units * UNIT * ticket["odds"]),
            }
        )
        used_units += units
    # 端数は本線に寄せる。予算超過した場合は妙味→抑えの順に削る。
    idx = len(allocation) - 1
    while used_units > total_units and idx > 0:
        if allocation[idx]["stake"] > UNIT:
            allocation[idx]["stake"] -= UNIT
            used_units -= 1
        else:
            idx -= 1
    if used_units < total_units:
        allocation[0]["stake"] += (total_units - used_units) * UNIT
    for ticket in allocation:
        ticket["projected_return"] = _round_yen(ticket["stake"] * ticket["odds"])
    return allocation


def _copy_text(race: dict, allocation: list[dict]) -> str:
    header = f"{race.get('venue') or ''}{race.get('race_no') or ''}R 3連単"
    lines = [header]
    for ticket in allocation:
        lines.append(f"{ticket['role']} {ticket['label']} {ticket['stake']}円")
    return "\n".join(lines)


def _session_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "session_date": row["session_date"],
        "status": row["status"],
        "stop_reason": row["stop_reason"],
        "config": json.loads(row["config_json"]),
    }


def _bet_row(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "race_key": row["race_key"],
        "venue": row["venue"],
        "race_no": row["race_no"],
        "start_time": row["start_time"],
        "url": row["url"],
        "tickets": json.loads(row["tickets_json"] or "[]"),
        "total_stake": row["total_stake"],
        "status": row["status"],
        "payout": row["payout"],
        "note": row["note"],
        "created_at": row["created_at"],
        "settled_at": row["settled_at"],
    }
