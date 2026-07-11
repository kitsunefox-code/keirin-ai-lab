from __future__ import annotations

"""選手個人ページ用のデータ集計。

本日の出走選手(+直近でJKA公式プロフィールを取得済みの選手)について、
プロフィール・得点推移・直近の着順・本日の出走レースをまとめる。
static-api/players.json として書き出し、player.html?id=登録番号 で参照する。
"""

import json

from keirin_ai.jka import class_move

JST_TERM_ROOKIE = (129, 130)


def build_players_payload(conn, today_payload: dict) -> dict:
    forecasts = today_payload.get("forecasts") or []

    # 本日出走している選手のentry(既にform/class_move/profileが付与済み)
    today_by_player: dict[str, dict] = {}
    for race in forecasts:
        for entry in race.get("entries") or []:
            pid = entry.get("player_id")
            if not pid:
                continue
            today_by_player[pid] = {
                "entry": entry,
                "race_key": race.get("race_key"),
                "venue": race.get("venue"),
                "race_no": race.get("race_no"),
                "start_time": race.get("start_time"),
                "day_index": race.get("day_index"),
                "race_class_official": race.get("race_class_official"),
            }

    ids = list(today_by_player.keys())
    # 直近でプロフィール取得済みの選手も対象に含める(前日の出走者など、単独ページとして開けるように)
    try:
        for row in conn.execute(
            "select player_id from player_profiles order by fetched_at desc limit 800"
        ).fetchall():
            if row["player_id"] not in today_by_player:
                ids.append(row["player_id"])
    except Exception:
        pass
    ids = list(dict.fromkeys(ids))  # 重複除去(順序維持)
    if not ids:
        return {"ok": True, "players": {}}

    marks = ",".join("?" for _ in ids)

    # 選手名・所属・脚質の最新値(entriesの最新行)
    name_map: dict[str, dict] = {}
    for row in conn.execute(
        f"""
        select player_id, name, prefecture, class, age, term, style
        from entries
        where player_id in ({marks})
        order by rowid
        """,
        ids,
    ).fetchall():
        name_map[row["player_id"]] = dict(row)

    # 着順履歴(結果確定済み全レース。古い順→新しい順)
    finish_history: dict[str, list[dict]] = {}
    for row in conn.execute(
        f"""
        select e.player_id, e.car_no, r.race_key, r.venue, r.race_no, r.race_date, r.race_class_official, r.result_json
        from entries e
        join races r on r.race_key = e.race_key
        where e.player_id in ({marks}) and r.result_json is not null and r.result_json != ''
        order by r.fetched_at
        """,
        ids,
    ).fetchall():
        try:
            order = [int(c) for c in json.loads(row["result_json"]).get("finish_order") or []]
            finish = order.index(int(row["car_no"])) + 1
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
        finish_history.setdefault(row["player_id"], []).append(
            {
                "race_key": row["race_key"],
                "venue": row["venue"],
                "race_no": row["race_no"],
                "race_date": row["race_date"],
                "race_class": row["race_class_official"],
                "finish": finish,
            }
        )

    # 競走得点の推移(出走表の履歴。古い順)
    score_trend: dict[str, list[dict]] = {}
    for row in conn.execute(
        f"""
        select e.player_id, e.racing_score, r.race_date, r.fetched_at
        from entries e
        join races r on r.race_key = e.race_key
        where e.player_id in ({marks}) and e.racing_score is not null
        order by r.fetched_at
        """,
        ids,
    ).fetchall():
        score_trend.setdefault(row["player_id"], []).append(
            {"date": row["race_date"], "score": row["racing_score"]}
        )

    # JKA公式プロフィール
    profile_map: dict[str, dict] = {}
    try:
        for row in conn.execute(
            f"select * from player_profiles where player_id in ({marks})", ids
        ).fetchall():
            profile_map[row["player_id"]] = dict(row)
    except Exception:
        pass

    players: dict[str, dict] = {}
    for pid in ids:
        today_info = today_by_player.get(pid)
        base_name = name_map.get(pid) or {}
        prof = profile_map.get(pid) or {}
        entry = (today_info or {}).get("entry") or {}
        term = entry.get("term") or base_name.get("term")
        finishes = finish_history.get(pid) or []
        trend = score_trend.get(pid) or []

        move = class_move(prof.get("class_now") or "", prof.get("class_next") or "")
        players[pid] = {
            "player_id": pid,
            "name": entry.get("name") or base_name.get("name") or "",
            "prefecture": entry.get("prefecture") or base_name.get("prefecture") or "",
            "class": entry.get("class") or base_name.get("class") or prof.get("class_now") or "",
            "class_next": prof.get("class_next") or "",
            "class_move": move,
            "style": entry.get("style") or base_name.get("style") or prof.get("style") or "",
            "age": base_name.get("age"),
            "term": term,
            "is_rookie": term in JST_TERM_ROOKIE,
            "score_now": prof.get("score_now"),
            "recent_official": _loads(prof.get("recent_json")),
            "total_official": _loads(prof.get("total_json")),
            "class_history": _loads(prof.get("class_history_json")) or [],
            "form": entry.get("form"),
            "finishes": finishes[-20:],
            "score_trend": trend[-20:],
            "today": (
                {
                    "race_key": today_info.get("race_key"),
                    "venue": today_info.get("venue"),
                    "race_no": today_info.get("race_no"),
                    "start_time": today_info.get("start_time"),
                    "day_index": today_info.get("day_index"),
                    "race_class_official": today_info.get("race_class_official"),
                    "win_probability": entry.get("win_probability"),
                    "car_no": entry.get("car_no"),
                    "comment": entry.get("comment"),
                }
                if today_info
                else None
            ),
        }

    return {"ok": True, "players": players}


def _loads(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
