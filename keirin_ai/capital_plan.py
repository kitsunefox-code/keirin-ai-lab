from __future__ import annotations

import itertools
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from keirin_ai.forecast_view import build_today_forecast_payload
from keirin_ai.odds import ex_signals_for_cars, fetch_live_odds, odds_url_from_racecard


JST = timezone(timedelta(hours=9))


def build_capital_plan_payload(
    conn,
    data_dir: Path | str,
    start_amount: int,
    target_amount: int,
    max_races: int = 2,
    live_odds: bool = True,
    max_live_fetch: int = 10,
) -> dict:
    start_amount = max(100, int(start_amount or 0))
    target_amount = max(start_amount, int(target_amount or start_amount))
    max_races = max(1, min(5, int(max_races or 2)))
    now_jst = datetime.now(JST)
    today = build_today_forecast_payload(conn, data_dir)
    forecasts = today.get("forecasts", [])
    active_forecasts, elapsed_forecasts = _future_forecasts(forecasts, now_jst)
    candidates = _candidate_tickets(active_forecasts)
    odds_status = {
        "mode": "live" if live_odds else "estimated",
        "attempted": 0,
        "fetched": 0,
        "failed": 0,
        "errors": [],
    }
    if live_odds:
        _attach_live_odds(candidates, max_live_fetch=max_live_fetch, odds_status=odds_status)

    plans = _build_plans(candidates, start_amount, target_amount, max_races)
    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "generated_at_jst": now_jst.isoformat(timespec="seconds"),
        "input": {
            "start_amount": start_amount,
            "target_amount": target_amount,
            "max_races": max_races,
            "live_odds": live_odds,
        },
        "odds": odds_status,
        "summary": {
            "forecast_count": len(forecasts),
            "active_forecast_count": len(active_forecasts),
            "elapsed_forecast_count": len(elapsed_forecasts),
            "candidate_count": len(candidates),
            "plan_count": len(plans),
            "next_race_time": min((race.get("start_time") or "99:99" for race in active_forecasts), default=""),
            "data_used": ["予想スコア", "前検コメント特徴", "ライン関係", "結果学習", "EXデータ", "ライブオッズ"],
        },
        "plans": plans,
        "notice": "ライブオッズは公開ページから取得できた場合だけ使用します。払戻や利益を保証するものではありません。",
    }


def _future_forecasts(forecasts: list[dict], now_jst: datetime) -> tuple[list[dict], list[dict]]:
    active: list[dict] = []
    elapsed: list[dict] = []
    for race in forecasts:
        starts_at = _race_start_datetime(race, now_jst)
        if starts_at is None:
            active.append(race)
            continue
        if starts_at > now_jst:
            active.append(race)
        else:
            elapsed.append(race)
    return active, elapsed


def _race_start_datetime(race: dict, now_jst: datetime) -> datetime | None:
    start_time = str(race.get("start_time") or "").strip()
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", start_time)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    race_date = _race_date(race, now_jst)
    return datetime(race_date.year, race_date.month, race_date.day, hour, minute, tzinfo=JST)


def _race_date(race: dict, now_jst: datetime):
    raw = str(race.get("race_date") or "").strip()
    iso_match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if iso_match:
        return datetime(int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))).date()
    jp_match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", raw)
    if jp_match:
        return datetime(int(jp_match.group(1)), int(jp_match.group(2)), int(jp_match.group(3))).date()
    return now_jst.date()


def _candidate_tickets(forecasts: list[dict]) -> list[dict]:
    candidates: list[dict] = []
    for race in forecasts:
        tickets = race.get("tickets") or []
        top = (race.get("top3") or [{}])[0]
        confidence = race.get("confidence") or {}
        rank = int(confidence.get("rank") or 1)
        for index, ticket in enumerate(tickets[:4]):
            label = ticket.get("label") if isinstance(ticket, dict) else str(ticket)
            cars = ticket.get("cars") if isinstance(ticket, dict) else _cars_from_label(label)
            ticket_score = _float(ticket.get("score") if isinstance(ticket, dict) else None, default=0.18)
            odds = _estimated_odds(race, ticket_score, index)
            hit_probability = _hit_probability(ticket_score, rank, bool(race.get("comment_signals")))
            candidates.append(
                {
                    "race_key": race.get("race_key"),
                    "venue": race.get("venue") or "",
                    "race_no": race.get("race_no"),
                    "race_date": race.get("race_date") or "",
                    "start_time": race.get("start_time") or "",
                    "url": race.get("url") or "",
                    "odds_url": _safe_odds_url(race.get("url") or ""),
                    "ticket": label,
                    "cars": cars,
                    "ticket_rank": index + 1,
                    "odds": odds,
                    "odds_str": f"{odds:.1f}",
                    "odds_source": "estimated",
                    "popularity": None,
                    "hit_probability": hit_probability,
                    "confidence": confidence.get("label") or "混戦",
                    "confidence_rank": rank,
                    "top_pick": {
                        "car_no": top.get("car_no"),
                        "name": top.get("name") or "",
                        "probability": top.get("probability") or 0,
                    },
                    "rationale": _rationale(race, ticket_score),
                    "ex_signals": [],
                    "scenario": race.get("scenario") or {},
                }
            )
    candidates.sort(key=lambda item: _candidate_sort_key(item), reverse=True)
    return candidates


def _attach_live_odds(candidates: list[dict], max_live_fetch: int, odds_status: dict) -> None:
    race_urls = []
    seen = set()
    for item in candidates:
        race_key = item.get("race_key")
        url = item.get("url")
        if not race_key or not url or race_key in seen:
            continue
        race_urls.append((race_key, url))
        seen.add(race_key)
        if len(race_urls) >= max_live_fetch:
            break

    cache: dict[str, dict] = {}
    for race_key, url in race_urls:
        odds_status["attempted"] += 1
        try:
            payload = fetch_live_odds(url, timeout=8)
            cache[race_key] = payload
            if payload.get("trifecta_count"):
                odds_status["fetched"] += 1
            else:
                odds_status["failed"] += 1
                odds_status["errors"].append({"race_key": race_key, "reason": "3連単オッズ未取得"})
        except Exception as exc:
            odds_status["failed"] += 1
            odds_status["errors"].append({"race_key": race_key, "reason": str(exc)[:140]})

    for item in candidates:
        payload = cache.get(item.get("race_key"))
        if not payload:
            continue
        live = (payload.get("trifecta") or {}).get(item.get("ticket"))
        if live and live.get("odds"):
            item["odds"] = float(live["odds"])
            item["odds_str"] = live.get("odds_str") or f"{item['odds']:.1f}"
            item["odds_source"] = "live"
            item["popularity"] = live.get("popularity")
            item["odds_url"] = payload.get("source_url") or item.get("odds_url")
        item["ex_signals"] = ex_signals_for_cars(payload.get("ex_records") or [], item.get("cars") or [])


def _build_plans(candidates: list[dict], start_amount: int, target_amount: int, max_races: int) -> list[dict]:
    pool = candidates[:36]
    plans: list[dict] = []
    for item in pool:
        plans.append(_single_plan(item, start_amount, target_amount))

    for count in range(2, max_races + 1):
        combo_pool = candidates[:22] if count <= 3 else candidates[:12]
        for combo in itertools.combinations(combo_pool, count):
            race_keys = {item.get("race_key") for item in combo}
            if len(race_keys) != count:
                continue
            ordered = sorted(combo, key=lambda item: item.get("start_time") or "99:99")
            plans.append(_roll_plan(ordered, start_amount, target_amount))

    viable = [plan for plan in plans if plan["projected_return"] >= target_amount * 0.72]
    viable.sort(key=_plan_sort_key, reverse=True)
    return viable[:8]


def _single_plan(item: dict, start_amount: int, target_amount: int) -> dict:
    projected = _round_yen(start_amount * item["odds"])
    return _plan(
        kind="single",
        title=f"{item['venue']} {item['race_no']}R 単発",
        races=[_leg(item, start_amount, projected)],
        start_amount=start_amount,
        target_amount=target_amount,
        projected_return=projected,
        hit_probability=item["hit_probability"],
    )


ROLL_REINVEST_RATIO = 0.7  # 全額転がし禁止: 各レースは資金の7割まで再投資し、3割は残す


def _roll_plan(items: list[dict], start_amount: int, target_amount: int) -> dict:
    current = float(start_amount)
    legs = []
    hit_probability = 1.0
    for item in items:
        stake = max(100, _round_yen(current * ROLL_REINVEST_RATIO))
        stake = min(stake, _round_yen(current))
        reserve = current - stake
        current = reserve + stake * item["odds"]
        hit_probability *= item["hit_probability"]
        legs.append(_leg(item, stake, _round_yen(current)))
    projected = _round_yen(current)
    title = " → ".join(f"{item['venue']} {item['race_no']}R" for item in items)
    return _plan(
        kind=f"{len(items)}race_roll",
        title=f"{len(items)}レース転がし: {title}",
        races=legs,
        start_amount=start_amount,
        target_amount=target_amount,
        projected_return=projected,
        hit_probability=hit_probability,
    )


def _plan(
    kind: str,
    title: str,
    races: list[dict],
    start_amount: int,
    target_amount: int,
    projected_return: int,
    hit_probability: float,
) -> dict:
    multiplier = projected_return / max(1, start_amount)
    target_ratio = target_amount / max(1, start_amount)
    overshoot = projected_return / max(1, target_amount)
    live_count = sum(1 for race in races if race["odds_source"] == "live")
    risk = "低" if hit_probability >= 0.28 else "中" if hit_probability >= 0.12 else "高"
    return {
        "kind": kind,
        "title": title,
        "start_amount": start_amount,
        "target_amount": target_amount,
        "projected_return": projected_return,
        "shortfall": max(0, target_amount - projected_return),
        "multiplier": round(multiplier, 2),
        "target_ratio": round(target_ratio, 2),
        "hit_probability": round(hit_probability, 4),
        "risk": risk,
        "live_odds_count": live_count,
        "races": races,
        "score": round(hit_probability + live_count * 0.025 - abs(math.log(max(0.1, overshoot))) * 0.08, 5),
        "warnings": _warnings(races, projected_return, target_amount, hit_probability),
    }


def _leg(item: dict, stake: int, projected_return: int) -> dict:
    return {
        "race_key": item.get("race_key"),
        "venue": item.get("venue"),
        "race_no": item.get("race_no"),
        "start_time": item.get("start_time"),
        "ticket": item.get("ticket"),
        "cars": item.get("cars") or [],
        "stake": stake,
        "projected_return": projected_return,
        "odds": round(float(item.get("odds") or 0), 2),
        "odds_str": item.get("odds_str") or "",
        "odds_source": item.get("odds_source"),
        "popularity": item.get("popularity"),
        "confidence": item.get("confidence"),
        "hit_probability": round(float(item.get("hit_probability") or 0), 4),
        "top_pick": item.get("top_pick") or {},
        "rationale": item.get("rationale") or [],
        "ex_signals": item.get("ex_signals") or [],
        "odds_url": item.get("odds_url") or "",
        "url": item.get("url") or "",
    }


def _candidate_sort_key(item: dict) -> tuple:
    return (
        1 if item.get("odds_source") == "live" else 0,
        item.get("confidence_rank") or 0,
        item.get("hit_probability") or 0,
        -int(item.get("ticket_rank") or 9),
    )


def _plan_sort_key(plan: dict) -> tuple:
    reached = 1 if plan["shortfall"] == 0 else 0
    live_ratio = plan["live_odds_count"] / max(1, len(plan["races"]))
    closeness = -abs(math.log(max(0.1, plan["projected_return"] / max(1, plan["target_amount"]))))
    return (reached, live_ratio, plan["score"], closeness)


def _rationale(race: dict, ticket_score: float) -> list[str]:
    top = (race.get("top3") or [{}])[0]
    confidence = race.get("confidence") or {}
    reasons = [
        f"信頼度: {confidence.get('label') or '混戦'}",
        f"本命: {top.get('car_no', '-')}{top.get('name', '')} {float(top.get('probability') or 0) * 100:.1f}%",
        f"買い目スコア: {ticket_score * 100:.1f}",
    ]
    signals = race.get("comment_signals") or []
    if signals:
        reasons.append("コメント/関係性あり")
    scenario = race.get("scenario") or {}
    if scenario.get("upset"):
        reasons.append("崩れ筋も評価")
    return reasons


def _warnings(races: list[dict], projected_return: int, target_amount: int, hit_probability: float) -> list[str]:
    warnings = []
    if len(races) >= 2:
        warnings.append("転がしは各レース7割まで再投資(全額転がし禁止)")
    if any(race["odds_source"] != "live" for race in races):
        warnings.append("一部は推定倍率です")
    if projected_return < target_amount:
        warnings.append("目標額に届かない候補です")
    if hit_probability < 0.1:
        warnings.append("目標重視でリスク高めです")
    return warnings


def _estimated_odds(race: dict, ticket_score: float, ticket_index: int) -> float:
    top = (race.get("top3") or [{}])[0]
    top_prob = _float(top.get("probability"), default=0.25)
    rank = int((race.get("confidence") or {}).get("rank") or 1)
    base = 3.2 + (1.0 - min(0.98, top_prob)) * 42.0 + ticket_index * 5.0
    base += max(0.0, 0.46 - ticket_score) * 36.0
    if rank >= 3:
        base *= 0.72
    elif rank <= 1:
        base *= 1.24
    return round(max(1.4, min(180.0, base)), 1)


def _hit_probability(ticket_score: float, confidence_rank: int, has_comment_signals: bool) -> float:
    value = ticket_score * (0.52 + confidence_rank * 0.12)
    if has_comment_signals:
        value *= 1.04
    return round(max(0.015, min(0.62, value)), 4)


def _cars_from_label(label: str) -> list[int]:
    return [int(part) for part in str(label).split("-") if part.isdigit()]


def _safe_odds_url(url: str) -> str:
    try:
        return odds_url_from_racecard(url)
    except Exception:
        return ""


def _round_yen(value: float) -> int:
    return int(round(value / 10.0) * 10)


def _float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
