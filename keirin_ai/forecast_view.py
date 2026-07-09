from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


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
            "schedule_summary": _load_schedule_summary(data_path),
        }

    source = _read_json(forecast_path, {})
    forecasts = [
        _enrich_forecast(conn, item)
        for item in source.get("forecasts", [])
        if item.get("race_key")
    ]
    forecasts.sort(key=lambda item: (item.get("start_time") or "99:99", item.get("venue") or "", item.get("race_no") or 0))

    confidence_counts: dict[str, int] = {}
    for item in forecasts:
        label = item.get("confidence", {}).get("label", "混戦")
        confidence_counts[label] = confidence_counts.get(label, 0) + 1

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
            "count": len(forecasts),
            "after": source.get("after") or "14:30",
            "target_date": source.get("target_date") or "2026-07-08",
            "high_confidence": confidence_counts.get("強", 0),
            "middle_confidence": confidence_counts.get("中", 0),
            "mixed": confidence_counts.get("混戦", 0),
        },
        "forecasts": forecasts,
        "schedule_summary": _load_schedule_summary(data_path),
    }


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
    return {
        "race_key": race_key,
        "venue": venue,
        "event": race.get("event") or "",
        "race_no": race_no,
        "race_date": race.get("race_date") or forecast.get("race_date") or "",
        "race_class": race.get("race_class") or forecast.get("race_class") or "",
        "start_time": forecast.get("start_time") or race.get("start_time") or "",
        "url": race.get("source_url") or forecast.get("url") or "",
        "title": _race_title(venue, race_no, race.get("event") or forecast.get("title")),
        "top3": top3,
        "tickets": [_ticket(ticket) for ticket in tickets[:5]],
        "confidence": confidence,
        "scenario": scenario,
        "comment_signals": signals,
        "lineup": lineup,
        "lines": lines,
        "entries": entries,
        "notes": _notes(forecast.get("notes", []), prediction),
    }


def _race_record(conn, race_key: str) -> dict:
    row = conn.execute(
        """
        select race_key, source_url, title, venue, event, race_no, race_class,
               race_date, lineup_json, raw_quality_json
        from races
        where race_key=?
        """,
        (race_key,),
    ).fetchone()
    return dict(row) if row else {}


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
               style, gear, comment_excerpt, emotion_json, features_json
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
            }
        )

    for car_no, rank in ranking_by_car.items():
        if car_no in seen:
            continue
        entries.append(_entry_from_ranking(rank))
    return sorted(entries, key=lambda item: item["car_no"])


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
    }


def _ticket(ticket: dict | str) -> dict:
    if isinstance(ticket, str):
        return {"label": ticket, "score": None, "cars": [int(part) for part in ticket.split("-") if part.isdigit()]}
    return {
        "label": ticket.get("label") or "-".join(str(car) for car in ticket.get("cars", [])),
        "score": ticket.get("score"),
        "cars": ticket.get("cars") or [],
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


def _scenario(top3: list[dict], ranking: list[dict], entries_by_car: dict[int, dict], lines: list[dict]) -> dict:
    if not top3:
        return {"headline": "出走データが足りません。", "flow": "", "watch": "", "upset": ""}
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

    return {"headline": headline, "flow": flow, "watch": watch, "upset": upset}


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
    if not top3:
        return {"label": "混戦", "rank": 0, "reason": "比較できる予想がありません。"}
    first = float(top3[0].get("probability") or 0)
    second = float(top3[1].get("probability") or 0) if len(top3) > 1 else 0
    gap = first - second
    if first >= 0.72 and gap >= 0.22:
        return {"label": "強", "rank": 3, "reason": f"本命確率{first * 100:.1f}%、2番手との差{gap * 100:.1f}pt。"}
    if first >= 0.45:
        return {"label": "中", "rank": 2, "reason": f"本命確率{first * 100:.1f}%。相手選びが重要。"}
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
