from __future__ import annotations

from keirin_ai.emotion import analyze_comment


FEATURE_NAMES = [
    "bias",
    "racing_score",
    "win_rate",
    "two_rate",
    "three_rate",
    "start_count",
    "home_count",
    "back_count",
    "style_escape",
    "style_pursuit",
    "style_allround",
    "ai_honmei",
    "ai_taiko",
    "ai_tanana",
    "ai_renshita",
    "line_len",
    "line_front",
    "line_second",
    "line_third_plus",
    "emotion_score",
    "emotion_positive",
    "emotion_negative",
    "age",
    "interview_score",
    "post_race_score",
    "ex_attack",
    "ex_left_behind",
    "recent_top3",
    "recent_avg_finish",
    "partner_top3_rate",
    "head_to_head_ratio",
]


def build_feature_row(race: dict, entrant: dict, emotion: dict | None = None) -> dict[str, float]:
    stats = entrant.get("stats", {})
    emotion = emotion or analyze_comment(entrant.get("comment"))
    car_no = int(entrant.get("car_no") or 0)
    line_len, line_pos = _line_context(car_no, race.get("lineup", []))
    style = entrant.get("style") or ""
    ai_mark = entrant.get("ai_mark") or ""
    emotion_score = float(emotion.get("score") or 0.0)

    row = {
        "bias": 1.0,
        "racing_score": (float(entrant.get("racing_score") or 0.0) - 74.0) / 10.0,
        "win_rate": float(stats.get("win_rate") or 0.0) / 100.0,
        "two_rate": float(stats.get("two_rate") or 0.0) / 100.0,
        "three_rate": float(stats.get("three_rate") or 0.0) / 100.0,
        "start_count": min(float(stats.get("start_count") or 0.0), 12.0) / 12.0,
        "home_count": min(float(stats.get("home_count") or 0.0), 12.0) / 12.0,
        "back_count": min(float(stats.get("back_count") or 0.0), 12.0) / 12.0,
        "style_escape": 1.0 if style == "逃" else 0.0,
        "style_pursuit": 1.0 if style == "追" else 0.0,
        "style_allround": 1.0 if style == "両" else 0.0,
        "ai_honmei": 1.0 if ai_mark == "本命" else 0.0,
        "ai_taiko": 1.0 if ai_mark == "対抗" else 0.0,
        "ai_tanana": 1.0 if ai_mark == "単穴" else 0.0,
        "ai_renshita": 1.0 if ai_mark == "連下" else 0.0,
        "line_len": min(line_len, 4) / 4.0,
        "line_front": 1.0 if line_pos == 0 else 0.0,
        "line_second": 1.0 if line_pos == 1 else 0.0,
        "line_third_plus": 1.0 if line_pos >= 2 else 0.0,
        "emotion_score": emotion_score / 3.0,
        "emotion_positive": max(0.0, emotion_score) / 3.0,
        "emotion_negative": max(0.0, -emotion_score) / 3.0,
        "age": (float(entrant.get("age") or 40.0) - 40.0) / 20.0,
        "interview_score": float(analyze_comment(entrant.get("interview")).get("score") or 0.0) / 3.0 if entrant.get("interview") else 0.0,
        "post_race_score": float(analyze_comment(entrant.get("post_race_comment")).get("score") or 0.0) / 3.0 if entrant.get("post_race_comment") else 0.0,
        "ex_attack": _ex_attack(entrant.get("ex") or {}),
        "ex_left_behind": min(float((entrant.get("ex") or {}).get("exLeftBehind") or 0.0), 60.0) / 60.0,
        "recent_top3": _recent_top3(entrant.get("recent_form") or []),
        "recent_avg_finish": _recent_avg_finish(entrant.get("recent_form") or []),
        "partner_top3_rate": _partner_top3_rate(entrant.get("partner_record")),
        "head_to_head_ratio": _head_to_head_ratio(entrant.get("head_to_head") or []),
    }
    return {name: float(row.get(name, 0.0)) for name in FEATURE_NAMES}


def _ex_attack(ex: dict) -> float:
    """スパート/突き抜け/奪取の攻撃系EXの最大成功率(0-1)。"""
    values = [float(ex.get(key) or 0.0) for key in ("exSpurt", "exThrust", "exSnatch")]
    return min(max(values, default=0.0), 100.0) / 100.0


def _recent_top3(form: list[int]) -> float:
    if not form:
        return 0.0
    return sum(1 for finish in form if finish <= 3) / len(form)


def _recent_avg_finish(form: list[int]) -> float:
    """直近平均着順を0-1へ(1着=1.0, 9着=0.0)。データなしは中立0.5。"""
    if not form:
        return 0.5
    average = sum(form) / len(form)
    return max(0.0, min(1.0, (9.0 - average) / 8.0))


def _partner_top3_rate(partner_record: dict | None) -> float:
    """ラインの相方との連携成績(過去に一緒のラインで走った時の3着内率)。データなしは中立0.45。"""
    if not partner_record or int(partner_record.get("races") or 0) < 2:
        return 0.45
    return float(partner_record.get("top3_rate") or 0.45)


def _head_to_head_ratio(records: list[dict]) -> float:
    """対戦成績の勝率(0-1)。データなしは中立0.5。"""
    wins = sum(int(item.get("wins") or 0) for item in records)
    losses = sum(int(item.get("losses") or 0) for item in records)
    if wins + losses <= 0:
        return 0.5
    return wins / (wins + losses)


def dot(weights: dict[str, float], features: dict[str, float]) -> float:
    return sum(float(weights.get(name, 0.0)) * float(features.get(name, 0.0)) for name in FEATURE_NAMES)


def _line_context(car_no: int, lineup: list[list[int]]) -> tuple[int, int]:
    for line in lineup or []:
        if car_no in line:
            return len(line), line.index(car_no)
    return 0, -1
