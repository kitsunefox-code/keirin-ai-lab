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
    }
    return {name: float(row.get(name, 0.0)) for name in FEATURE_NAMES}


def dot(weights: dict[str, float], features: dict[str, float]) -> float:
    return sum(float(weights.get(name, 0.0)) * float(features.get(name, 0.0)) for name in FEATURE_NAMES)


def _line_context(car_no: int, lineup: list[list[int]]) -> tuple[int, int]:
    for line in lineup or []:
        if car_no in line:
            return len(line), line.index(car_no)
    return 0, -1
