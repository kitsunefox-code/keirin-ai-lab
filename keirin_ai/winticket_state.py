from __future__ import annotations

"""WINTICKETページ埋め込みの __PRELOADED_STATE__ から構造化データを取り込む。

テキストパース(sources.parse_winticket_racecard)より信頼できる情報源:
- 前検日インタビュー(FETCH_KEIRIN_INSPECTION_DAY_INTERVIEW_LIST)
- レース後インタビュー(FETCH_KEIRIN_RACE_RESULT_INTERVIEW_LIST, raceresultページ)
- EXデータ6種・直近開催成績(FETCH_KEIRIN_RACE の records)
- 確定結果・決まり手・上がりタイム(FETCH_KEIRIN_RACE の results)
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from keirin_ai.odds import _preloaded_state


EX_KEYS = ("exSpurt", "exThrust", "exLeftBehind", "exSplitLine", "exSnatch", "exCompete")
JST = timezone(timedelta(hours=9))


def raceresult_url_from_racecard(racecard_url: str) -> str:
    parsed = urlparse(racecard_url)
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 6 and parts[0] == "keirin" and parts[2] in {"racecard", "raceresult", "odds"}:
        slug, cup_id, day_index, race_no = parts[1], parts[3], parts[4], parts[5]
        return f"{parsed.scheme}://{parsed.netloc}/keirin/{slug}/raceresult/{cup_id}/{day_index}/{race_no}"
    raise ValueError("WINTICKETのレースURLからraceresult URLを作れません")


def state_queries(html_text: str) -> dict[str, dict]:
    state = _preloaded_state(html_text)
    queries = {}
    for query in (state.get("tanStackQuery") or {}).get("queries", []):
        key = query.get("queryKey") or []
        name = str(key[1]) if len(key) > 1 else str(key[0] if key else "")
        data = (query.get("state") or {}).get("data")
        if isinstance(data, dict):
            queries[name] = data
    return queries


def enrich_race_from_state(race: dict, html_text: str) -> dict:
    """テキストパース済みのraceへ、埋め込みJSONの追加情報を注入する。"""
    queries = state_queries(html_text)
    common = queries.get("FETCH_KEIRIN_RACE") or {}
    if not common:
        return race

    car_by_player = _car_by_player(common)
    name_by_player = {
        str(player.get("id")): player.get("name") or ""
        for player in common.get("players") or []
        if player.get("id")
    }

    inspection = _interviews_by_player(queries.get("FETCH_KEIRIN_INSPECTION_DAY_INTERVIEW_LIST"))
    post_race = _interviews_by_player(queries.get("FETCH_KEIRIN_RACE_RESULT_INTERVIEW_LIST"))

    records_by_car: dict[int, dict] = {}
    for record in common.get("records") or []:
        car_no = car_by_player.get(str(record.get("playerId") or ""))
        if car_no is None:
            continue
        records_by_car[car_no] = record

    # テキスト解析で取りこぼした選手を埋め込みデータから補完する(全車を必ず揃える)
    _fill_missing_entrants(race, common, car_by_player, name_by_player, records_by_car)

    bank = _current_bank(common)
    if bank:
        race["bank"] = bank
    race["all_venues"] = all_venue_details(common)
    hour_type = _hour_type(common.get("race") or {})
    if hour_type:
        race["hour_type"] = hour_type
    weather = _weather(queries, common.get("race") or {})
    if weather:
        race["weather_info"] = weather

    for entrant in race.get("entrants", []):
        car_no = int(entrant.get("car_no") or 0)
        record = records_by_car.get(car_no)
        if record:
            player_id = str(record.get("playerId") or "")
            entrant["player_id"] = player_id
            entrant["ex"] = _ex_summary(record)
            entrant["recent_form"] = _recent_form(record)
            entrant["position_stats"] = _position_stats(record)
            entrant["venue_stats"] = _venue_stats(record)
            if bank and bank.get("track_distance") in (333, 400, 500):
                entrant["track_stats"] = _rate_block(record.get(f"trackDistance{bank['track_distance']}"))
            if hour_type:
                entrant["hour_stats"] = _rate_block(record.get(hour_type))
            if not entrant.get("comment"):
                entrant["comment"] = str(record.get("comment") or "")
            interview = inspection.get(player_id)
            if interview:
                entrant["interview"] = interview
            post = post_race.get(player_id)
            if post:
                entrant["post_race_comment"] = post

    detail = _results_detail(common, car_by_player)
    if detail:
        race["results_detail"] = detail
        order = [row["car_no"] for row in detail if row.get("order")]
        if len(order) >= 2 and not race.get("result"):
            race["result"] = {
                "finish_order": order,
                "positions": {str(car): pos for pos, car in enumerate(order, start=1)},
                "source": "winticket-state",
            }

    race_meta = common.get("race") or {}
    race_class_official = str(race_meta.get("class") or race_meta.get("raceType3") or "")
    is_girls = "ガール" in race_class_official or "ガ" in str(race_meta.get("raceType3") or "")
    if race_class_official:
        race["race_class_official"] = race_class_official
    race["is_girls"] = is_girls

    ids_in_race = {str(entrant.get("player_id") or "") for entrant in race.get("entrants", []) if entrant.get("player_id")}
    head_to_head = _head_to_head_within_race(common.get("competitionRecords") or [], ids_in_race, name_by_player)
    if head_to_head:
        race["head_to_head"] = head_to_head
        head_to_head_by_player: dict[str, list[dict]] = {}
        for record in head_to_head:
            head_to_head_by_player.setdefault(record["player_id"], []).append(record)
        for entrant in race.get("entrants", []):
            matches = head_to_head_by_player.get(str(entrant.get("player_id") or ""))
            if matches:
                entrant["head_to_head"] = matches

    race["state_meta"] = {
        "has_inspection_interviews": bool(inspection),
        "has_post_race_interviews": bool(post_race),
        "record_count": len(records_by_car),
        "player_names": name_by_player,
    }
    return race


def _fill_missing_entrants(
    race: dict,
    common: dict,
    car_by_player: dict[str, int],
    name_by_player: dict[str, str],
    records_by_car: dict[int, dict],
) -> None:
    """テキスト解析の正規表現で欠けた選手を、埋め込みJSON(entries/players/records)から復元する。

    出走表の車番はテキストより埋め込みデータが正。特殊な氏名や表組み崩れで
    パースが数人落とすことがある(実測で52レース中13レース)。
    """
    existing = {int(e.get("car_no") or 0) for e in race.get("entrants", [])}
    entrants = race.setdefault("entrants", [])
    added = 0
    for player_id, car_no in car_by_player.items():
        if car_no in existing:
            continue
        record = records_by_car.get(car_no) or {}
        entrants.append(
            {
                "frame_no": car_no,
                "car_no": car_no,
                "name": name_by_player.get(player_id, "") or f"{car_no}番",
                "prefecture": "",
                "class": "",
                "age": None,
                "term": None,
                "ai_mark": "",
                "racing_score": record.get("racePoint"),
                "style": str(record.get("style") or ""),
                "gear": str(record.get("gearRatioStr") or record.get("gearRatio") or ""),
                "comment": str(record.get("comment") or ""),
                "player_id": player_id,
                "stats": {
                    "start_count": int(record.get("standing") or 0),
                    "home_count": 0,
                    "back_count": int(record.get("back") or 0),
                    "escape": int(record.get("frontRunner") or 0),
                    "makuri": int(record.get("stalker") or 0),
                    "sashi": int(record.get("deepCloser") or 0),
                    "mark": int(record.get("marker") or 0),
                    "first": int(record.get("first") or 0),
                    "second": int(record.get("second") or 0),
                    "third": int(record.get("third") or 0),
                    "outside": int(record.get("others") or 0),
                    "win_rate": float(record.get("firstRate") or 0.0),
                    "two_rate": float(record.get("secondRate") or 0.0),
                    "three_rate": float(record.get("thirdRate") or 0.0),
                },
                "recovered_from_state": True,
            }
        )
        existing.add(car_no)
        added += 1
    if added:
        entrants.sort(key=lambda e: int(e.get("car_no") or 0))
        quality = race.setdefault("raw_quality", {})
        quality["entrant_count"] = len(entrants)
        quality["recovered_entrants"] = added


def _head_to_head_within_race(records: list, ids_in_race: set[str], name_by_player: dict[str, str]) -> list[dict]:
    """対戦成績(competitionRecords)のうち、今回同時出走する2選手同士の対戦だけを抽出する。"""
    result = []
    for record in records:
        player_id = str(record.get("playerId") or "")
        opponent_id = str(record.get("opponentId") or "")
        if player_id not in ids_in_race or opponent_id not in ids_in_race:
            continue
        wins = int(record.get("wins") or 0)
        losses = int(record.get("losses") or 0)
        if wins + losses <= 0:
            continue
        result.append(
            {
                "player_id": player_id,
                "opponent_id": opponent_id,
                "opponent_name": name_by_player.get(opponent_id, ""),
                "wins": wins,
                "losses": losses,
            }
        )
    return result


def venue_details(venue: dict) -> dict:
    """WINTICKETのvenueオブジェクトをアプリ内のバンク特徴dictへ変換する。"""
    kimarite = _kimarite_distribution(venue.get("factors"))
    return {
        "venue_id": str(venue.get("id") or ""),
        "name": venue.get("name") or "",
        "slug": venue.get("slug") or "",
        "track_distance": venue.get("trackDistance"),
        "straight": venue.get("trackStraightDistance"),
        "angle_center": venue.get("trackAngleCenter"),
        "angle_straight": venue.get("trackAngleStraight"),
        "home_width": venue.get("homeWidth"),
        "back_width": venue.get("backWidth"),
        "center_width": venue.get("centerWidth"),
        "is_indoor": venue.get("isIndoor"),
        "kimarite": kimarite,
        "bank_bias": _bank_bias(kimarite, venue.get("trackDistance"), venue.get("trackStraightDistance")),
        "bank_feature": (venue.get("bankFeature") or "")[:600],
    }


def all_venue_details(common: dict) -> list[dict]:
    """レースページに載っている全競輪場のバンク特徴を返す(DB蓄積用)。"""
    details = []
    for venue in common.get("venues") or []:
        if venue.get("id") and venue.get("trackDistance"):
            details.append(venue_details(venue))
    return details


def _current_bank(common: dict) -> dict | None:
    """開催中バンクのバンク特徴を取り出す。"""
    cups = common.get("cups")
    cup = cups[0] if isinstance(cups, list) and cups else (cups if isinstance(cups, dict) else {})
    venue_id = str(cup.get("venueId") or "")
    venue = next(
        (v for v in common.get("venues") or [] if str(v.get("id")) == venue_id),
        None,
    )
    if not venue and common.get("venues"):
        venue = (common.get("venues") or [None])[0]
    if not venue:
        return None
    return venue_details(venue)


def _kimarite_distribution(factors) -> dict | None:
    """factorsの「1着」決まり手分布を {逃げ,捲り,差し} の割合(合計1)に正規化する。"""
    if not isinstance(factors, list):
        return None
    first = next((f for f in factors if str(f.get("title") or "").startswith("1着")), None)
    if not first:
        return None
    dist = {"逃げ": 0.0, "捲り": 0.0, "差し": 0.0}
    total = 0.0
    for item in first.get("datasets") or []:
        name = str(item.get("name") or "")
        value = float(item.get("value") or 0)
        if name in dist:
            dist[name] += value
            total += value
        elif name == "マーク":  # マークは差し系として扱う
            dist["差し"] += value
            total += value
    if total <= 0:
        return None
    return {k: round(v / total, 3) for k, v in dist.items()}


def _bank_bias(kimarite: dict | None, track_distance, straight) -> float | None:
    """バンクの脚質傾向を -1(差し・追込有利)〜+1(逃げ・先行有利)で表す。

    決まり手分布(逃げ%-差し%)を主軸に、周長が短い/直線が短いほど先行有利へ補正。
    """
    parts: list[float] = []
    if kimarite:
        parts.append(kimarite.get("逃げ", 0.0) - kimarite.get("差し", 0.0))
    if track_distance:
        # 333m以下=先行有利(+)、500m=差し有利(-)。400mを中立。
        parts.append(max(-1.0, min(1.0, (400 - float(track_distance)) / 70.0)) * 0.5)
    if straight:
        # 直線が短いほど逃げ残りやすい(+)。40m短→+、60m長→-。
        parts.append(max(-1.0, min(1.0, (50.0 - float(straight)) / 15.0)) * 0.3)
    if not parts:
        return None
    return round(max(-1.0, min(1.0, sum(parts))), 3)


def _hour_type(race_meta: dict) -> str | None:
    """発走時刻からrecordの時間帯別成績キー(hourType*)を決める。"""
    start_at = race_meta.get("startAt")
    if not start_at:
        return None
    try:
        hour = datetime.fromtimestamp(int(start_at), JST).hour
    except (ValueError, OSError, OverflowError):
        return None
    if hour < 11:
        return "hourTypeMorning"
    if hour < 17:
        return "hourTypeNormal"
    if hour < 21:
        return "hourTypeNight"
    return "hourTypeMidnight"


# WINTICKETの天気コード(数値文字列)→日本語ラベル
WEATHER_CODES = {"100": "晴", "200": "曇", "300": "雨", "400": "雪", "500": "霧"}


def _weather(queries: dict, race_meta: dict) -> dict | None:
    """天気・風速・降水を取り出す。降雨は逃げ・先行有利の材料。"""
    payload = queries.get("FETCH_KEIRIN_RACE_WEATHER") or {}
    raw = str(payload.get("weather") or race_meta.get("weather") or "").strip()
    if not raw and not payload:
        return None
    label = WEATHER_CODES.get(raw, raw)
    try:
        precip = float(payload.get("precipitation") or 0)
    except (TypeError, ValueError):
        precip = 0.0
    is_rain = raw in {"300", "400"} or any(w in label for w in ("雨", "雪")) or precip > 0
    return {
        "weather": label,
        "temperature": payload.get("temperature"),
        "wind_speed": payload.get("windSpeed") or race_meta.get("windSpeed"),
        "wind_direction": payload.get("windDirection"),
        "precipitation": payload.get("precipitation"),
        "is_rain": is_rain,
    }


def _rate_block(value) -> dict | None:
    """{first,second,third,others,total,...Percentage} 形式の成績ブロックを正規化する。"""
    if not isinstance(value, dict) or int(value.get("total") or 0) <= 0:
        return None
    total = int(value.get("total") or 0)
    first = int(value.get("first") or 0)
    top3 = first + int(value.get("second") or 0) + int(value.get("third") or 0)
    return {
        "total": total,
        "win_rate": round(first / total, 3),
        "top3_rate": round(top3 / total, 3),
    }


def _position_stats(record: dict) -> dict:
    """ライン位置別(先頭/番手/3番手/単騎)の勝率・3着内率。"""
    return {
        key: block
        for key, source in (
            ("front", "linePositionFirst"),
            ("second", "linePositionSecond"),
            ("third", "linePositionThird"),
            ("single", "lineSingleHorseman"),
        )
        if (block := _rate_block(record.get(source)))
    }


def _venue_stats(record: dict) -> dict | None:
    """当該バンクでの直近成績(latestVenueResults)から勝率・3着内率を出す。"""
    finishes: list[int] = []
    for cup in record.get("latestVenueResults") or []:
        if not isinstance(cup, dict):
            continue
        for item in cup.get("raceResults") or []:
            if isinstance(item, dict):
                order = item.get("order")
                if isinstance(order, int) and 1 <= order <= 9:
                    finishes.append(order)
                elif item.get("hasAccident"):
                    finishes.append(9)
    if not finishes:
        return None
    total = len(finishes)
    return {
        "total": total,
        "win_rate": round(sum(1 for f in finishes if f == 1) / total, 3),
        "top3_rate": round(sum(1 for f in finishes if f <= 3) / total, 3),
    }


def _car_by_player(common: dict) -> dict[str, int]:
    mapping = {}
    for entry in common.get("entries") or []:
        player_id = str(entry.get("playerId") or "")
        number = entry.get("number")
        if player_id and number is not None:
            mapping[player_id] = int(number)
    return mapping


def _interviews_by_player(payload: dict | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for interview in (payload or {}).get("interviews") or []:
        player_id = str(interview.get("playerId") or "")
        answers = []
        for thread in interview.get("threads") or []:
            answer = str(thread.get("answer") or "").strip()
            if answer:
                answers.append(answer)
        if player_id and answers:
            result[player_id] = " ".join(answers)
    return result


def _ex_summary(record: dict) -> dict[str, float]:
    summary = {}
    for key in EX_KEYS:
        value = record.get(key)
        if isinstance(value, dict) and int(value.get("total") or 0) > 0:
            summary[key] = float(value.get("percentage") or 0.0)
    return summary


def _recent_form(record: dict) -> list[int]:
    """直近の着順リスト。落車・失格などは9扱い。今開催→前開催の順に最大6走。"""
    finishes: list[int] = []

    def add(items) -> None:
        for item in items or []:
            if len(finishes) >= 6:
                return
            if not isinstance(item, dict):
                continue
            order = item.get("order")
            if isinstance(order, int) and 1 <= order <= 9:
                finishes.append(min(order, 9))
            elif item.get("hasAccident"):
                finishes.append(9)

    add(record.get("currentCupResults"))
    add(record.get("previousCupResults"))
    for cup in record.get("latestCupResults") or []:
        if isinstance(cup, dict):
            add(cup.get("raceResults"))
    return finishes[:6]


def _results_detail(common: dict, car_by_player: dict[str, int]) -> list[dict]:
    detail = []
    for row in common.get("results") or []:
        car_no = car_by_player.get(str(row.get("playerId") or ""))
        if car_no is None:
            continue
        detail.append(
            {
                "car_no": car_no,
                "order": int(row.get("order") or 0),
                "factor": str(row.get("factor") or ""),
                "final_half": str(row.get("finalHalfRecord") or ""),
                "accident": str(row.get("accidentName") or row.get("accident") or ""),
            }
        )
    detail.sort(key=lambda item: item.get("order") or 99)
    return detail
