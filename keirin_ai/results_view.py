from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


JST = timezone(timedelta(hours=9))


def build_results_payload(conn: sqlite3.Connection, date: str | None = None, data_dir: Path | str | None = None) -> dict:
    dates = _available_dates(conn)
    if not dates:
        return {"ok": True, "date": None, "dates": [], "summary": _summary([]), "venues": []}

    target = _normalize_date(date) if date else None
    if target not in dates:
        target = dates[0]

    time_map = _start_time_map(data_dir)
    races = [_race_report(conn, row, time_map) for row in _races_on(conn, target)]
    races = [race for race in races if race is not None]
    venues: dict[str, list[dict]] = {}
    for race in races:
        venues.setdefault(race["venue"] or "未設定", []).append(race)
    venue_list = [
        {"venue": venue, "races": sorted(items, key=lambda r: (r["start_time"] or "99:99", r["race_no"] or 0))}
        for venue, items in venues.items()
    ]
    venue_list.sort(key=lambda v: v["races"][0]["start_time"] or "99:99")

    return {
        "ok": True,
        "date": target,
        "dates": dates[:14],
        "summary": _summary(races),
        "venues": venue_list,
        "record": build_record_summary(conn),
        "conditions": build_condition_stats(conn),
        "calibration": build_calibration(conn),
    }


def build_record_summary(conn: sqlite3.Connection) -> dict:
    """AI成績の通算集計: 本日 / 直近7日 / 通年(今年)。

    レースごとに「最初に保存された予想」と確定結果を突き合わせる(1クエリ)。
    """
    rows = conn.execute(
        """
        select r.race_date, r.result_json, r.payouts_json, p.ranking_json, p.tickets_json
        from races r
        join (select race_key, min(id) as first_id from predictions group by race_key) fp
          on fp.race_key = r.race_key
        join predictions p on p.id = fp.first_id
        where r.result_json is not null and r.result_json != ''
        """
    ).fetchall()

    today = datetime.now(JST).date()
    # 週は月曜始まり。今週=直近の月曜〜、先週=その前の月〜日
    this_monday = today - timedelta(days=today.weekday())
    last_monday = this_monday - timedelta(days=7)
    buckets = {
        "today": {"label": "今日", "races": []},
        "week": {"label": "今週", "races": []},
        "last_week": {"label": "先週", "races": []},
        "total": {"label": "通算", "races": []},
    }
    for row in rows:
        iso = _normalize_date(row["race_date"])
        if not iso:
            continue
        race_date = datetime.strptime(iso, "%Y-%m-%d").date()
        hit = _hit_flags(row)
        if hit is None:
            continue
        buckets["total"]["races"].append(hit)
        if race_date == today:
            buckets["today"]["races"].append(hit)
        if race_date >= this_monday:
            buckets["week"]["races"].append(hit)
        elif last_monday <= race_date < this_monday:
            buckets["last_week"]["races"].append(hit)

    def stat(items: list[dict]) -> dict:
        n = len(items)
        honmei = sum(1 for hit in items if hit["honmei"])
        top3 = sum(1 for hit in items if hit["in_top3"])
        trifecta = sum(1 for hit in items if hit["trifecta"])
        # ROI: 2車単軸1着固定の上位2点を各100円ずつ平坦買いした場合の回収率
        priced = [hit for hit in items if hit.get("exacta_stake")]
        ex_stake = sum(hit["exacta_stake"] for hit in priced)
        ex_payout = sum(hit["exacta_payout"] for hit in priced)
        ex_hits = sum(1 for hit in priced if hit.get("exacta_payout", 0) > 0)
        tri_priced = [hit for hit in items if hit.get("trifecta_stake")]
        tri_stake = sum(hit["trifecta_stake"] for hit in tri_priced)
        tri_payout = sum(hit["trifecta_payout"] for hit in tri_priced)
        return {
            "settled": n,
            "honmei_hits": honmei,
            "honmei_rate": round(honmei / n, 4) if n else None,
            "in_top3_rate": round(top3 / n, 4) if n else None,
            "trifecta_hits": trifecta,
            "trifecta_rate": round(trifecta / n, 4) if n else None,
            "exacta_roi": round(ex_payout / ex_stake, 4) if ex_stake else None,
            "exacta_priced": len(priced),
            "exacta_hits": ex_hits,
            "exacta_hit_rate": round(ex_hits / len(priced), 4) if priced else None,
            "trifecta_roi": round(tri_payout / tri_stake, 4) if tri_stake else None,
        }

    stats = {key: stat(buckets[key]["races"]) for key in buckets}
    baseline = stats["total"]["exacta_roi"]

    def form_of(period: dict) -> str | None:
        """好調/不調は通算(平常)回収率を基準にした相対評価。いつもより良ければ好調。"""
        roi = period.get("exacta_roi")
        if roi is None or (period.get("exacta_priced") or 0) < 3 or baseline is None:
            return None
        if roi >= baseline + 0.08:
            return "hot"
        if roi <= baseline - 0.08:
            return "cold"
        return "even"

    for key in ("today", "week", "last_week"):
        stats[key]["form"] = form_of(stats[key])
    # 通算は基準そのものなので好不調アイコンは付けない
    stats["total"]["form"] = None

    return {
        "as_of": today.isoformat(),
        "today": {"label": buckets["today"]["label"], **stats["today"]},
        "week": {"label": buckets["week"]["label"], **stats["week"]},
        "last_week": {"label": buckets["last_week"]["label"], **stats["last_week"]},
        "total": {"label": buckets["total"]["label"], **stats["total"]},
    }


_HOUR_LABELS = {
    "hourTypeMorning": "モーニング",
    "hourTypeNormal": "デイ",
    "hourTypeNight": "ナイター",
    "hourTypeMidnight": "ミッドナイト",
}


def _settled_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """答え合わせ対象(最初の予想×確定結果)の行。条件列付き。"""
    return conn.execute(
        """
        select r.race_date, r.venue, r.hour_type, r.race_class_official,
               r.result_json, r.payouts_json, p.ranking_json, p.tickets_json
        from races r
        join (select race_key, min(id) as first_id from predictions group by race_key) fp
          on fp.race_key = r.race_key
        join predictions p on p.id = fp.first_id
        where r.result_json is not null and r.result_json != ''
        """
    ).fetchall()


def build_condition_stats(conn: sqlite3.Connection) -> list[dict]:
    """AIの得意条件: 会場/時間帯/車立て/級班ごとの2車単平坦買い回収率。

    実払戻(確定オッズ)があるレースだけで集計する。n<10の条件はノイズなので出さない。
    """
    groups: dict[tuple[str, str], dict] = {}
    for row in _settled_rows(conn):
        hit = _hit_flags(row)
        if not hit or not hit.get("exacta_stake"):
            continue
        ranking = _json_or(row["ranking_json"], [])
        cls = str(row["race_class_official"] or "")
        girls = "ガール" in cls
        grade = "ガールズ" if girls else ("S級" if "S級" in cls[:4] else "A級" if "A級" in cls[:4] else "その他")
        keys = [
            ("会場", row["venue"] or "不明"),
            ("時間帯", _HOUR_LABELS.get(str(row["hour_type"] or ""), "デイ")),
            ("車立て", f"{len(ranking)}車"),
            ("級班", grade),
        ]
        for category, label in keys:
            g = groups.setdefault((category, label), {"stake": 0, "payout": 0, "n": 0, "hits": 0})
            g["stake"] += hit["exacta_stake"]
            g["payout"] += hit["exacta_payout"]
            g["n"] += 1
            g["hits"] += 1 if hit["exacta_payout"] > 0 else 0
    out = []
    for (category, label), g in groups.items():
        if g["n"] < 10 or not g["stake"]:
            continue
        out.append(
            {
                "category": category,
                "label": label,
                "races": g["n"],
                "roi": round(g["payout"] / g["stake"], 4),
                "hit_rate": round(g["hits"] / g["n"], 4),
            }
        )
    out.sort(key=lambda item: -item["roi"])
    return out


def build_calibration(conn: sqlite3.Connection) -> list[dict]:
    """較正カーブ: AIが表示した本命勝率(ビン0.1刻み)ごとの実際の的中率。"""
    bins: dict[int, dict] = {}
    for row in _settled_rows(conn):
        ranking = _json_or(row["ranking_json"], [])
        order = (_json_or(row["result_json"], {}) or {}).get("finish_order") or []
        if not ranking or not order:
            continue
        top = ranking[0]
        try:
            prob = float(top.get("win_probability") or top.get("probability") or 0)
            won = int(top.get("car_no") or 0) == int(order[0])
        except (TypeError, ValueError):
            continue
        if prob <= 0:
            continue
        b = min(9, int(prob * 10))
        g = bins.setdefault(b, {"n": 0, "hits": 0, "prob_sum": 0.0})
        g["n"] += 1
        g["hits"] += 1 if won else 0
        g["prob_sum"] += prob
    out = []
    for b in sorted(bins):
        g = bins[b]
        if g["n"] < 5:
            continue
        out.append(
            {
                "bin": f"{b * 10}-{b * 10 + 10}%",
                "predicted": round(g["prob_sum"] / g["n"], 4),
                "actual": round(g["hits"] / g["n"], 4),
                "races": g["n"],
            }
        )
    return out


def _hit_flags(row: sqlite3.Row) -> dict | None:
    ranking = _json_or(row["ranking_json"], [])
    if not ranking:
        return None
    order = (_json_or(row["result_json"], {}) or {}).get("finish_order") or []
    if len(order) < 3:
        return None
    top_car = int(ranking[0].get("car_no") or 0)
    tickets = [_ticket_label(t) for t in _json_or(row["tickets_json"], [])[:6]]
    actual3 = "-".join(str(car) for car in order[:3])
    actual2 = "-".join(str(car) for car in order[:2])
    flags = {
        "honmei": top_car == int(order[0]),
        "in_top3": top_car in [int(car) for car in order[:3]],
        "trifecta": actual3 in tickets,
    }
    # ROI用: 払戻(確定オッズ)がある場合だけ、平坦買いの投資額と払戻を積む
    payouts = _json_or(row["payouts_json"], None) if "payouts_json" in row.keys() else None
    if isinstance(payouts, dict):
        # 2車単: 軸(予想1位)1着固定で相手上位2点(各100円=計200円)
        cars = [int(r.get("car_no") or 0) for r in ranking]
        exacta_picks = [f"{cars[0]}-{cars[1]}", f"{cars[0]}-{cars[2]}"] if len(cars) >= 3 else []
        if payouts.get("exacta") is not None and exacta_picks:
            flags["exacta_stake"] = 100 * len(exacta_picks)
            flags["exacta_payout"] = int(round(payouts["exacta"] * 100)) if actual2 in exacta_picks else 0
        # 3連単: AI買い目6点(各100円=計600円)
        if payouts.get("trifecta") is not None and tickets:
            flags["trifecta_stake"] = 100 * len(tickets)
            flags["trifecta_payout"] = int(round(payouts["trifecta"] * 100)) if actual3 in tickets else 0
    return flags


def _available_dates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        select distinct race_date from races
        where race_date is not null and race_date != ''
          and race_key in (select race_key from predictions)
        """
    ).fetchall()
    dates = {iso for row in rows if (iso := _normalize_date(row["race_date"]))}
    return sorted(dates, reverse=True)


def _races_on(conn: sqlite3.Connection, iso_date: str) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        select race_key, venue, race_no, race_date, source_url, result_json
        from races
        where race_key in (select race_key from predictions)
        order by race_no
        """
    ).fetchall()
    return [row for row in rows if _normalize_date(row["race_date"]) == iso_date]


def _start_time_map(data_dir: Path | str | None) -> dict[str, str]:
    if not data_dir:
        return {}
    mapping: dict[str, str] = {}
    for path in sorted(Path(data_dir).glob("forecast_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for item in payload.get("forecasts", []):
            key = item.get("race_key")
            start = item.get("start_time")
            if key and start:
                mapping[key] = start
    return mapping


def _race_report(conn: sqlite3.Connection, row: sqlite3.Row, time_map: dict[str, str]) -> dict | None:
    # 最初の予想 = レース前にAIが出した答え。再学習後の上書きは評価に使わない。
    pred = conn.execute(
        """
        select ranking_json, tickets_json, created_at from predictions
        where race_key=? order by id asc limit 1
        """,
        (row["race_key"],),
    ).fetchone()
    if not pred:
        return None
    ranking = _json_or(pred["ranking_json"], [])
    tickets = _json_or(pred["tickets_json"], [])
    if not ranking:
        return None

    top = ranking[0]
    top_pick = {
        "car_no": top.get("car_no"),
        "name": top.get("name") or "",
        "probability": round(float(top.get("win_probability") or top.get("probability") or 0), 4),
    }
    # 答え合わせはAIが提示した上位6点(運用モードの最大点数)で判定する
    ticket_labels = [_ticket_label(t) for t in tickets[:6]]
    ticket_labels = [label for label in ticket_labels if label]

    start_time = time_map.get(row["race_key"], "")
    result = _json_or(row["result_json"], None)
    order = (result or {}).get("finish_order") or []

    if not order:
        status = "pending"
        hits = {"honmei": None, "in_top3": None, "trifecta": None, "trifecta_label": None}
    else:
        actual3 = "-".join(str(car) for car in order[:3])
        trifecta_label = actual3 if actual3 in ticket_labels else None
        honmei = int(top_pick["car_no"] or 0) == int(order[0])
        in_top3 = int(top_pick["car_no"] or 0) in [int(c) for c in order[:3]]
        hits = {
            "honmei": honmei,
            "in_top3": in_top3,
            "trifecta": trifecta_label is not None,
            "trifecta_label": trifecta_label,
        }
        if trifecta_label:
            status = "hit_trifecta"
        elif honmei:
            status = "hit_honmei"
        elif in_top3:
            status = "in_top3"
        else:
            status = "miss"

    return {
        "race_key": row["race_key"],
        "venue": row["venue"] or "",
        "race_no": row["race_no"],
        "start_time": start_time,
        "url": row["source_url"] or "",
        "top_pick": top_pick,
        "tickets": ticket_labels,
        "result_order": order[:3],
        "status": status,
        "hits": hits,
    }


def _summary(races: list[dict]) -> dict:
    settled = [race for race in races if race["status"] != "pending"]
    honmei = sum(1 for race in settled if race["hits"]["honmei"])
    in_top3 = sum(1 for race in settled if race["hits"]["in_top3"])
    trifecta = sum(1 for race in settled if race["hits"]["trifecta"])
    return {
        "races": len(races),
        "settled": len(settled),
        "pending": len(races) - len(settled),
        "honmei_hits": honmei,
        "honmei_rate": round(honmei / len(settled), 4) if settled else None,
        "in_top3_hits": in_top3,
        "in_top3_rate": round(in_top3 / len(settled), 4) if settled else None,
        "trifecta_hits": trifecta,
        "trifecta_rate": round(trifecta / len(settled), 4) if settled else None,
    }


def _normalize_date(raw: str | None) -> str | None:
    raw = str(raw or "").strip()
    if not raw:
        return None
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if not match:
        match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", raw)
    if not match:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    try:
        return datetime(year, month, day).date().isoformat()
    except ValueError:
        return None


def _ticket_label(ticket) -> str:
    if isinstance(ticket, str):
        return ticket
    if isinstance(ticket, dict):
        return ticket.get("label") or "-".join(str(c) for c in ticket.get("cars", []))
    return ""


def _json_or(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default
