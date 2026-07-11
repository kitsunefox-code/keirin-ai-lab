from __future__ import annotations

from keirin_ai.predictor import _exacta_candidates

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
JST = timezone(timedelta(hours=9))


def build_today_forecast_payload(conn, data_dir: Path | str = DATA_DIR) -> dict:
    data_path = Path(data_dir)
    forecast_path = _latest_forecast_file(data_path)
    if not forecast_path:
        return {
            "ok": True,
            "generated_at": _now(),
            "forecast_file": None,
            "summary": {"count": 0},
            "forecasts": [],
            "recommended_races": [],
            "schedule_summary": _load_schedule_summary(data_path),
        }

    source = _read_json(forecast_path, {})
    all_forecasts = [
        _enrich_forecast(conn, item)
        for item in source.get("forecasts", [])
        if item.get("race_key")
    ]
    now_jst = datetime.now(JST)
    for race in all_forecasts:
        race["elapsed"] = _is_elapsed(race, now_jst)
    forecasts = all_forecasts
    active = [race for race in forecasts if not race["elapsed"]]
    elapsed_count = len(forecasts) - len(active)
    forecasts.sort(key=lambda item: (item.get("start_time") or "99:99", item.get("venue") or "", item.get("race_no") or 0))

    confidence_counts: dict[str, int] = {}
    for item in active:
        label = item.get("confidence", {}).get("label", "混戦")
        confidence_counts[label] = confidence_counts.get(label, 0) + 1

    recommended = _pick_recommended(active)
    recommended_keys = {race["race_key"] for race in recommended}
    for race in forecasts:
        race["recommended"] = race["race_key"] in recommended_keys

    return {
        "ok": True,
        "generated_at": _now(),
        "forecast_file": str(forecast_path),
        "source": {
            "target_date": source.get("target_date"),
            "after": source.get("after"),
            "scanned_pages": source.get("scanned_pages"),
            "candidates": source.get("candidates", []),
        },
        "summary": {
            "count": len(active),
            "elapsed_count": elapsed_count,
            "after": source.get("after") or "14:30",
            "target_date": source.get("target_date") or "2026-07-08",
            "high_confidence": confidence_counts.get("強", 0),
            "middle_confidence": confidence_counts.get("中", 0),
            "mixed": confidence_counts.get("混戦", 0),
        },
        "forecasts": forecasts,
        "recommended_races": recommended,
        "schedule_summary": _load_schedule_summary(data_path),
    }


def _is_elapsed(race: dict, now_jst: datetime) -> bool:
    starts_at = _race_start_datetime(race, now_jst)
    if starts_at is None:
        return False
    return starts_at <= now_jst


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


def _pick_recommended(forecasts: list[dict], limit: int = 5) -> list[dict]:
    """信頼度・本命確率・買い目スコアから、AIが自信を持てるレースを上位表示用に選ぶ。"""
    scored = []
    for race in forecasts:
        confidence = race.get("confidence") or {}
        rank = int(confidence.get("rank") or 0)
        if rank < 2:  # 混戦は推奨に出さない
            continue
        top = (race.get("top3") or [{}])[0]
        top_prob = float(top.get("probability") or 0)
        best_ticket_score = max((t.get("score") or 0) for t in (race.get("tickets") or [{"score": 0}]))
        score = rank * 10 + top_prob * 8 + best_ticket_score * 4
        scored.append((score, race))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [race for _score, race in scored[:limit]]


def _latest_forecast_file(data_path: Path) -> Path | None:
    files = list(data_path.glob("forecast_*.json"))
    if not files:
        return None

    def priority(path: Path) -> tuple[str, int, float]:
        name = path.name
        # 日付が新しいファイルを最優先。同日なら refit 版を優先する。
        date_match = re.search(r"(\d{8})", name)
        date_key = date_match.group(1) if date_match else "00000000"
        refit = 1 if "refit" in name else 0
        return (date_key, refit, path.stat().st_mtime)

    return max(files, key=priority)


def _enrich_forecast(conn, forecast: dict) -> dict:
    race_key = forecast["race_key"]
    race = _race_record(conn, race_key)
    prediction = _latest_prediction(conn, race_key)
    ranking = prediction.get("ranking") or _ranking_from_forecast(forecast)
    tickets = prediction.get("tickets") or _tickets_from_forecast(forecast)
    entries = _entry_records(conn, race_key, ranking)
    entries_by_car = {entry["car_no"]: entry for entry in entries}
    lineup = _clean_lineup(_json_or(race.get("lineup_json"), forecast.get("lineup") or []), entries_by_car)
    lines = _line_details(lineup, entries_by_car)
    top3 = [_top_row(row, entries_by_car) for row in ranking[:3]]
    confidence = _confidence(top3)
    scenario = _scenario(top3, ranking, entries_by_car, lines)
    signals = _comment_signals(top3, entries, lines)

    venue = race.get("venue") or forecast.get("venue")
    race_no = race.get("race_no") or forecast.get("race_no")
    race_class_official = race.get("race_class_official") or ""
    bank = _bank_info(conn, race)
    hour_type = race.get("hour_type") or ""
    weather = _json_or(race.get("weather_json"), None)
    exacta_picks = _exacta_candidates(
        [{"car_no": r.get("car_no"), "win_probability": r.get("win_probability") or r.get("probability") or 0} for r in ranking],
        {"lineup": lineup, "entrants": [{"car_no": e.get("car_no"), "racing_score": e.get("racing_score")} for e in entries]},
    )
    value = _attach_value(exacta_picks, ranking, _json_or(race.get("latest_odds_json"), None))
    hit_estimate = _hit_estimate(ranking)
    source_url = race.get("source_url") or forecast.get("url") or ""
    day_index = _day_index_from_url(source_url)
    return {
        "race_key": race_key,
        "venue": venue,
        "event": race.get("event") or "",
        "race_no": race_no,
        "race_date": race.get("race_date") or forecast.get("race_date") or "",
        "race_class": race.get("race_class") or forecast.get("race_class") or "",
        "race_class_official": race_class_official,
        "is_girls": "ガール" in race_class_official,
        "class_group": _class_group(race_class_official, "ガール" in race_class_official),
        "start_time": forecast.get("start_time") or race.get("start_time") or "",
        "url": source_url,
        "day_index": day_index,
        "is_day1": day_index == 1,
        "title": _race_title(venue, race_no, race.get("event") or forecast.get("title")),
        "top3": top3,
        "tickets": [_ticket(ticket) for ticket in tickets[:8]],
        "exacta": exacta_picks,
        "value": value,
        "hit_estimate": hit_estimate,
        "confidence": confidence,
        "scenario": scenario,
        "comment_signals": signals,
        "lineup": lineup,
        "lines": lines,
        "entries": entries,
        "bank": bank,
        "hour_type": hour_type,
        "hour_label": HOUR_LABELS.get(hour_type, ""),
        "weather": weather,
        "notes": _notes(forecast.get("notes", []), prediction),
    }


def _hit_estimate(ranking: list[dict]) -> dict | None:
    """買い目の的中目安(較正済み勝率ベース)。市場オッズがなくても出せる。

    2車単: 軸1着固定で相手上位2点(1-2, 1-3)の合計的中率。
    3連単: 軸1着固定のスジ上位6点までの合計的中率。Harville近似。
    """
    probs = []
    for row in ranking[:5]:
        try:
            probs.append((int(row.get("car_no") or 0), max(0.001, min(0.95, float(row.get("win_probability") or row.get("probability") or 0)))))
        except (TypeError, ValueError):
            continue
    if len(probs) < 3:
        return None
    p = {car: pr for car, pr in probs}
    order = [car for car, _ in probs]
    r1, r2, r3 = order[0], order[1], order[2]
    r4 = order[3] if len(order) >= 4 else None

    def p_ex(a, b):  # 2車単 a-b の的中確率
        return p[a] * min(0.95, p[b] / max(0.05, 1 - p[a]))

    def p_tri(a, b, c):  # 3連単 a-b-c
        rem = max(0.05, 1 - p[a] - p[b])
        return p_ex(a, b) * min(0.95, p[c] / rem)

    exacta = p_ex(r1, r2) + p_ex(r1, r3)
    tri_combos = [(r1, r2, r3), (r1, r3, r2)]
    if r4:
        tri_combos += [(r1, r2, r4), (r1, r3, r4)]
    trifecta = sum(p_tri(*c) for c in tri_combos)
    return {"exacta": round(min(0.99, exacta), 4), "trifecta": round(min(0.99, trifecta), 4)}


def _attach_value(exacta_picks: list[dict], ranking: list[dict], snapshot: dict | None) -> dict | None:
    """AIの2車単候補に実オッズを付け、期待値(EV=較正確率×オッズ)を計算する。

    的中確率はHarville近似: P(a-b) = P(a勝ち) × P(bが残りで最上位) = p_a × p_b/(1-p_a)。
    win_probability は較正済み(実測に一致)なので、EV>1 は「市場がAIの見立てより安く売っている」目。
    """
    if not isinstance(snapshot, dict) or not snapshot.get("exacta"):
        return None
    odds_by_key = {row.get("key"): float(row.get("odds") or 0) for row in snapshot["exacta"] if row.get("key")}
    probs = {}
    for row in ranking:
        try:
            probs[int(row.get("car_no") or 0)] = float(row.get("win_probability") or row.get("probability") or 0)
        except (TypeError, ValueError):
            continue
    best = None
    for pick in exacta_picks:
        cars = pick.get("cars") or []
        if len(cars) != 2:
            continue
        odds = odds_by_key.get(pick.get("label"))
        p_a = max(0.001, min(0.95, probs.get(int(cars[0]), 0)))
        p_b = max(0.001, min(0.95, probs.get(int(cars[1]), 0)))
        p_hit = p_a * min(0.9, p_b / max(0.05, 1 - p_a))
        if not odds or odds <= 1.0:
            continue
        ev = odds * p_hit
        pick["live_odds"] = round(odds, 1)
        pick["hit_prob"] = round(p_hit, 4)
        pick["ev"] = round(ev, 2)
        if best is None or ev > best["ev"]:
            best = {"label": pick.get("label"), "odds": round(odds, 1), "prob": round(p_hit, 4), "ev": round(ev, 2)}
    if best is None:
        return None
    best["taken_at"] = snapshot.get("taken_at") or ""
    return best


def _day_index_from_url(url: str) -> int | None:
    """WINTICKETのracecard URL(/keirin/{venue}/racecard/{cup_id}/{day_index}/{race_no})から節day目を取り出す。"""
    m = re.search(r"/racecard/\d{10}/(\d+)/\d+", str(url or ""))
    return int(m.group(1)) if m else None


def _class_group(race_class_official: str, is_girls: bool) -> str:
    """級班を短いバッジ用ラベルへ(S級/A級/ガールズ)。"""
    if is_girls:
        return "ガールズ"
    text = str(race_class_official or "")
    if text.startswith("S級") or "S級" in text[:3]:
        return "S級"
    if text.startswith("A級") or "A級" in text[:3]:
        return "A級"
    if text.startswith("L級"):
        return "L級"
    return ""


def _race_record(conn, race_key: str) -> dict:
    row = conn.execute(
        """
        select race_key, source_url, title, venue, event, race_no, race_class,
               race_date, lineup_json, raw_quality_json, race_class_official,
               venue_id, hour_type, weather_json, latest_odds_json
        from races
        where race_key=?
        """,
        (race_key,),
    ).fetchone()
    return dict(row) if row else {}


HOUR_LABELS = {
    "hourTypeMorning": "モーニング",
    "hourTypeNormal": "デイ",
    "hourTypeNight": "ナイター",
    "hourTypeMidnight": "ミッドナイト",
}


def _bank_info(conn, race: dict) -> dict | None:
    """races.venue_id からvenuesテーブルのバンク特徴を組み立てる(UI表示・傾向文用)。"""
    venue_id = str(race.get("venue_id") or "")
    if not venue_id:
        return None
    row = conn.execute("select * from venues where venue_id=?", (venue_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    kimarite = _json_or(data.get("kimarite_json"), None)
    bias = data.get("bank_bias")
    tendency = "逃げ・先行有利" if (bias or 0) > 0.1 else "差し・追込有利" if (bias or 0) < -0.1 else "標準的"
    return {
        "name": data.get("name"),
        "track_distance": data.get("track_distance"),
        "straight": data.get("straight"),
        "angle_center": data.get("angle_center"),
        "is_indoor": bool(data.get("is_indoor")) if data.get("is_indoor") is not None else None,
        "kimarite": kimarite,
        "bank_bias": bias,
        "tendency": tendency,
        "net_notes": data.get("net_notes"),
    }


def _latest_prediction(conn, race_key: str) -> dict:
    row = conn.execute(
        """
        select ranking_json, tickets_json, model_name, model_version, created_at
        from predictions
        where race_key=?
        order by id desc
        limit 1
        """,
        (race_key,),
    ).fetchone()
    if not row:
        return {}
    return {
        "ranking": _json_or(row["ranking_json"], []),
        "tickets": _json_or(row["tickets_json"], []),
        "model_name": row["model_name"],
        "model_version": row["model_version"],
        "created_at": row["created_at"],
    }


def _entry_records(conn, race_key: str, ranking: list[dict]) -> list[dict]:
    ranking_by_car = {int(row.get("car_no") or 0): row for row in ranking}
    rows = conn.execute(
        """
        select car_no, name, prefecture, class, age, term, ai_mark, racing_score,
               style, gear, comment_excerpt, emotion_json, features_json, player_id
        from entries
        where race_key=?
        order by car_no
        """,
        (race_key,),
    ).fetchall()
    entries: list[dict] = []
    seen: set[int] = set()
    for row in rows:
        car_no = int(row["car_no"])
        seen.add(car_no)
        rank = ranking_by_car.get(car_no, {})
        emotion = _json_or(row["emotion_json"], rank.get("emotion") or {})
        entries.append(
            {
                "car_no": car_no,
                "name": row["name"] or rank.get("name") or "",
                "prefecture": row["prefecture"] or rank.get("prefecture") or "",
                "class": row["class"] or rank.get("class") or "",
                "age": row["age"],
                "term": row["term"],
                "ai_mark": row["ai_mark"] or rank.get("ai_mark") or "",
                "racing_score": row["racing_score"] if row["racing_score"] is not None else rank.get("racing_score"),
                "style": row["style"] or rank.get("style") or "",
                "gear": row["gear"] or rank.get("gear") or "",
                "comment": row["comment_excerpt"] or rank.get("comment") or "",
                "emotion": emotion if isinstance(emotion, dict) else {},
                "features": _json_or(row["features_json"], rank.get("features") or {}),
                "stats": rank.get("stats") or {},
                "win_probability": rank.get("win_probability") or rank.get("probability"),
                "model_score": rank.get("model_score") or rank.get("score"),
                "reasons": rank.get("reasons") or [],
                "player_id": row["player_id"] or "",
            }
        )

    for car_no, rank in ranking_by_car.items():
        if car_no in seen:
            continue
        entries.append(_entry_from_ranking(rank))
    _attach_player_form(conn, entries)
    return sorted(entries, key=lambda item: item["car_no"])


def _attach_player_form(conn, entries: list[dict]) -> None:
    """蓄積した実戦データ(着順・競走得点の推移)から、選手ごとの調子を付ける。

    player_form(レース後の実着順)と entries履歴(出走表の競走得点の変化)を集計し、
    直近着順のならび・3着内率・得点の増減から 好調/平常/不調 を判定する。
    データが3走未満の選手は判定しない(捏造しない)。
    """
    ids = [e.get("player_id") for e in entries if e.get("player_id")]
    if not ids:
        return
    marks = ",".join("?" for _ in ids)
    # 直近の実着順(確定済み全レースの着順から導出。古い順に並べて後ろが最新)
    finish_map: dict[str, list[int]] = {}
    for row in conn.execute(
        f"""
        select e.player_id, e.car_no, r.result_json
        from entries e
        join races r on r.race_key = e.race_key
        where e.player_id in ({marks}) and r.result_json is not null and r.result_json != ''
        order by r.fetched_at
        """,
        ids,
    ).fetchall():
        order = (_json_or(row["result_json"], {}) or {}).get("finish_order") or []
        try:
            finish = [int(c) for c in order].index(int(row["car_no"])) + 1
        except (ValueError, TypeError):
            continue
        finish_map.setdefault(row["player_id"], []).append(finish)
    # 競走得点の推移(出走表の履歴。最初と最新の差=上向き/下向き)
    score_map: dict[str, list[float]] = {}
    for row in conn.execute(
        f"""
        select e.player_id, e.racing_score from entries e
        join races r on r.race_key = e.race_key
        where e.player_id in ({marks}) and e.racing_score is not null
        order by r.fetched_at
        """,
        ids,
    ).fetchall():
        try:
            score_map.setdefault(row["player_id"], []).append(float(row["racing_score"]))
        except (TypeError, ValueError):
            continue

    # JKA公式プロフィール(今期得点・直近4ヶ月成績・次期級班)
    profile_map: dict[str, dict] = {}
    try:
        for row in conn.execute(
            f"select * from player_profiles where player_id in ({marks})", ids
        ).fetchall():
            profile_map[row["player_id"]] = dict(row)
    except Exception:
        profile_map = {}

    from keirin_ai.jka import class_move  # 循環importなし(jkaはsources/stdlibのみ依存)

    for entry in entries:
        pid = entry.get("player_id")
        finishes = (finish_map.get(pid) or [])[-12:]
        scores = score_map.get(pid) or []
        score_delta = round(scores[-1] - scores[0], 1) if len(scores) >= 2 else None

        # 公式データ: 直近4ヶ月得点 − 今期(適用)得点 = 上向き/下向きの公式シグナル
        prof = profile_map.get(pid) or {}
        recent_official = _json_or(prof.get("recent_json"), None) or {}
        score_now = prof.get("score_now")
        score_recent = recent_official.get("score")
        official_delta = (
            round(float(score_recent) - float(score_now), 2)
            if score_recent is not None and score_now is not None
            else None
        )
        move = class_move(prof.get("class_now") or "", prof.get("class_next") or "")
        if move:
            entry["class_move"] = move  # up=昇級予定 / down=降級予定
        if prof:
            entry["profile"] = {
                "class_now": prof.get("class_now"),
                "class_next": prof.get("class_next"),
                "class_history": _json_or(prof.get("class_history_json"), []),
                "total": _json_or(prof.get("total_json"), None),
                "fetched_at": prof.get("fetched_at"),
            }

        if len(finishes) < 3 and official_delta is None:
            entry["form"] = None
            continue

        point = 0
        top3_rate = None
        avg = None
        if len(finishes) >= 3:
            recent = finishes[-6:]
            top3 = sum(1 for f in recent if f <= 3)
            top3_rate = top3 / len(recent)
            avg = sum(recent) / len(recent)
            point += 1 if top3_rate >= 0.5 else -1 if top3_rate <= 0.2 else 0
        # 得点シグナルは公式(直近4ヶ月 vs 適用点)を優先、無ければ自前の履歴差
        delta_signal = official_delta if official_delta is not None else score_delta
        if delta_signal is not None:
            point += 1 if delta_signal >= 1.5 else -1 if delta_signal <= -1.5 else 0
        label = "好調" if point >= 1 else "不調" if point <= -1 else "平常"
        entry["form"] = {
            "label": label,
            "finishes": finishes,
            "top3_rate": round(top3_rate, 3) if top3_rate is not None else None,
            "avg_finish": round(avg, 1) if avg is not None else None,
            "score_delta": score_delta,
            "official_delta": official_delta,
            "score_now": score_now,
            "score_recent": score_recent,
            "races": len(finishes),
        }


def _entry_from_ranking(row: dict) -> dict:
    return {
        "car_no": int(row.get("car_no") or 0),
        "name": row.get("name") or "",
        "prefecture": row.get("prefecture") or "",
        "class": row.get("class") or "",
        "age": row.get("age"),
        "term": row.get("term"),
        "ai_mark": row.get("ai_mark") or "",
        "racing_score": row.get("racing_score"),
        "style": row.get("style") or "",
        "gear": row.get("gear") or "",
        "comment": row.get("comment") or "",
        "emotion": row.get("emotion") or {},
        "features": row.get("features") or {},
        "stats": row.get("stats") or {},
        "win_probability": row.get("win_probability") or row.get("probability"),
        "model_score": row.get("model_score") or row.get("score"),
        "reasons": row.get("reasons") or [],
    }


def _ranking_from_forecast(forecast: dict) -> list[dict]:
    rows = []
    for row in forecast.get("top3", []):
        rows.append(
            {
                "car_no": row.get("car_no"),
                "name": row.get("name"),
                "win_probability": row.get("probability"),
                "model_score": row.get("score"),
                "emotion": {"tone": row.get("emotion")},
                "reasons": row.get("reasons") or [],
            }
        )
    return rows


def _tickets_from_forecast(forecast: dict) -> list[dict | str]:
    return forecast.get("tickets") or []


def _top_row(row: dict, entries_by_car: dict[int, dict]) -> dict:
    car_no = int(row.get("car_no") or 0)
    entry = entries_by_car.get(car_no, {})
    emotion = row.get("emotion") if isinstance(row.get("emotion"), dict) else entry.get("emotion", {})
    return {
        "car_no": car_no,
        "name": row.get("name") or entry.get("name") or "",
        "probability": round(float(row.get("win_probability") or row.get("probability") or entry.get("win_probability") or 0), 4),
        "score": row.get("model_score") or row.get("score") or entry.get("model_score"),
        "mark": row.get("ai_mark") or entry.get("ai_mark") or "",
        "style": row.get("style") or entry.get("style") or "",
        "comment": row.get("comment") or entry.get("comment") or "",
        "emotion": emotion or {},
        "reasons": row.get("reasons") or entry.get("reasons") or [],
        "form": entry.get("form"),
        "term": row.get("term") or entry.get("term"),
        "class_move": entry.get("class_move"),
        "player_id": entry.get("player_id") or "",
    }


def _ticket(ticket: dict | str) -> dict:
    if isinstance(ticket, str):
        return {"label": ticket, "score": None, "cars": [int(part) for part in ticket.split("-") if part.isdigit()]}
    return {
        "label": ticket.get("label") or "-".join(str(car) for car in ticket.get("cars", [])),
        "score": ticket.get("score"),
        "cars": ticket.get("cars") or [],
        "suji": bool(ticket.get("suji")),
    }


def _line_details(lineup: list[list[int]], entries_by_car: dict[int, dict]) -> list[dict]:
    details = []
    for index, line in enumerate(lineup, start=1):
        members = [entries_by_car.get(int(car), {"car_no": int(car), "name": "", "comment": ""}) for car in line]
        label = "-".join(str(item["car_no"]) for item in members)
        front = members[0] if members else {}
        followers = members[1:]
        relation = _line_relation(front, followers)
        details.append(
            {
                "index": index,
                "label": label,
                "members": [
                    {
                        "car_no": item.get("car_no"),
                        "name": item.get("name") or f"{item.get('car_no')}車",
                        "style": item.get("style") or "",
                        "comment": item.get("comment") or "",
                        "probability": item.get("win_probability"),
                        "player_id": item.get("player_id") or "",
                    }
                    for item in members
                ],
                "front": _entry_label(front),
                "relation": relation,
            }
        )
    return details


def _clean_lineup(lineup: list[list[int]], entries_by_car: dict[int, dict]) -> list[list[int]]:
    valid = set(entries_by_car)
    cleaned: list[list[int]] = []
    seen: set[tuple[int, ...]] = set()
    for raw_line in lineup:
        line: list[int] = []
        for raw_car in raw_line:
            car = int(raw_car)
            if car not in valid or car in line:
                continue
            line.append(car)
        if not line:
            continue
        key = tuple(line)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(line)

    if len(cleaned) >= 3:
        smaller_cars = set()
        for line in cleaned:
            if len(line) <= 4:
                smaller_cars.update(line)
        cleaned = [line for line in cleaned if len(line) <= 4 or not smaller_cars.issubset(set(line))]
    return cleaned


def _line_relation(front: dict, followers: list[dict]) -> str:
    if not front:
        return "ライン情報が薄い構成です。"
    front_label = _entry_label(front)
    if not followers:
        comment = front.get("comment") or ""
        if "決めず" in comment:
            return f"{front_label}は決めずの構え。位置取りの自由度はありますが、展開待ちです。"
        return f"{front_label}は単騎気味。自分で動くか、好位を拾う競走になりそうです。"
    follower_names = "、".join(_entry_label(item) for item in followers)
    comments = " / ".join(f"{item.get('car_no')}「{item.get('comment')}」" for item in followers if item.get("comment"))
    if _has_intent(front.get("comment") or "") or front.get("style") == "逃":
        base = f"{front_label}が前で動き、{follower_names}が追走する形。"
    else:
        base = f"{front_label}を先頭に、{follower_names}が続く並び。"
    if comments:
        base += f" 後ろのコメント: {comments}"
    return base


def _race_pattern(lines: list[dict], entries_by_car: dict[int, dict]) -> str:
    """KEIRIN.JPガイドの戦型分類: 2分戦/3分戦/4分戦(細切れ戦)/先行1車。"""
    groups = [line for line in lines if len(line.get("members") or []) >= 2]
    singles = [line for line in lines if len(line.get("members") or []) == 1]
    total_lines = len(groups) + len(singles)
    self_powered = 0
    for line in lines:
        front = (line.get("members") or [{}])[0]
        entry = entries_by_car.get(int(front.get("car_no") or 0), front)
        if (entry.get("style") or "") in {"逃", "両"}:
            self_powered += 1
    if self_powered == 1 and total_lines >= 2:
        return "先行1車"
    if total_lines <= 1:
        return ""
    if total_lines == 2:
        return "2分戦"
    if total_lines == 3:
        return "3分戦"
    return "細切れ戦"


def _scenario(top3: list[dict], ranking: list[dict], entries_by_car: dict[int, dict], lines: list[dict]) -> dict:
    if not top3:
        return {"headline": "出走データが足りません。", "flow": "", "watch": "", "upset": "", "pattern": ""}
    top = top3[0]
    second = top3[1] if len(top3) > 1 else {}
    top_line = _find_line(top["car_no"], lines)
    top_position = _line_position(top["car_no"], top_line)
    top_label = _top_label(top)
    prob = top.get("probability") or 0
    gap = prob - float(second.get("probability") or 0)
    rival_front = _rival_front(top_line, lines, entries_by_car)

    if top_position == 0 and top_line and len(top_line.get("members", [])) >= 2:
        headline = f"{top_label}が本線。前で動いてライン{top_line['label']}を残す展開を重視。"
        flow = f"{top_line['relation']} 先行争いが長引かなければ、番手以降も車券圏に残る想定です。"
    elif top_position > 0 and top_line:
        front = top_line.get("members", [{}])[0]
        headline = f"{top_label}は番手/追走からの差しが中心。{_entry_label(front)}が形を作れるかが鍵。"
        flow = f"ライン{top_line['label']}の前が主導権を取れば、{top_label}の差し込みが見えます。早めに踏み合うと外のまくりを受けるリスク。"
    elif top_line and len(top_line.get("members", [])) == 1:
        headline = f"{top_label}は単騎でも総合評価上位。混戦の好位取りを評価。"
        flow = "固定の援護は薄いので、隊列が緩んだ瞬間に仕掛ける形が理想です。"
    else:
        headline = f"{top_label}を中心視。得点、近況、コメント評価の合算で上位。"
        flow = "ライン情報が薄いので、上位確率と脚質バランスを優先して見ています。"

    watch = f"対抗は{_top_label(second) if second else '未設定'}。1着確率差は約{gap * 100:.1f}ポイントです。"
    if rival_front:
        upset = f"崩れるなら{_entry_label(rival_front)}の主導権取り、またはライン分断で番手が離れるケース。"
    else:
        upset = "崩れるなら単騎勢の位置取り成功、または本線ラインの踏み遅れです。"

    pattern = _race_pattern(lines, entries_by_car)
    if pattern == "先行1車":
        flow = f"先行1車の展開。主導権は自力型が握りやすく、そのライン残りが本線です。 {flow}"
    elif pattern == "細切れ戦":
        flow = f"細切れ戦(4分戦以上)。短いラインの主導権争いで展開が乱れやすい一戦です。 {flow}"
    elif pattern:
        flow = f"{pattern}。 {flow}"

    sequence = _dev_sequence(top3, entries_by_car, lines)
    return {"headline": headline, "flow": flow, "watch": watch, "upset": upset, "pattern": pattern, "sequence": sequence}


def _dev_sequence(top3: list[dict], entries_by_car: dict[int, dict], lines: list[dict]) -> list[str]:
    """スタートからゴールまでの展開ストーリーを過去データ(S/B実績・決まり手)から組み立てる。"""

    def stat(car_no, key: str) -> float:
        entry = entries_by_car.get(int(car_no or 0)) or {}
        stats = entry.get("stats") or {}
        try:
            return float(stats.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    def label(member: dict) -> str:
        return f"{member.get('car_no')}{(member.get('name') or '')[:4]}"

    heads = [(line, (line.get("members") or [{}])[0]) for line in lines if line.get("members")]
    heads = [(line, head) for line, head in heads if head.get("car_no")]
    if not heads:
        return []

    # S取り: 先頭選手のS実績(start_count)最多のライン。同数なら車番の若い方(内枠有利)。
    s_line, s_head = max(heads, key=lambda p: (stat(p[1].get("car_no"), "start_count"), -int(p[1].get("car_no") or 9)))
    s_count = int(stat(s_head.get("car_no"), "start_count"))
    # 主導権(B): 先頭選手のB実績+逃げ決まり手が最も強いライン。
    b_line, b_head = max(heads, key=lambda p: (stat(p[1].get("car_no"), "back_count") + stat(p[1].get("car_no"), "escape") * 2, -int(p[1].get("car_no") or 9)))
    b_count = int(stat(b_head.get("car_no"), "back_count"))
    # 一度出るが下がる役: 主導権ライン以外の自力型で、まくり実績が逃げ実績を上回る選手。
    feint = None
    for line, head in heads:
        if head is b_head:
            continue
        if stat(head.get("car_no"), "makuri") >= max(1.0, stat(head.get("car_no"), "escape")):
            if feint is None or stat(head.get("car_no"), "makuri") > stat(feint.get("car_no"), "makuri"):
                feint = head

    seq = []
    seq.append(f"S: {label(s_head)}のライン(S実績{s_count}回)が取って前受け。")
    if s_line is not b_line:
        seq.append(f"周回: 打鐘前に{label(b_head)}ラインが上昇。{label(s_head)}は突っ張るか下げるかの駆け引き。")
    else:
        seq.append(f"周回: {label(s_head)}が前のまま隊列は落ち着いて流れる。")
    if feint is not None and feint is not s_head:
        seq.append(f"動き: {label(feint)}が一度出るが深追いせず下げ、まくり(実績{int(stat(feint.get('car_no'), 'makuri'))}回)に脚をためる。")
    seq.append(f"打鐘: {label(b_head)}が主導権(B実績{b_count}回)。ライン{b_line.get('label') or ''}で駆ける。")

    top = (top3 or [{}])[0]
    top_car = top.get("car_no")
    sashi = stat(top_car, "sashi")
    makuri = stat(top_car, "makuri")
    escape = stat(top_car, "escape")
    if escape >= max(sashi, makuri):
        finish = f"最終: 本命{top_car}はそのまま押し切り(逃げ{int(escape)}回)を狙う。"
    elif makuri > sashi:
        finish = f"最終: 本命{top_car}は最終バックからまくり(実績{int(makuri)}回)で仕留める。"
    else:
        finish = f"最終: 本命{top_car}は番手から直線差し(実績{int(sashi)}回)で勝負。"
    seq.append(finish)
    return seq


def _comment_signals(top3: list[dict], entries: list[dict], lines: list[dict]) -> list[str]:
    signals: list[str] = []
    for row in top3:
        entry = next((item for item in entries if item.get("car_no") == row.get("car_no")), {})
        comment = entry.get("comment") or row.get("comment") or ""
        emotion = entry.get("emotion") or row.get("emotion") or {}
        if comment:
            signals.append(f"{row['car_no']}{row['name']}: 「{comment}」 {emotion.get('summary') or _comment_hint(comment)}")
    for line in lines:
        relation = line.get("relation") or ""
        if relation:
            signals.append(f"ライン{line['label']}: {relation}")
    return signals[:7]


def _confidence(top3: list[dict]) -> dict:
    """本命の堅さで3段階に分類。しきい値は較正済み勝率(本命は中央値約29%)に合わせている。

    本命戦 = 本命が抜けている堅いレース / 順当 = 本命中心の標準戦 / 混戦 = 割れ気味。
    """
    if not top3:
        return {"label": "混戦", "rank": 0, "reason": "比較できる予想がありません。"}
    first = float(top3[0].get("probability") or 0)
    second = float(top3[1].get("probability") or 0) if len(top3) > 1 else 0
    gap = first - second
    if first >= 0.38 or (first >= 0.34 and gap >= 0.12):
        return {"label": "本命", "rank": 3, "reason": f"本命確率{first * 100:.1f}%、2番手との差{gap * 100:.1f}pt。本命が抜けている。"}
    if first >= 0.29:
        return {"label": "順当", "rank": 2, "reason": f"本命確率{first * 100:.1f}%。本命中心で相手を絞る。"}
    return {"label": "混戦", "rank": 1, "reason": f"本命確率{first * 100:.1f}%で割れ気味。"}


def _notes(notes: list[str], prediction: dict) -> list[str]:
    clean_notes = [note for note in notes if note]
    if prediction.get("model_name"):
        clean_notes.append(f"使用モデル: {prediction['model_name']} {prediction.get('model_version') or ''}".strip())
    return clean_notes[:5]


def _load_schedule_summary(data_path: Path) -> dict:
    path = data_path / "backfill" / "official_schedule_summary_20230708_20260708.json"
    return _read_json(path, {}) if path.exists() else {}


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _json_or(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _race_title(venue: str | None, race_no: int | None, fallback: str | None) -> str:
    if venue and race_no:
        return f"{venue} {race_no}R"
    return fallback or "レース"


def _entry_label(entry: dict) -> str:
    if not entry:
        return "-"
    name = entry.get("name") or ""
    car_no = entry.get("car_no") or ""
    return f"{car_no}{name}".strip()


def _top_label(row: dict) -> str:
    if not row:
        return "-"
    return f"{row.get('car_no')}{row.get('name')}"


def _find_line(car_no: int, lines: list[dict]) -> dict | None:
    for line in lines:
        if any(member.get("car_no") == car_no for member in line.get("members", [])):
            return line
    return None


def _line_position(car_no: int, line: dict | None) -> int:
    if not line:
        return -1
    for index, member in enumerate(line.get("members", [])):
        if member.get("car_no") == car_no:
            return index
    return -1


def _rival_front(top_line: dict | None, lines: list[dict], entries_by_car: dict[int, dict]) -> dict | None:
    candidates = []
    top_label = top_line.get("label") if top_line else None
    for line in lines:
        if line.get("label") == top_label or not line.get("members"):
            continue
        front = line["members"][0]
        entry = entries_by_car.get(int(front.get("car_no") or 0), front)
        stats = entry.get("stats") or {}
        pressure = float(stats.get("back_count") or 0) + float(stats.get("home_count") or 0) * 0.7
        if entry.get("style") == "逃":
            pressure += 2
        candidates.append((pressure, entry))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _has_intent(comment: str) -> bool:
    return any(word in comment for word in ["自力", "前で", "自在", "先行", "何でも"])


def _comment_hint(comment: str) -> str:
    if "自力" in comment:
        return "自力意思があり、仕掛ける気配を評価します。"
    if "決めず" in comment or "単騎" in comment:
        return "位置取り次第で評価が変わるコメントです。"
    if "君" in comment or "勢" in comment or "番手" in comment:
        return "追走関係がはっきりしています。"
    return "コメント単体では強弱を決めにくいです。"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
