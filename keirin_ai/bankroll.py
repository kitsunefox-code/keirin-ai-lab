from __future__ import annotations

import json
import math
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
from keirin_ai.odds import fetch_live_odds


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

# 運用スタイル。買い目配分・停止条件・レース選別基準をまとめて決める。
STYLES: dict[str, dict] = {
    "kenjitsu": {
        "key": "kenjitsu",
        "label": "堅実",
        "description": "信頼度の高いレースだけ、本線厚めの最大3点。損失は浅く止める。",
        "per_race_cap_pct": 10,
        "daily_loss_limit_pct": 20,
        "max_consecutive_losses": 2,
        "min_ev": 1.3,
        "weights": [("本線", 0.55), ("抑え", 0.3), ("妙味", 0.15)],
        "min_confidence_rank": 2,  # 混戦は見送る
        "assumed_main_odds": 4.5,
    },
    "balance": {
        "key": "balance",
        "label": "バランス",
        "description": "本線・抑え・妙味を最大5点に分散。標準の停止条件で回す。",
        "per_race_cap_pct": 20,
        "daily_loss_limit_pct": 30,
        "max_consecutive_losses": 3,
        "min_ev": 1.2,
        "weights": [("本線", 0.34), ("抑え", 0.24), ("妙味", 0.16), ("妙味2", 0.14), ("穴", 0.12)],
        "min_confidence_rank": 1,
        "assumed_main_odds": 7.0,
    },
    "bouken": {
        "key": "bouken",
        "label": "冒険",
        "description": "妙味・穴を厚めに最大6点。振れ幅と引き換えに高配当を取りにいく。",
        "per_race_cap_pct": 30,
        "daily_loss_limit_pct": 40,
        "max_consecutive_losses": 4,
        "min_ev": 1.1,
        "weights": [("本線", 0.28), ("抑え", 0.2), ("妙味", 0.16), ("妙味2", 0.14), ("穴", 0.12), ("大穴", 0.1)],
        "min_confidence_rank": 1,
        "assumed_main_odds": 12.0,
    },
    "original": {
        "key": "original",
        "label": "オリジナル",
        "description": "1万円を株の運用のように少しずつ増やす10レース。朝にAIが自信のあるレースだけを厳選して確定し、小さく張って複利で積み上げる(差し替えは変更履歴付き)。",
        "per_race_cap_pct": 12,
        "daily_loss_limit_pct": 25,
        "max_consecutive_losses": 4,
        "min_ev": 1.25,
        "weights": [("本線", 0.5), ("抑え", 0.3), ("妙味", 0.2)],
        "min_confidence_rank": 2,
        "assumed_main_odds": 5.5,
        "race_limit": 10,
        "default_start": 10000,
        "bet_type": "auto",  # レースごとに3連単/2車単を使い分ける
    },
}


def choose_bet_type(top_probability: float) -> str:
    """オリジナル運用の券種をレースの自信度で使い分ける。

    本命が抜けているレース(AI勝率が高い=スジで決まりやすい)は3連単を少点数で買い、
    高めの配当を取りにいく。それ以外は2車単で的中率を確保し、複利を安定させる。
    """
    return "trifecta" if float(top_probability or 0) >= 0.42 else "exacta"


def estimate_races(start_amount: int, target_amount: int, style_key: str) -> dict:
    """目標達成までのレース数目安。expected=EV下限ペース、fast=本線連続的中ペース。"""
    style = STYLES.get(style_key) or STYLES["balance"]
    cap = style["per_race_cap_pct"] / 100.0
    main_weight = style["weights"][0][1]

    def races(growth: float) -> int | None:
        if growth <= 0 or target_amount <= start_amount or start_amount <= 0:
            return None
        value = math.ceil(math.log(target_amount / start_amount) / math.log(1.0 + growth))
        return min(value, 99)

    return {
        "expected": races(cap * (style["min_ev"] - 1.0)),
        "fast": races(cap * (main_weight * style["assumed_main_odds"] - 1.0)),
    }


@dataclass
class BankrollConfig:
    start_amount: int = 1000
    target_amount: int = 3000
    style: str = "balance"
    per_race_cap_pct: int = 20
    daily_loss_limit_pct: int = 30
    max_consecutive_losses: int = 3
    min_ev: float = 1.2
    auto_buy: bool = False  # 初期実装では常にOFF。公式APIがないため自動購入は実装しない。

    @classmethod
    def from_style(cls, style: str, start_amount: int, target_amount: int) -> "BankrollConfig":
        preset = STYLES.get(style) or STYLES["balance"]
        return cls(
            start_amount=start_amount,
            target_amount=target_amount,
            style=preset["key"],
            per_race_cap_pct=preset["per_race_cap_pct"],
            daily_loss_limit_pct=preset["daily_loss_limit_pct"],
            max_consecutive_losses=preset["max_consecutive_losses"],
            min_ev=preset["min_ev"],
        )

    def normalized(self) -> "BankrollConfig":
        start = max(300, int(self.start_amount or 0))
        target = max(start + UNIT, int(self.target_amount or 0))
        style = self.style if self.style in STYLES else "balance"
        return BankrollConfig(
            start_amount=start,
            target_amount=target,
            style=style,
            per_race_cap_pct=max(5, min(50, int(self.per_race_cap_pct or 20))),
            daily_loss_limit_pct=max(10, min(80, int(self.daily_loss_limit_pct or 30))),
            max_consecutive_losses=max(1, min(10, int(self.max_consecutive_losses or 3))),
            min_ev=max(0.5, min(2.0, float(self.min_ev or 1.2))),
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
    columns = {row["name"] for row in conn.execute("pragma table_info(bankroll_sessions)").fetchall()}
    if "plan_json" not in columns:
        conn.execute("alter table bankroll_sessions add column plan_json text")
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


def set_session_plan(conn: sqlite3.Connection, session_id: int, plan: dict) -> None:
    ensure_tables(conn)
    conn.execute(
        "update bankroll_sessions set plan_json=? where id=?",
        (json.dumps(plan, ensure_ascii=False), session_id),
    )
    conn.commit()


def set_session_style(conn: sqlite3.Connection, session_id: int, style: str) -> dict:
    """運用中に乗り方(堅実/バランス/冒険)を切り替える。残高・購入履歴・元手はそのまま。

    レース上限つき(オリジナル)からの相互切替も可能。予定表(plan)は保持する。
    """
    ensure_tables(conn)
    row = conn.execute("select config_json from bankroll_sessions where id=?", (session_id,)).fetchone()
    if not row:
        raise ValueError("運用セッションが見つかりません。")
    config = json.loads(row["config_json"])
    preset = STYLES.get(style)
    if not preset:
        raise ValueError("不明な運用スタイルです。")
    config["style"] = preset["key"]
    config["per_race_cap_pct"] = preset["per_race_cap_pct"]
    config["daily_loss_limit_pct"] = preset["daily_loss_limit_pct"]
    config["max_consecutive_losses"] = preset["max_consecutive_losses"]
    config["min_ev"] = preset["min_ev"]
    conn.execute(
        "update bankroll_sessions set config_json=? where id=?",
        (json.dumps(config, ensure_ascii=False), session_id),
    )
    conn.commit()
    return config


def build_original_plan(conn: sqlite3.Connection, data_dir, race_limit: int) -> dict:
    """オリジナル運用: 朝のうちに本日勝負する10レースをAIが確定する。

    信頼度・本命確率・買い目スコアで採点した上位レースを、発走時刻順に並べて予定表にする。
    """
    now_jst = datetime.now(JST)
    today = build_today_forecast_payload(conn, data_dir)
    active, _ = _future_forecasts(today.get("forecasts", []), now_jst)

    def score(race: dict) -> float:
        confidence = race.get("confidence") or {}
        top = (race.get("top3") or [{}])[0]
        best_ticket = max((t.get("score") or 0) for t in (race.get("tickets") or [{"score": 0}]))
        return int(confidence.get("rank") or 0) * 10 + float(top.get("probability") or 0) * 8 + best_ticket * 4

    # 株運用型: まず信頼度「強・中」だけに絞って厳選。足りない分だけ次点で補う
    strong = [race for race in active if int((race.get("confidence") or {}).get("rank") or 0) >= 2]
    pool = sorted(strong, key=score, reverse=True)[: race_limit]
    if len(pool) < race_limit:
        rest = [race for race in active if race not in pool]
        pool += sorted(rest, key=score, reverse=True)[: race_limit - len(pool)]
    picked = pool
    picked.sort(key=lambda race: race.get("start_time") or "99:99")
    slots = [
        {
            "race_key": race.get("race_key"),
            "venue": race.get("venue") or "",
            "race_no": race.get("race_no"),
            "start_time": race.get("start_time") or "",
            "original": None,
            "changed_at": None,
        }
        for race in picked
    ]
    return {
        "locked_at": now_jst.isoformat(timespec="seconds"),
        "race_limit": race_limit,
        "slots": slots,
    }


def replace_plan_slot(conn: sqlite3.Connection, session: dict, slot_index: int, new_race: dict) -> dict:
    """予定レースを差し替える。元のレースを記録し「変更済み」と分かるようにする。"""
    plan = session.get("plan") or {}
    slots = plan.get("slots") or []
    if not (0 <= slot_index < len(slots)):
        raise ValueError("差し替え対象のレースが見つかりません。")
    slot = slots[slot_index]
    if any(s.get("race_key") == new_race.get("race_key") for s in slots):
        raise ValueError("そのレースはすでに予定に入っています。")
    original = slot.get("original") or {
        "race_key": slot.get("race_key"),
        "venue": slot.get("venue"),
        "race_no": slot.get("race_no"),
        "start_time": slot.get("start_time"),
    }
    slots[slot_index] = {
        "race_key": new_race.get("race_key"),
        "venue": new_race.get("venue") or "",
        "race_no": new_race.get("race_no"),
        "start_time": new_race.get("start_time") or "",
        "original": original,
        "changed_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    # 差し替え後も時刻順を保つ
    slots.sort(key=lambda s: s.get("start_time") or "99:99")
    plan["slots"] = slots
    set_session_plan(conn, session["id"], plan)
    session["plan"] = plan
    return plan


def _plan_with_status(plan: dict, state: dict, now_jst: datetime) -> list[dict]:
    """予定レース表に消化状況(予定/購入済/的中/不的中/見送り/未消化)を付ける。"""
    bets_by_key = {bet["race_key"]: bet for bet in state.get("bets", []) if bet.get("race_key")}
    slots = []
    next_marked = False
    for slot in (plan or {}).get("slots") or []:
        info = dict(slot)
        bet = bets_by_key.get(slot.get("race_key"))
        if bet:
            info["status"] = bet["status"]  # pending/won/lost/skipped
            info["profit"] = (bet.get("payout") or 0) - (bet.get("total_stake") or 0) if bet["status"] in {"won", "lost"} else None
        else:
            start = _slot_start_dt(slot, now_jst)
            if start and start <= now_jst:
                info["status"] = "missed"
            else:
                info["status"] = "planned"
                if not next_marked:
                    info["is_next"] = True
                    next_marked = True
        slots.append(info)
    return slots


def _slot_start_dt(slot: dict, now_jst: datetime) -> datetime | None:
    import re as _re

    match = _re.fullmatch(r"(\d{1,2}):(\d{2})", str(slot.get("start_time") or ""))
    if not match:
        return None
    return datetime(now_jst.year, now_jst.month, now_jst.day, int(match.group(1)), int(match.group(2)), tzinfo=JST)


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


def sessions_on_date(conn: sqlite3.Connection, iso_date: str) -> list[dict]:
    """指定日に運用した全セッションの結果をそれぞれ返す(スタイルごとに複数回運用していれば全件)。"""
    ensure_tables(conn)
    rows = conn.execute(
        "select * from bankroll_sessions where session_date=? order by id", (iso_date,)
    ).fetchall()
    results = []
    for row in rows:
        session = _session_row(row)
        state = session_state(conn, session)
        results.append(
            {
                "id": session["id"],
                "style": session["config"].get("style", "balance"),
                "style_label": STYLES.get(session["config"].get("style", "balance"), STYLES["balance"])["label"],
                "status": session["status"],
                "stop_reason": session.get("stop_reason"),
                "config": session["config"],
                "balance": state["balance"],
                "profit": state["profit"],
                "wins": state["wins"],
                "losses": state["losses"],
                "skips": state["skips"],
                "target_reached": state["balance"] >= session["config"]["target_amount"],
            }
        )
    return results


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
        "races_to_target": estimate_races(balance, config["target_amount"], config.get("style", "balance")),
    }


def daily_history(conn: sqlite3.Connection, days: int = 14) -> list[dict]:
    """直近N日の運用収支をスタイル別に日次で集計する(オリジナル10R勝負の記録用)。"""
    ensure_tables(conn)
    today = datetime.now(JST).date()
    history = []
    for offset in range(days):
        date = (today - timedelta(days=offset)).isoformat()
        sessions = sessions_on_date(conn, date)
        if not sessions and offset > 0:
            continue
        original = next((s for s in sessions if s["style"] == "original"), None)
        history.append(
            {
                "date": date,
                "total_profit": sum(s["profit"] for s in sessions),
                "sessions": sessions,
                "original": original,
            }
        )
    return history


def bankroll_finance(conn: sqlite3.Connection) -> dict:
    """Stripe風ダッシュボード用の運用サマリー。全セッション通算。"""
    ensure_tables(conn)
    row = conn.execute(
        """
        select
            coalesce(sum(total_stake), 0) as stake,
            coalesce(sum(payout), 0) as payout,
            coalesce(sum(case when status='won' then 1 else 0 end), 0) as wins,
            coalesce(sum(case when status='lost' then 1 else 0 end), 0) as losses
        from bankroll_bets where status in ('won', 'lost')
        """
    ).fetchone()
    stake, payout, wins, losses = row["stake"], row["payout"], row["wins"], row["losses"]
    session = active_session(conn) or latest_session(conn)
    balance = None
    if session:
        balance = session_state(conn, session)["balance"]
    history = daily_history(conn, days=30)
    # 古い順に累計損益を積み上げて資金推移にする
    series = []
    cumulative = 0
    for day in sorted(history, key=lambda d: d["date"]):
        cumulative += day["total_profit"]
        series.append({"date": day["date"], "cumulative_profit": cumulative, "day_profit": day["total_profit"]})
    return {
        "current_balance": balance,
        "total_stake": stake,
        "total_payout": payout,
        "total_profit": payout - stake,
        "roi": round(payout / stake, 4) if stake else None,
        "wins": wins,
        "losses": losses,
        "hit_rate": round(wins / (wins + losses), 4) if (wins + losses) else None,
        "today_profit": (history[0]["total_profit"] if history else 0),
        "series": series,
    }


def evaluate_stop(config: dict, state: dict) -> str | None:
    # 停止条件は結果確定分(残高)で判定する。pending中は判定を保留しない。
    style = STYLES.get(config.get("style") or "balance") or STYLES["balance"]
    race_limit = style.get("race_limit")
    settled = state["wins"] + state["losses"]
    if race_limit and settled >= race_limit:
        return f"{race_limit}レース完了(収支確定 {'+' if state['profit'] >= 0 else ''}{state['profit']}円)"
    # オリジナル(伸ばせるだけ伸ばす)は目標達成で止めない
    if not race_limit and state["balance"] >= config["target_amount"]:
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
        "styles": [
            {**style, "weights": [list(pair) for pair in style["weights"]]}
            for style in STYLES.values()
        ],
        "auto_buy_available": False,
        "session": None,
        "state": None,
        "proposal": None,
        "judged_races": [],
    }
    yesterday = (datetime.now(JST).date() - timedelta(days=1)).isoformat()
    payload["yesterday"] = {"date": yesterday, "sessions": sessions_on_date(conn, yesterday)}
    payload["daily_history"] = daily_history(conn, days=14)
    payload["finance"] = bankroll_finance(conn)
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
    now_jst = datetime.now(JST)
    if session.get("plan"):
        payload["plan"] = {
            "locked_at": session["plan"].get("locked_at"),
            "race_limit": session["plan"].get("race_limit"),
            "slots": _plan_with_status(session["plan"], state, now_jst),
        }

    if session["status"] != "active":
        return payload
    if state["pending_bet"]:
        payload["message"] = "結果待ちの購入記録があります。レース確定後に結果を入力してください。"
        return payload

    plan_keys = None
    if session.get("plan"):
        plan_keys = [
            slot["race_key"]
            for slot in payload["plan"]["slots"]
            if slot.get("status") == "planned" and slot.get("race_key")
        ]
    judged, proposal = _judge_races(conn, data_dir, config, state, plan_keys=plan_keys)
    payload["judged_races"] = judged
    payload["proposal"] = proposal
    if proposal is None and not judged:
        payload["message"] = (
            "予定レースを消化しました。差し替えるか停止してください。" if plan_keys is not None else "本日はこれから発走する対象レースがありません。"
        )
    return payload


def _judge_races(conn, data_dir, config: dict, state: dict, plan_keys: list | None = None) -> tuple[list[dict], dict | None]:
    now_jst = datetime.now(JST)
    today = build_today_forecast_payload(conn, data_dir)
    active, _ = _future_forecasts(today.get("forecasts", []), now_jst + timedelta(minutes=2))
    active.sort(key=lambda race: race.get("start_time") or "99:99")

    handled_keys = {bet["race_key"] for bet in state["bets"] if bet["race_key"]}
    budget = _race_budget(state["balance"], config)
    if plan_keys is not None:
        # 予定レース表(朝に確定)に沿って回す
        by_key = {race.get("race_key"): race for race in active}
        pending_races = [by_key[key] for key in plan_keys if key in by_key][:12]
    else:
        pending_races = [race for race in active[:12] if race.get("race_key") not in handled_keys]

    odds_status = {"attempted": 0, "fetched": 0, "failed": 0, "errors": []}
    live_odds_by_race = _fetch_live_odds_for_races(pending_races, limit=6, odds_status=odds_status)

    judged: list[dict] = []
    proposal: dict | None = None
    for race in pending_races:
        live_trifecta = live_odds_by_race.get(race.get("race_key"))
        verdict = _judge_race(race, budget, config, live_trifecta, force_plan=plan_keys is not None)
        judged.append(verdict["summary"])
        if proposal is None and verdict["proposal"] is not None:
            proposal = verdict["proposal"]
            verdict["summary"]["is_next"] = True
    if proposal is not None:
        proposal["odds_status"] = odds_status
    return judged, proposal


def _race_budget(balance: int, config: dict) -> int:
    cap = int(balance * config["per_race_cap_pct"] / 100 // UNIT * UNIT)
    return max(0, min(cap, balance))


def _judge_race(race: dict, budget: int, config: dict, live_trifecta: dict | None = None, force_plan: bool = False) -> dict:
    style = STYLES.get(config.get("style") or "balance") or STYLES["balance"]
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
    confidence_rank = int((race.get("confidence") or {}).get("rank") or 1)
    if not force_plan and confidence_rank < style["min_confidence_rank"]:
        summary["reason"] = f"{style['label']}運用のため混戦は見送り"
        return {"summary": summary, "proposal": None}
    bet_type = style.get("bet_type") or "trifecta"
    if bet_type == "auto":
        top_prob = float(((race.get("top3") or [{}])[0]).get("probability") or 0)
        bet_type = choose_bet_type(top_prob)
    candidates = _race_tickets(race, live_trifecta, bet_type=bet_type)
    if len(candidates) < 2:
        summary["reason"] = "買い目候補が不足(1点勝負は行わない)"
        return {"summary": summary, "proposal": None}
    if budget < UNIT * 2:
        summary["reason"] = f"1レース上限({config['per_race_cap_pct']}%)内で最低2点を買えません"
        return {"summary": summary, "proposal": None}

    allocation = _allocate(budget, candidates, style["weights"])
    total_stake = sum(t["stake"] for t in allocation)
    expected = sum(t["stake"] * t["odds"] * t["hit_probability"] for t in allocation)
    ev = expected / max(1, total_stake)
    summary["ev"] = round(ev, 3)
    if ev < config["min_ev"] and not force_plan:
        summary["reason"] = f"期待値不足(EV {ev:.2f} < {config['min_ev']:.2f})"
        return {"summary": summary, "proposal": None}

    summary["decision"] = "bet"
    if force_plan and ev < config["min_ev"]:
        # 株運用の発想: 自信の薄い局面はポジションを半分に縮小して張る
        half_budget = max(UNIT * 2, (budget // 2) // UNIT * UNIT)
        if half_budget < sum(t["stake"] for t in allocation):
            allocation = _allocate(half_budget, candidates, style["weights"])
            total_stake = sum(t["stake"] for t in allocation)
            expected = sum(t["stake"] * t["odds"] * t["hit_probability"] for t in allocation)
            ev = expected / max(1, total_stake)
    live_count = sum(1 for t in allocation if t.get("odds_source") == "live")
    summary["reason"] = f"EV {ev:.2f} / {len(allocation)}点分散" + (f" / ライブ{live_count}点" if live_count else "")
    if force_plan and ev < config["min_ev"]:
        summary["reason"] = f"予定レース・半分に縮小して実行(EV {ev:.2f} 低め・見送りも可)"
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
        "live_odds_count": live_count,
        "top_pick": (race.get("top3") or [{}])[0],
        "scenario_headline": (race.get("scenario") or {}).get("headline") or "",
        "bet_type": bet_type,
        "copy_text": _copy_text(race, allocation, bet_type),
    }
    return {"summary": summary, "proposal": proposal}


def _exacta_odds(race: dict, score: float) -> tuple[float, float]:
    """2車単の推定オッズと的中確率。軸(本命)1着×相手2着の条件付き確率から。"""
    axis_prob = float(((race.get("top3") or [{}])[0]).get("probability") or 0.4)
    axis_prob = max(0.05, min(0.95, axis_prob))
    partner = max(0.01, min(0.95, float(score or 0.05)))
    p_hit = axis_prob * min(0.9, partner / max(0.05, 1 - axis_prob))
    p_hit = max(0.005, min(0.8, p_hit))
    odds = max(1.3, min(80.0, (1.0 / p_hit) * 0.75))  # 控除率25%
    return round(odds, 1), round(p_hit, 4)


def _race_tickets(race: dict, live_trifecta: dict | None = None, bet_type: str = "trifecta") -> list[dict]:
    confidence = race.get("confidence") or {}
    rank = int(confidence.get("rank") or 1)
    has_signals = bool(race.get("comment_signals"))
    live_trifecta = live_trifecta or {}
    tickets = []
    if bet_type == "exacta":
        # 2車単(軸1着固定・少点数): 的中率を確保する主力券種
        for index, ex in enumerate((race.get("exacta") or [])[:4]):
            label = ex.get("label") if isinstance(ex, dict) else str(ex)
            if not label:
                continue
            score = _float(ex.get("score") if isinstance(ex, dict) else None, default=0.1)
            odds, p_hit = _exacta_odds(race, score)
            tickets.append(
                {
                    "label": label,
                    "odds": odds,
                    "odds_source": "estimated",
                    "popularity": None,
                    "hit_probability": p_hit,
                    "ticket_rank": index + 1,
                    "bet_type": "exacta",
                    "suji": bool(ex.get("suji")) if isinstance(ex, dict) else False,
                }
            )
        return tickets
    for index, ticket in enumerate((race.get("tickets") or [])[:6]):
        label = ticket.get("label") if isinstance(ticket, dict) else str(ticket)
        if not label:
            continue
        score = _float(ticket.get("score") if isinstance(ticket, dict) else None, default=0.18)
        odds = _estimated_odds(race, score, index)
        odds_source = "estimated"
        popularity = None
        live = live_trifecta.get(label)
        if live and live.get("odds"):
            odds = float(live["odds"])
            odds_source = "live"
            popularity = live.get("popularity")
        tickets.append(
            {
                "label": label,
                "odds": odds,
                "odds_source": odds_source,
                "popularity": popularity,
                "hit_probability": _hit_probability(score, rank, has_signals),
                "ticket_rank": index + 1,
            }
        )
    return tickets


def _fetch_live_odds_for_races(races: list[dict], limit: int, odds_status: dict) -> dict[str, dict]:
    """発走が近いレースから順にライブオッズを取得する(公開ページ取得のため件数は絞る)。"""
    result: dict[str, dict] = {}
    fetched = 0
    for race in races:
        if fetched >= limit:
            break
        url = race.get("url")
        race_key = race.get("race_key")
        if not url or not race_key:
            continue
        odds_status["attempted"] += 1
        try:
            payload = fetch_live_odds(url, timeout=8)
            trifecta = payload.get("trifecta") or {}
            if trifecta:
                result[race_key] = trifecta
                odds_status["fetched"] += 1
            else:
                odds_status["failed"] += 1
        except Exception as exc:
            odds_status["failed"] += 1
            odds_status["errors"].append({"race_key": race_key, "reason": str(exc)[:140]})
        fetched += 1
    return result


def _allocate(budget: int, candidates: list[dict], weights: list | None = None) -> list[dict]:
    """スタイルの重み表に沿って、予算と候補が許す限り複数点へ配分する。

    点数の上限はスタイルの重み表の長さ(堅実3点/バランス5点/冒険6点)だが、
    予算(100円単位)と買い目候補数が足りなければ自然に減る。固定の点数縛りはない。
    """
    weights = [tuple(pair) for pair in (weights or [("本線", 0.5), ("抑え", 0.3), ("妙味", 0.2)])]
    total_units = budget // UNIT

    # 本線=スコア1位、抑え=2位。3枠目以降(妙味/穴系)は残りから期待値順に選ぶ。
    ordered = list(candidates[:2])
    tail = sorted(candidates[2:], key=lambda t: t["odds"] * t["hit_probability"], reverse=True)
    ordered.extend(tail)

    usable = max(2, min(len(weights), len(ordered), total_units))
    usable = min(usable, len(ordered), total_units) or 1
    roles = weights[:usable]
    weight_sum = sum(weight for _, weight in roles) or 1.0

    allocation = []
    used_units = 0
    for (role, weight), ticket in zip(roles, ordered):
        units = max(1, int(total_units * weight / weight_sum))
        allocation.append(
            {
                "role": role,
                "label": ticket["label"],
                "stake": units * UNIT,
                "odds": ticket["odds"],
                "odds_source": ticket.get("odds_source") or "estimated",
                "popularity": ticket.get("popularity"),
                "hit_probability": ticket["hit_probability"],
                "projected_return": _round_yen(units * UNIT * ticket["odds"]),
            }
        )
        used_units += units
    # 予算超過は下位の枠から削り、端数は本線へ寄せる。
    idx = len(allocation) - 1
    while used_units > total_units and idx >= 0:
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


def _copy_text(race: dict, allocation: list[dict], bet_type: str = "trifecta") -> str:
    kind = "2車単" if bet_type == "exacta" else "3連単"
    header = f"{race.get('venue') or ''}{race.get('race_no') or ''}R {kind}"
    lines = [header]
    for ticket in allocation:
        lines.append(f"{ticket['role']} {ticket['label']} {ticket['stake']}円")
    return "\n".join(lines)


def _session_row(row: sqlite3.Row) -> dict:
    keys = set(row.keys())
    plan = None
    if "plan_json" in keys and row["plan_json"]:
        try:
            plan = json.loads(row["plan_json"])
        except (TypeError, ValueError):
            plan = None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "session_date": row["session_date"],
        "status": row["status"],
        "stop_reason": row["stop_reason"],
        "config": json.loads(row["config_json"]),
        "plan": plan,
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
