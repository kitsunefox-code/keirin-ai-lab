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

# 勝率表示の較正温度。631レースの実測でトップ表示勝率が実態(約39%)に一致する値。
# 大きいほど確率が平ら(控えめ)になる。順位は不変で確率だけ実態に寄せる。
SOFTMAX_TEMPERATURE = 4.0


def predict_race(race: dict, use_learning: bool = True) -> dict:
    entrants = race.get("entrants", [])
    if not entrants:
        return {"rankings": [], "tickets": [], "race_notes": ["出走データがありません。"]}

    learned_model = load_model() if use_learning else None
    _attach_line_ranks(race)
    scored = []
    for entrant in entrants:
        emotion = analyze_comment(entrant.get("comment"))
        features = build_feature_row(race, entrant, emotion)
        baseline = _baseline_score(entrant, emotion, race)
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
                "reasons": _reasons(entrant, emotion, baseline, learned_model, learned_logit, race),
            }
        )

    probs = _softmax([row["model_score"] for row in scored])
    for row, prob in zip(scored, probs):
        row["win_probability"] = round(prob, 4)

    scored.sort(key=lambda row: row["model_score"], reverse=True)
    tickets = _ticket_candidates(scored, race)
    exacta = _exacta_candidates(scored, race)
    return {
        "rankings": scored,
        "exacta": exacta,
        "tickets": tickets,
        "race_notes": _race_notes(race, scored, learned_model),
        "model": _model_meta(learned_model),
    }


def _baseline_score(entrant: dict, emotion: dict, race: dict) -> float:
    lineup = race.get("lineup", [])
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
    # ガールズは単騎主体でライン概念が薄いので、ライン強度の寄与を下げる
    line_weight = 0.4 if race.get("is_girls") else 1.0
    score += _line_strength_score(entrant) * line_weight
    score += _position_fit_score(entrant)
    score += _bank_fit_score(entrant)
    score += _bank_tendency_score(entrant, race)
    score += _condition_score(entrant, race)
    score += _line_bonus(int(entrant.get("car_no") or 0), lineup) * line_weight
    if entrant.get("style") == "逃":
        score += 0.12
    if entrant.get("style") == "追" and _is_second_in_line(int(entrant.get("car_no") or 0), lineup):
        score += 0.16
    return score


def _attach_line_ranks(race: dict) -> None:
    """2軸ライン強度(パ部競輪方式)を各選手に付与する。

    ①先頭選手の競走得点によるラインランク(本命軸)
    ②先頭選手のバック回数によるラインランク(穴目軸)
    それぞれA=0が最強。ライン内位置(0=先頭,1=番手,...)も併せて持つ。
    """
    by_car = {int(e.get("car_no") or 0): e for e in race.get("entrants", [])}
    lines = []
    seen: set[int] = set()
    for raw_line in race.get("lineup") or []:
        line = [int(c) for c in raw_line if int(c) in by_car and int(c) not in seen]
        if line:
            lines.append(line)
            seen.update(line)
    for car in by_car:
        if car not in seen:
            lines.append([car])
    if len(lines) < 2:
        return

    def front_score(line: list[int]) -> float:
        return float(by_car[line[0]].get("racing_score") or 0)

    def front_back(line: list[int]) -> float:
        return float((by_car[line[0]].get("stats") or {}).get("back_count") or 0)

    by_score = sorted(range(len(lines)), key=lambda i: front_score(lines[i]), reverse=True)
    by_back = sorted(range(len(lines)), key=lambda i: front_back(lines[i]), reverse=True)
    rank_score = {line_idx: rank for rank, line_idx in enumerate(by_score)}
    rank_back = {line_idx: rank for rank, line_idx in enumerate(by_back)}
    for idx, line in enumerate(lines):
        for pos, car in enumerate(line):
            by_car[car]["line_rank"] = {
                "rank_score": rank_score[idx],
                "rank_back": rank_back[idx],
                "pos": pos,
                "line_len": len(line),
                "line_count": len(lines),
            }


def _position_key(line_rank: dict | None) -> str:
    if not line_rank:
        return ""
    if line_rank.get("line_len", 1) <= 1:
        return "single"
    return {0: "front", 1: "second"}.get(line_rank.get("pos"), "third")


def _line_strength_score(entrant: dict) -> float:
    """ライン強度×位置の加点。番手を最優位に扱う(リサーチ結果に基づく)。"""
    line_rank = entrant.get("line_rank")
    if not line_rank:
        return 0.0
    span = max(1, line_rank["line_count"] - 1)
    strength_by_score = 1.0 - line_rank["rank_score"] / span
    strength_by_back = 1.0 - line_rank["rank_back"] / span
    position_factor = {0: 0.9, 1: 1.0, 2: 0.55}.get(line_rank["pos"], 0.35)
    if line_rank.get("line_len", 1) <= 1:
        position_factor = 0.5
    return strength_by_score * position_factor * 0.35 + strength_by_back * position_factor * 0.22


def _position_fit_score(entrant: dict) -> float:
    """今回のライン位置に対応する過去の位置別勝率(先頭勝率/番手勝率/単騎)を加点する。"""
    block = (entrant.get("position_stats") or {}).get(_position_key(entrant.get("line_rank")))
    if not block or block.get("total", 0) < 5:
        return 0.0
    return (block["win_rate"] - 0.12) * 1.5 + (block["top3_rate"] - 0.35) * 0.6


def _bank_fit_score(entrant: dict) -> float:
    """当該バンクの過去成績・周長別成績・時間帯別成績の加点。"""
    score = 0.0
    venue = entrant.get("venue_stats")
    if venue and venue.get("total", 0) >= 4:
        score += (venue["top3_rate"] - 0.4) * 0.5
    track = entrant.get("track_stats")
    if track and track.get("total", 0) >= 8:
        score += (track["top3_rate"] - 0.35) * 0.3
    hour = entrant.get("hour_stats")
    if hour and hour.get("total", 0) >= 8:
        score += (hour["top3_rate"] - 0.35) * 0.2
    return score


def _style_axis(style: str) -> float:
    """脚質を -1(差し・追込)〜+1(逃げ・先行)の軸へ。"""
    return {"逃": 1.0, "両": 0.3, "追": -1.0}.get(str(style or ""), 0.0)


def _bank_tendency_score(entrant: dict, race: dict) -> float:
    """バンクの脚質傾向(周長・直線・決まり手分布)と選手の脚質の適合を加点する。

    逃げ有利バンク(bank_bias>0)では逃げ・先行型を、差し有利バンクでは差し・追込型を評価。
    """
    bank = race.get("bank") or {}
    bias = bank.get("bank_bias")
    if bias is None:
        return 0.0
    axis = _style_axis(entrant.get("style"))
    if axis == 0.0:
        return 0.0
    # bias と 脚質軸が同符号(適合)なら加点、逆なら減点
    return float(bias) * axis * 0.22


def _condition_score(entrant: dict, race: dict) -> float:
    """ナイター/ミッドナイト・天気(雨)による展開補正。逃げ・先行型に効く。"""
    axis = _style_axis(entrant.get("style"))
    score = 0.0
    weather = race.get("weather_info") or {}
    if weather.get("is_rain"):
        # 雨は差しづらく逃げ・先行が残りやすい
        score += axis * 0.12
    if race.get("hour_type") == "hourTypeMidnight":
        # ミッドナイトは無観客・short fieldで先行有利になりやすい
        score += axis * 0.06
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
    partner = entrant.get("partner_record")
    if partner and partner.get("races", 0) >= 2:
        score += (partner["top3_rate"] - 0.45) * 0.4
    h2h = entrant.get("head_to_head") or []
    if h2h:
        wins = sum(item["wins"] for item in h2h)
        losses = sum(item["losses"] for item in h2h)
        if wins + losses > 0:
            score += (wins - losses) / (wins + losses) * 0.15
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
    # 温度(較正済み): 631レースの実測で、旧scale=1.15は本命勝率を平均76.6%と過信し、
    # 実際の的中は38.8%だった。scale=4.0(=1.15×3.5)にするとトップ表示勝率が平均約40%と
    # 実態に一致し、Brierも0.126→0.100に改善する。並べ替え(順位)は不変で確率だけ実態に合わせる。
    scale = SOFTMAX_TEMPERATURE
    peak = max(values)
    exps = [math.exp((value - peak) / scale) for value in values]
    total = sum(exps)
    return [value / total for value in exps]


def _suji_map(race: dict) -> dict[tuple[int, int], float]:
    """ライン内の隣接ペア(スジ)にボーナス係数を割り当てる。

    リサーチ知見: スジ決着は全体の約48.5%。特に第二ライン(先頭の得点が2番手の
    ライン)の「先頭→番手」はベタ買いでも回収率98.84%と過小評価されている。
    先頭の競走得点順でラインを並べ、第一ライン<第二ライン<以降にボーナスを傾斜。
    """
    by_car = {int(e.get("car_no") or 0): e for e in race.get("entrants", [])}
    # lineup は稀に重複行が混ざるので、_attach_line_ranks と同じく先頭出現優先で重複除去
    lines = []
    seen: set[int] = set()
    for raw in race.get("lineup") or []:
        line = [int(car) for car in raw if int(car) in by_car and int(car) not in seen]
        if line:
            seen.update(line)
            if len(line) >= 2:
                lines.append(line)
    if not lines:
        return {}

    def head_score(line: list[int]) -> float:
        return float((by_car.get(line[0]) or {}).get("racing_score") or 0)

    ranked = sorted(range(len(lines)), key=lambda i: head_score(lines[i]), reverse=True)
    line_rank = {idx: rank for rank, idx in enumerate(ranked)}  # 0=第一ライン(人気)
    suji: dict[tuple[int, int], float] = {}
    for idx, line in enumerate(lines):
        # 第一ラインは過剰人気なので控えめ、第二ライン以降を厚く(市場の歪み)
        rank = line_rank[idx]
        base = 1.12 if rank == 0 else (1.30 if rank == 1 else 1.20)
        for pos in range(len(line) - 1):
            head, mark = line[pos], line[pos + 1]
            # 先頭→番手を最重視、番手→3番手はやや弱め
            suji[(head, mark)] = base if pos == 0 else base * 0.85
    return suji


def _favorite_dampen(race: dict) -> float:
    """7車立ては人気サイドの期待値が低い(回収率72% vs 9車立て77%)ため本命目を軽く割り引く。"""
    n = len(race.get("entrants") or [])
    return 0.94 if n <= 7 else 1.0


def _ticket_candidates(scored: list[dict], race: dict) -> list[dict]:
    top = scored[:4]
    suji = _suji_map(race)
    dampen = _favorite_dampen(race)
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
        # スジ(ライン決着)ボーナス: 1着→2着がライン隣接なら市場の歪みを取りにいく
        suji_factor = suji.get((c1["car_no"], c2["car_no"]), 1.0)
        strength *= suji_factor
        # 本命1着はやや割引(7車立て)
        if c1 is top[0]:
            strength *= dampen
        tickets.append(
            {
                "cars": [c1["car_no"], c2["car_no"], c3["car_no"]],
                "score": round(strength, 4),
                "label": f"{c1['car_no']}-{c2['car_no']}-{c3['car_no']}",
                "suji": suji_factor > 1.0,
                "bet_type": "trifecta",
            }
        )
    tickets.sort(key=lambda item: item["score"], reverse=True)
    return tickets[:8]


def _exacta_candidates(scored: list[dict], race: dict) -> list[dict]:
    """2車単(軸1着固定・少点数)。控除率一律25%下で的中率を確保する主力券種。

    リサーチ知見: 商用AI(netkeirin Aiライン極等)の共通解は「2車単・軸1車1着固定・
    少点数」。3連単1/504に対し2車単1/72で桁違いに当たる。1着は本命固定、2着に
    スジ番手と上位を厚めに、最大4点。
    """
    if len(scored) < 3:
        return []
    axis = scored[0]
    suji = _suji_map(race)
    partners = []
    for row in scored[1:5]:
        p = float(row["win_probability"])
        factor = suji.get((axis["car_no"], row["car_no"]), 1.0)
        partners.append(
            {
                "cars": [axis["car_no"], row["car_no"]],
                "score": round(p * factor, 4),
                "label": f"{axis['car_no']}-{row['car_no']}",
                "suji": factor > 1.0,
                "bet_type": "exacta",
            }
        )
    partners.sort(key=lambda item: item["score"], reverse=True)
    return partners[:4]


def _reasons(entrant: dict, emotion: dict, baseline: float, learned_model: dict | None, learned_logit: float, race: dict | None = None) -> list[str]:
    stats = entrant.get("stats", {})
    race = race or {}
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
    left_behind = float(ex.get("exLeftBehind") or 0.0)
    if left_behind >= 25:
        reasons.append(f"ちぎられ率{left_behind:.0f}%")
    form = entrant.get("recent_form") or []
    if form and sum(1 for finish in form if finish <= 3) / len(form) >= 0.6:
        reasons.append("直近3着内多い")
    pos_key = _position_key(entrant.get("line_rank"))
    pos_block = (entrant.get("position_stats") or {}).get(pos_key)
    if pos_block and pos_block.get("total", 0) >= 5:
        pos_label = {"front": "先頭", "second": "番手", "third": "3番手", "single": "単騎"}.get(pos_key, "")
        if pos_block["win_rate"] >= 0.15:
            reasons.append(f"{pos_label}勝率{pos_block['win_rate'] * 100:.0f}%")
        elif pos_block["top3_rate"] >= 0.45:
            reasons.append(f"{pos_label}3着内{pos_block['top3_rate'] * 100:.0f}%")
    venue = entrant.get("venue_stats")
    if venue and venue.get("total", 0) >= 4 and venue["top3_rate"] >= 0.5:
        reasons.append(f"当所3着内{venue['top3_rate'] * 100:.0f}%")
    line_rank = entrant.get("line_rank")
    if line_rank and line_rank["rank_back"] == 0 and line_rank["rank_score"] > 0 and line_rank["pos"] <= 1:
        reasons.append("バック数最強ライン(穴目)")
    bank = race.get("bank") or {}
    bias = bank.get("bank_bias")
    axis = _style_axis(entrant.get("style"))
    if bias is not None and axis != 0 and abs(float(bias)) >= 0.15 and (float(bias) * axis) > 0:
        if float(bias) > 0:
            reasons.append(f"逃げ有利バンク({bank.get('track_distance') or ''}m)")
        else:
            reasons.append("差し有利バンク")
    weather = race.get("weather_info") or {}
    if weather.get("is_rain") and axis > 0:
        reasons.append("雨で先行有利")
    if race.get("hour_type") == "hourTypeMidnight" and axis > 0:
        reasons.append("ミッドナイト先行")
    partner = entrant.get("partner_record")
    if partner and partner.get("races", 0) >= 2:
        reasons.append(f"連携実績 {partner['partner_name']}と{partner['races']}戦{partner['top3']}回3着内")
    h2h = entrant.get("head_to_head") or []
    if h2h:
        best = max(h2h, key=lambda item: item["wins"] + item["losses"])
        reasons.append(f"対{best['opponent_name']} {best['wins']}勝{best['losses']}敗")
    if learned_model and abs(learned_logit) >= 0.35:
        direction = "追い風" if learned_logit > 0 else "割引"
        reasons.append(f"学習: {direction}")
    if baseline < 0:
        reasons.append("総合材料は弱め")
    return reasons[:7] or ["目立つ強調材料は少なめ"]


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
        notes.append("EXデータ(スパート・ちぎられ率等)を評価に反映しました。")
    if any(row.get("partner_record") for row in scored):
        notes.append("ライン相方との過去の連携成績を評価に反映しました。")
    if any(row.get("position_stats") for row in scored):
        notes.append("ライン位置別成績(先頭勝率・番手勝率・単騎)を評価に反映しました。")
    if any(row.get("venue_stats") or row.get("track_stats") for row in scored):
        notes.append("当該バンクの過去成績と周長別成績を評価に反映しました。")
    if any(row.get("line_rank") for row in scored):
        notes.append("2軸ライン強度(先頭の得点とバック回数)を評価に反映しました。")
    if race.get("is_girls") and any(row.get("head_to_head") for row in scored):
        notes.append("ガールズケイリンのため単騎前提でライン重みを下げ、選手個人力と対戦成績を重視しました。")
    elif any(row.get("head_to_head") for row in scored):
        notes.append("出走選手同士の対戦成績を評価に反映しました。")
    bank = race.get("bank") or {}
    if bank.get("bank_bias") is not None:
        km = bank.get("kimarite") or {}
        tendency = "逃げ・先行有利" if bank["bank_bias"] > 0.1 else "差し・追込有利" if bank["bank_bias"] < -0.1 else "標準的"
        km_text = ""
        if km:
            km_text = f"(決まり手 逃{km.get('逃げ',0)*100:.0f}/捲{km.get('捲り',0)*100:.0f}/差{km.get('差し',0)*100:.0f})"
        notes.append(f"{bank.get('name','')}バンク{bank.get('track_distance') or ''}m・直線{bank.get('straight') or ''}mは{tendency}{km_text}として脚質評価に反映しました。")
    weather = race.get("weather_info") or {}
    if weather.get("is_rain"):
        notes.append(f"天気({weather.get('weather') or '雨'})は逃げ・先行有利の材料として反映しました。")
    if race.get("hour_type") == "hourTypeMidnight":
        notes.append("ミッドナイト(無観客)は先行有利の傾向を軽く加味しました。")
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
