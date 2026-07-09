from __future__ import annotations

import itertools
import math

from keirin_ai.emotion import analyze_comment
from keirin_ai.features import build_feature_row
from keirin_ai.learner import load_model, predict_logit, predict_probability


MARK_BONUS = {
    "本命": 0.9,
    "対抗": 0.55,
    "単穴": 0.3,
    "連下": 0.15,
}


def predict_race(race: dict, use_learning: bool = True) -> dict:
    entrants = race.get("entrants", [])
    if not entrants:
        return {"rankings": [], "tickets": [], "race_notes": ["出走データがありません。"]}

    learned_model = load_model() if use_learning else None
    scored = []
    for entrant in entrants:
        emotion = analyze_comment(entrant.get("comment"))
        features = build_feature_row(race, entrant, emotion)
        baseline = _baseline_score(entrant, emotion, race.get("lineup", []))
        learned_logit = predict_logit(learned_model, features) if learned_model else 0.0
        learned_prob = predict_probability(learned_model, features) if learned_model else None
        score = baseline + _learned_adjustment(learned_model, learned_logit)
        scored.append(
            {
                **entrant,
                "emotion": emotion,
                "features": features,
                "baseline_score": round(baseline, 3),
                "learned_logit": round(learned_logit, 3),
                "learned_probability": round(learned_prob, 4) if learned_prob is not None else None,
                "model_score": round(score, 3),
                "reasons": _reasons(entrant, emotion, baseline, learned_model, learned_logit),
            }
        )

    probs = _softmax([row["model_score"] for row in scored])
    for row, prob in zip(scored, probs):
        row["win_probability"] = round(prob, 4)

    scored.sort(key=lambda row: row["model_score"], reverse=True)
    tickets = _ticket_candidates(scored)
    return {
        "rankings": scored,
        "tickets": tickets,
        "race_notes": _race_notes(race, scored, learned_model),
        "model": _model_meta(learned_model),
    }


def _baseline_score(entrant: dict, emotion: dict, lineup: list[list[int]]) -> float:
    stats = entrant.get("stats", {})
    score = 0.0
    score += (float(entrant.get("racing_score") or 0) - 68.0) * 0.18
    score += float(stats.get("win_rate") or 0) * 0.035
    score += float(stats.get("two_rate") or 0) * 0.018
    score += float(stats.get("three_rate") or 0) * 0.01
    score += min(float(stats.get("back_count") or 0), 12.0) * 0.08
    score += min(float(stats.get("start_count") or 0), 8.0) * 0.03
    score += MARK_BONUS.get(str(entrant.get("ai_mark") or ""), 0.0)
    score += float(emotion.get("score") or 0) * 0.18
    score += _deep_signal_score(entrant)
    score += _line_bonus(int(entrant.get("car_no") or 0), lineup)
    if entrant.get("style") == "逃":
        score += 0.12
    if entrant.get("style") == "追" and _is_second_in_line(int(entrant.get("car_no") or 0), lineup):
        score += 0.16
    return score


def _deep_signal_score(entrant: dict) -> float:
    """前検日/レース後インタビュー、EXデータ、直近着順の追加シグナル。"""
    score = 0.0
    if entrant.get("interview"):
        score += float(analyze_comment(entrant["interview"]).get("score") or 0) * 0.14
    if entrant.get("post_race_comment"):
        score += float(analyze_comment(entrant["post_race_comment"]).get("score") or 0) * 0.10
    ex = entrant.get("ex") or {}
    attack = max(
        float(ex.get("exSpurt") or 0.0),
        float(ex.get("exThrust") or 0.0),
        float(ex.get("exSnatch") or 0.0),
    )
    score += min(attack, 100.0) / 100.0 * 0.35
    score -= min(float(ex.get("exLeftBehind") or 0.0), 60.0) / 60.0 * 0.30
    form = entrant.get("recent_form") or []
    if form:
        top3_ratio = sum(1 for finish in form if finish <= 3) / len(form)
        score += (top3_ratio - 0.4) * 0.5
    return score


def _learned_adjustment(model: dict | None, learned_logit: float) -> float:
    if not model:
        return 0.0
    rows = int(model.get("training", {}).get("rows") or 0)
    confidence = min(1.0, rows / 350.0)
    return max(-1.7, min(1.7, learned_logit)) * (0.25 + confidence * 0.55)


def _line_bonus(car_no: int, lineup: list[list[int]]) -> float:
    for line in lineup:
        if car_no in line:
            pos = line.index(car_no)
            if len(line) >= 3 and pos <= 1:
                return 0.22
            if len(line) == 2 and pos == 0:
                return 0.12
    return 0.0


def _is_second_in_line(car_no: int, lineup: list[list[int]]) -> bool:
    return any(len(line) >= 2 and line[1] == car_no for line in lineup)


def _softmax(values: list[float]) -> list[float]:
    if not values:
        return []
    scale = 1.15
    peak = max(values)
    exps = [math.exp((value - peak) / scale) for value in values]
    total = sum(exps)
    return [value / total for value in exps]


def _ticket_candidates(scored: list[dict]) -> list[dict]:
    top = scored[:4]
    tickets = []
    for combo in itertools.permutations(top, 3):
        c1, c2, c3 = combo
        strength = (
            c1["win_probability"] * 0.55
            + c2["win_probability"] * 0.3
            + c3["win_probability"] * 0.15
        )
        if c1["model_score"] < c2["model_score"] - 0.35:
            strength *= 0.9
        tickets.append(
            {
                "cars": [c1["car_no"], c2["car_no"], c3["car_no"]],
                "score": round(strength, 4),
                "label": f"{c1['car_no']}-{c2['car_no']}-{c3['car_no']}",
            }
        )
    tickets.sort(key=lambda item: item["score"], reverse=True)
    return tickets[:8]


def _reasons(entrant: dict, emotion: dict, baseline: float, learned_model: dict | None, learned_logit: float) -> list[str]:
    stats = entrant.get("stats", {})
    reasons = []
    if entrant.get("ai_mark"):
        reasons.append(f"印: {entrant['ai_mark']}")
    if float(entrant.get("racing_score") or 0) >= 78:
        reasons.append("競走得点上位")
    if float(stats.get("win_rate") or 0) >= 25:
        reasons.append("勝率高め")
    if float(stats.get("back_count") or 0) >= 4:
        reasons.append("バック数あり")
    if emotion.get("tone") not in {"中立", "材料なし"}:
        reasons.append(f"前検コメ: {emotion['tone']}")
    if entrant.get("interview"):
        tone = analyze_comment(entrant["interview"]).get("tone")
        if tone not in {"中立", "材料なし"}:
            reasons.append(f"前検日談話: {tone}")
    if entrant.get("post_race_comment"):
        tone = analyze_comment(entrant["post_race_comment"]).get("tone")
        if tone not in {"中立", "材料なし"}:
            reasons.append(f"前走後談話: {tone}")
    ex = entrant.get("ex") or {}
    attack = max(
        float(ex.get("exSpurt") or 0.0),
        float(ex.get("exThrust") or 0.0),
        float(ex.get("exSnatch") or 0.0),
    )
    if attack >= 50:
        reasons.append(f"EX攻撃力{attack:.0f}%")
    if float(ex.get("exLeftBehind") or 0.0) >= 40:
        reasons.append("EX置かれ注意")
    form = entrant.get("recent_form") or []
    if form and sum(1 for finish in form if finish <= 3) / len(form) >= 0.6:
        reasons.append("直近3着内多い")
    if learned_model and abs(learned_logit) >= 0.35:
        direction = "追い風" if learned_logit > 0 else "割引"
        reasons.append(f"学習: {direction}")
    if baseline < 0:
        reasons.append("総合材料は弱め")
    return reasons[:6] or ["目立つ強調材料は少なめ"]


def _race_notes(race: dict, scored: list[dict], learned_model: dict | None) -> list[str]:
    notes = []
    if race.get("lineup"):
        notes.append("ライン構成をスコアへ反映しました。")
    if any(row["emotion"]["hits"] for row in scored):
        notes.append("コメントの心理・状態語を補助材料にしました。")
    if any(row.get("interview") for row in scored):
        notes.append("前検日インタビューを評価に反映しました。")
    if any(row.get("post_race_comment") for row in scored):
        notes.append("前走レース後の談話を評価に反映しました。")
    if any(row.get("ex") for row in scored):
        notes.append("EXデータ(スパート・置かれ等)を評価に反映しました。")
    if learned_model:
        rows = learned_model.get("training", {}).get("rows", 0)
        races = learned_model.get("training", {}).get("races", 0)
        notes.append(f"過去結果の学習重みを反映しました。教師データ: {races}レース / {rows}選手。")
    if race.get("source", {}).get("url"):
        notes.append("公開ページ由来の暫定取り込みです。結果保存で検証してください。")
    return notes


def _model_meta(learned_model: dict | None) -> dict:
    if not learned_model:
        return {
            "name": "transparent-baseline",
            "version": "0.1",
            "warning": "研究用の相対スコアです。的中や利益を保証するものではありません。",
        }
    return {
        "name": "transparent-baseline + online-logistic-win",
        "version": learned_model.get("version", "0.1"),
        "training": learned_model.get("training", {}),
        "metrics": learned_model.get("metrics", {}),
        "warning": "研究用の相対スコアです。的中や利益を保証するものではありません。",
    }
