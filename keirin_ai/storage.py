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
        """
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
            race_date, weather, lineup_json, result_json, raw_quality_json, fetched_at
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            fetched_at=excluded.fetched_at
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
        ),
    )

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
                finish_position, is_win, updated_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                updated_at=excluded.updated_at
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
