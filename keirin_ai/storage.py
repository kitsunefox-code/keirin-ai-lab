from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from keirin_ai.emotion import analyze_comment
from keirin_ai.features import build_feature_row


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "keirin_learning.sqlite3"


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        create table if not exists races (
            race_key text primary key,
            source_name text,
            source_url text unique,
            title text,
            venue text,
            event text,
            race_no integer,
            race_class text,
            race_date text,
            weather text,
            lineup_json text,
            result_json text,
            raw_quality_json text,
            fetched_at text not null
        );

        create table if not exists entries (
            race_key text not null,
            car_no integer not null,
            name text,
            prefecture text,
            class text,
            age integer,
            term integer,
            ai_mark text,
            racing_score real,
            style text,
            gear text,
            comment_excerpt text,
            emotion_json text,
            features_json text,
            finish_position integer,
            is_win integer,
            updated_at text not null,
            player_id text,
            primary key (race_key, car_no)
        );

        create table if not exists predictions (
            id integer primary key autoincrement,
            race_key text not null,
            created_at text not null,
            model_name text,
            model_version text,
            top_car integer,
            ranking_json text,
            tickets_json text
        );

        create table if not exists source_documents (
            id integer primary key autoincrement,
            url text unique not null,
            domain text,
            kind text,
            title text,
            fingerprint text,
            excerpt text,
            tags_json text,
            signal_score real,
            word_count integer,
            learned_at text not null
        );

        create table if not exists player_form (
            player_id text not null,
            race_key text not null,
            name text,
            race_date text,
            finish integer,
            factor text,
            post_comment text,
            updated_at text not null,
            primary key (player_id, race_key)
        );

        create table if not exists venues (
            venue_id text primary key,
            name text,
            slug text,
            track_distance integer,
            straight real,
            angle_center text,
            angle_straight text,
            home_width real,
            back_width real,
            center_width real,
            is_indoor integer,
            kimarite_json text,
            bank_bias real,
            bank_feature text,
            net_notes text,
            net_checked_at text,
            updated_at text not null
        );
        """
    )
    _migrate_columns(conn)
    conn.commit()


def _migrate_columns(conn: sqlite3.Connection) -> None:
    """既存DBに後から足した列を補う(古いDBからの移行用)。"""
    entry_columns = {row["name"] for row in conn.execute("pragma table_info(entries)").fetchall()}
    if "player_id" not in entry_columns:
        conn.execute("alter table entries add column player_id text")
    race_columns = {row["name"] for row in conn.execute("pragma table_info(races)").fetchall()}
    for column, ddl in (
        ("race_class_official", "text"),
        ("venue_id", "text"),
        ("hour_type", "text"),
        ("weather_json", "text"),
        ("payouts_json", "text"),
    ):
        if column not in race_columns:
            conn.execute(f"alter table races add column {column} {ddl}")


def save_race_payouts(conn: sqlite3.Connection, race_key: str, payouts: dict) -> None:
    """勝ち組み合わせの確定オッズ(=100円あたり払戻)を保存する。{"trifecta": 162.5, "exacta": 8.2}"""
    if not payouts:
        return
    conn.execute(
        "update races set payouts_json=? where race_key=?",
        (json.dumps(payouts, ensure_ascii=False), race_key),
    )
    conn.commit()


def save_player_form(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """レース後の着順・決まり手・談話を選手単位で蓄積する。"""
    saved = 0
    now = _now()
    for row in rows:
        player_id = str(row.get("player_id") or "").strip()
        race_key_value = str(row.get("race_key") or "").strip()
        if not player_id or not race_key_value:
            continue
        conn.execute(
            """
            insert into player_form (
                player_id, race_key, name, race_date, finish, factor, post_comment, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(player_id, race_key) do update set
                name=excluded.name,
                race_date=excluded.race_date,
                finish=coalesce(excluded.finish, player_form.finish),
                factor=coalesce(nullif(excluded.factor, ''), player_form.factor),
                post_comment=coalesce(nullif(excluded.post_comment, ''), player_form.post_comment),
                updated_at=excluded.updated_at
            """,
            (
                player_id,
                race_key_value,
                row.get("name"),
                row.get("race_date"),
                row.get("finish"),
                row.get("factor") or "",
                row.get("post_comment") or "",
                now,
            ),
        )
        saved += 1
    conn.commit()
    return saved


def save_venue(conn: sqlite3.Connection, venue: dict) -> None:
    """競輪場のバンク特徴をDBへ記憶する(場ごとに1度。ネット調査分は上書きしない)。"""
    venue_id = str(venue.get("venue_id") or "").strip()
    if not venue_id:
        return
    now = _now()
    conn.execute(
        """
        insert into venues (
            venue_id, name, slug, track_distance, straight, angle_center, angle_straight,
            home_width, back_width, center_width, is_indoor, kimarite_json, bank_bias,
            bank_feature, updated_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(venue_id) do update set
            name=coalesce(nullif(excluded.name, ''), venues.name),
            slug=coalesce(nullif(excluded.slug, ''), venues.slug),
            track_distance=coalesce(excluded.track_distance, venues.track_distance),
            straight=coalesce(excluded.straight, venues.straight),
            angle_center=coalesce(nullif(excluded.angle_center, ''), venues.angle_center),
            angle_straight=coalesce(nullif(excluded.angle_straight, ''), venues.angle_straight),
            home_width=coalesce(excluded.home_width, venues.home_width),
            back_width=coalesce(excluded.back_width, venues.back_width),
            center_width=coalesce(excluded.center_width, venues.center_width),
            is_indoor=coalesce(excluded.is_indoor, venues.is_indoor),
            kimarite_json=coalesce(nullif(excluded.kimarite_json, ''), venues.kimarite_json),
            bank_bias=coalesce(excluded.bank_bias, venues.bank_bias),
            bank_feature=coalesce(nullif(excluded.bank_feature, ''), venues.bank_feature),
            updated_at=excluded.updated_at
        """,
        (
            venue_id,
            venue.get("name"),
            venue.get("slug"),
            venue.get("track_distance"),
            venue.get("straight"),
            venue.get("angle_center"),
            venue.get("angle_straight"),
            venue.get("home_width"),
            venue.get("back_width"),
            venue.get("center_width"),
            1 if venue.get("is_indoor") else 0 if venue.get("is_indoor") is not None else None,
            _dump(venue.get("kimarite")) if venue.get("kimarite") is not None else None,
            venue.get("bank_bias"),
            venue.get("bank_feature"),
            now,
        ),
    )
    conn.commit()


def save_venue_net_notes(conn: sqlite3.Connection, venue_id: str, notes: str) -> None:
    """ネット調査で得たバンク傾向メモを記憶する。"""
    conn.execute(
        "update venues set net_notes=?, net_checked_at=? where venue_id=?",
        (notes, _now(), str(venue_id)),
    )
    conn.commit()


def get_venue(conn: sqlite3.Connection, venue_id: str) -> dict | None:
    row = conn.execute("select * from venues where venue_id=?", (str(venue_id),)).fetchone()
    return _venue_row(row) if row else None


def all_venues(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("select * from venues order by name").fetchall()
    return [_venue_row(row) for row in rows]


def venue_ids_missing_net_notes(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "select venue_id from venues where net_notes is null or net_notes = '' order by updated_at"
    ).fetchall()
    return [row["venue_id"] for row in rows]


def _venue_row(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["kimarite"] = _json_or(data.pop("kimarite_json", None), None)
    data["is_indoor"] = bool(data["is_indoor"]) if data.get("is_indoor") is not None else None
    return data


def line_partner_record(conn: sqlite3.Connection, player_a: str, player_b: str) -> dict | None:
    """2選手が過去に同じラインを組んだレースでの「連携成績」(ライン内どちらかが3着内に入った割合)。"""
    player_a, player_b = str(player_a or ""), str(player_b or "")
    if not player_a or not player_b or player_a == player_b:
        return None
    rows = conn.execute(
        """
        select e1.race_key as race_key, e1.car_no as car_a, e2.car_no as car_b,
               e1.finish_position as finish_a, e2.finish_position as finish_b,
               r.lineup_json as lineup_json
        from entries e1
        join entries e2 on e1.race_key = e2.race_key and e2.player_id = ?
        join races r on r.race_key = e1.race_key
        where e1.player_id = ? and e1.finish_position is not null and e2.finish_position is not null
        """,
        (player_b, player_a),
    ).fetchall()
    races = 0
    top3 = 0
    for row in rows:
        lineup = _json_or(row["lineup_json"], [])
        if not _same_line(lineup, row["car_a"], row["car_b"]):
            continue
        races += 1
        if (row["finish_a"] and row["finish_a"] <= 3) or (row["finish_b"] and row["finish_b"] <= 3):
            top3 += 1
    if races == 0:
        return None
    return {"races": races, "top3": top3, "top3_rate": round(top3 / races, 3)}


def _same_line(lineup: list, car_a: int, car_b: int) -> bool:
    for line in lineup or []:
        if not isinstance(line, list):
            continue
        if car_a in line and car_b in line:
            return True
    return False


def _json_or(value: object, default: object) -> object:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def attach_line_partner_stats(conn: sqlite3.Connection, race: dict) -> None:
    """出走表のライン構成から隣接ペアの連携成績を各選手へ付与する。"""
    entries_by_car = {int(e.get("car_no") or 0): e for e in race.get("entrants", [])}
    for line in race.get("lineup") or []:
        members = [entries_by_car.get(int(car)) for car in line if int(car) in entries_by_car]
        for index, entrant in enumerate(members):
            if not entrant or not entrant.get("player_id"):
                continue
            partner = members[index + 1] if index + 1 < len(members) else (members[index - 1] if index > 0 else None)
            if not partner or not partner.get("player_id"):
                continue
            record = line_partner_record(conn, entrant["player_id"], partner["player_id"])
            if record:
                entrant["partner_record"] = {**record, "partner_name": partner.get("name") or ""}


def latest_player_form(conn: sqlite3.Connection, player_ids: list[str]) -> dict[str, dict]:
    """各選手の直近のレース後情報(談話・着順・決まり手)を返す。"""
    result: dict[str, dict] = {}
    for player_id in {str(pid) for pid in player_ids if pid}:
        row = conn.execute(
            """
            select player_id, name, race_date, finish, factor, post_comment
            from player_form
            where player_id=?
            order by updated_at desc, race_key desc
            limit 1
            """,
            (player_id,),
        ).fetchone()
        if row:
            result[player_id] = dict(row)
    return result


def save_race(conn: sqlite3.Connection, race: dict, prediction: dict | None = None) -> str:
    now = _now()
    key = race_key(race)
    result = normalize_result(race.get("result"))
    conn.execute(
        """
        insert into races (
            race_key, source_name, source_url, title, venue, event, race_no, race_class,
            race_date, weather, lineup_json, result_json, raw_quality_json, fetched_at,
            race_class_official, venue_id, hour_type, weather_json
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(race_key) do update set
            source_name=excluded.source_name,
            source_url=excluded.source_url,
            title=excluded.title,
            venue=excluded.venue,
            event=excluded.event,
            race_no=excluded.race_no,
            race_class=excluded.race_class,
            race_date=excluded.race_date,
            weather=excluded.weather,
            lineup_json=excluded.lineup_json,
            result_json=coalesce(excluded.result_json, races.result_json),
            raw_quality_json=excluded.raw_quality_json,
            fetched_at=excluded.fetched_at,
            race_class_official=coalesce(nullif(excluded.race_class_official, ''), races.race_class_official),
            venue_id=coalesce(nullif(excluded.venue_id, ''), races.venue_id),
            hour_type=coalesce(nullif(excluded.hour_type, ''), races.hour_type),
            weather_json=coalesce(nullif(excluded.weather_json, ''), races.weather_json)
        """,
        (
            key,
            race.get("source", {}).get("name"),
            race.get("source", {}).get("url"),
            race.get("title"),
            race.get("venue"),
            race.get("event"),
            race.get("race_no"),
            race.get("race_class"),
            race.get("date"),
            race.get("weather"),
            _dump(race.get("lineup", [])),
            _dump(result) if result else None,
            _dump(race.get("raw_quality", {})),
            now,
            str(race.get("race_class_official") or ""),
            str((race.get("bank") or {}).get("venue_id") or ""),
            str(race.get("hour_type") or ""),
            _dump(race.get("weather_info")) if race.get("weather_info") else "",
        ),
    )

    for venue in race.get("all_venues") or []:
        save_venue(conn, venue)

    positions = result.get("positions", {}) if result else {}
    for entrant in race.get("entrants", []):
        car_no = int(entrant.get("car_no") or 0)
        emotion = analyze_comment(entrant.get("comment"))
        features = build_feature_row(race, entrant, emotion)
        finish_position = positions.get(str(car_no)) or positions.get(car_no)
        conn.execute(
            """
            insert into entries (
                race_key, car_no, name, prefecture, class, age, term, ai_mark,
                racing_score, style, gear, comment_excerpt, emotion_json, features_json,
                finish_position, is_win, updated_at, player_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            on conflict(race_key, car_no) do update set
                name=excluded.name,
                prefecture=excluded.prefecture,
                class=excluded.class,
                age=excluded.age,
                term=excluded.term,
                ai_mark=excluded.ai_mark,
                racing_score=excluded.racing_score,
                style=excluded.style,
                gear=excluded.gear,
                comment_excerpt=excluded.comment_excerpt,
                emotion_json=excluded.emotion_json,
                features_json=excluded.features_json,
                finish_position=coalesce(excluded.finish_position, entries.finish_position),
                is_win=coalesce(excluded.is_win, entries.is_win),
                updated_at=excluded.updated_at,
                player_id=coalesce(nullif(excluded.player_id, ''), entries.player_id)
            """,
            (
                key,
                car_no,
                entrant.get("name"),
                entrant.get("prefecture"),
                entrant.get("class"),
                entrant.get("age"),
                entrant.get("term"),
                entrant.get("ai_mark"),
                entrant.get("racing_score"),
                entrant.get("style"),
                entrant.get("gear"),
                _excerpt(entrant.get("comment")),
                _dump(emotion),
                _dump(features),
                finish_position,
                1 if finish_position == 1 else (0 if finish_position else None),
                now,
                str(entrant.get("player_id") or ""),
            ),
        )

    if prediction:
        rankings = prediction.get("rankings", [])
        top_car = rankings[0].get("car_no") if rankings else None
        model = prediction.get("model", {})
        conn.execute(
            """
            insert into predictions (
                race_key, created_at, model_name, model_version, top_car, ranking_json, tickets_json
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                now,
                model.get("name"),
                model.get("version"),
                top_car,
                _dump(rankings),
                _dump(prediction.get("tickets", [])),
            ),
        )

    conn.commit()
    return key


def apply_manual_result(conn: sqlite3.Connection, race_key_value: str, order: list[int]) -> dict:
    result = result_from_order(order, source="manual")
    conn.execute("update races set result_json=? where race_key=?", (_dump(result), race_key_value))
    for pos, car_no in enumerate(order, start=1):
        conn.execute(
            "update entries set finish_position=?, is_win=? where race_key=? and car_no=?",
            (pos, 1 if pos == 1 else 0, race_key_value, car_no),
        )
    conn.commit()
    return result


def training_rows(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        select race_key, car_no, name, features_json, finish_position, is_win
        from entries
        where finish_position is not null and features_json is not null
        order by race_key, car_no
        """
    ).fetchall()
    return [
        {
            "race_key": row["race_key"],
            "car_no": row["car_no"],
            "name": row["name"],
            "features": json.loads(row["features_json"]),
            "finish_position": row["finish_position"],
            "label": int(row["is_win"] or 0),
        }
        for row in rows
    ]


def learning_status(conn: sqlite3.Connection) -> dict:
    race_count = conn.execute("select count(*) from races").fetchone()[0]
    entry_count = conn.execute("select count(*) from entries").fetchone()[0]
    result_races = conn.execute("select count(distinct race_key) from entries where finish_position is not null").fetchone()[0]
    result_entries = conn.execute("select count(*) from entries where finish_position is not null").fetchone()[0]
    prediction_count = conn.execute("select count(*) from predictions").fetchone()[0]
    document_count = conn.execute("select count(*) from source_documents").fetchone()[0]
    return {
        "races": race_count,
        "entries": entry_count,
        "result_races": result_races,
        "result_entries": result_entries,
        "predictions": prediction_count,
        "documents": document_count,
        "db_path": str(DEFAULT_DB_PATH),
    }


def save_source_document(conn: sqlite3.Connection, doc: dict) -> int:
    now = _now()
    conn.execute(
        """
        insert into source_documents (
            url, domain, kind, title, fingerprint, excerpt, tags_json,
            signal_score, word_count, learned_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        on conflict(url) do update set
            domain=excluded.domain,
            kind=excluded.kind,
            title=excluded.title,
            fingerprint=excluded.fingerprint,
            excerpt=excluded.excerpt,
            tags_json=excluded.tags_json,
            signal_score=excluded.signal_score,
            word_count=excluded.word_count,
            learned_at=excluded.learned_at
        """,
        (
            doc.get("url"),
            doc.get("domain"),
            doc.get("kind"),
            doc.get("title"),
            doc.get("fingerprint"),
            doc.get("excerpt"),
            _dump(doc.get("tags", {})),
            doc.get("signal_score"),
            doc.get("word_count"),
            now,
        ),
    )
    conn.commit()
    row = conn.execute("select id from source_documents where url=?", (doc.get("url"),)).fetchone()
    return int(row["id"])


def race_key(race: dict) -> str:
    url = race.get("source", {}).get("url") or ""
    if url:
        parsed = urlparse(url)
        parts = [part for part in parsed.path.split("/") if part]
        if "racecard" in parts:
            idx = parts.index("racecard")
            tail = parts[idx + 1 :]
            if len(tail) >= 3:
                return "winticket:" + ":".join(tail[:4])
        return f"url:{url}"
    return f"manual:{race.get('date')}:{race.get('venue')}:{race.get('race_no')}"


def result_from_order(order: list[int], source: str = "manual") -> dict:
    clean = [int(car) for car in order if int(car) > 0]
    return {
        "finish_order": clean,
        "positions": {str(car): pos for pos, car in enumerate(clean, start=1)},
        "source": source,
    }


def normalize_result(result: dict | None) -> dict | None:
    if not result:
        return None
    if "positions" in result and "finish_order" in result:
        return result
    order = result.get("finish_order") or result.get("order") or []
    return result_from_order([int(car) for car in order], source=result.get("source", "parsed"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _excerpt(text: str | None, limit: int = 160) -> str:
    text = (text or "").strip()
    return text[:limit]
