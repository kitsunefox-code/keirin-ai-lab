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
    # EXデータは6項目あるが、従来は攻撃系3つをmaxで1つに潰し、
    # exSplitLine(保有率76.7%)と exCompete は丸ごと捨てていた。
    # 欠損を0で埋めると「成功率0%」と「データなし」を区別できないため、
    # 値と一緒に「データがあるか」も渡す。
    "exf_split_line",
    "exf_split_line_has",
    "exf_left_behind_has",
    "exf_spurt",
    "exf_spurt_has",
    "exf_thrust",
    "exf_thrust_has",
    "exf_snatch",
    "exf_snatch_has",
    "exf_compete",
    "exf_compete_has",
    "exf_attack_count",
    "exf_split_line_z",
    "recent_top3",
    "recent_avg_finish",
    "partner_top3_rate",
    "head_to_head_ratio",
    "pos_win_rate",
    "pos_top3_rate",
    "venue_top3_rate",
    "track_top3_rate",
    "hour_top3_rate",
    "line_strength_score",
    "line_strength_back",
    "bank_style_fit",
    "is_girls",
    "class_s",
    "class_a",
    "is_night",
    "is_midnight",
    "is_rain",
]

# ライン(隊列)の力学。build_line_row() が作る。
LINE_FEATURE_NAMES = [
    "ln_solo",
    "ln_pos",
    "ln_size_rel",
    "ln_own_avg_score",
    "ln_score_gap",
    "ln_is_top_line",
    "ln_leader_back",
    "ln_leader_escape",
    "ln_selfpower_ratio",
    "ln_line_count",
    "ln_second_of_top",
    "ln_score_in_line",
    # 主導権(バック取得)ラインは「先頭のバック数・ホーム数・ライン長・
    # 番手の得点差・番手が先頭より年上か」の5変数で56.7%の精度で予測できる
    # という先行分析(遠山競輪研究所, 3,442件)に基づく。
    "ln_leader_home",
    "ln_bante_score_diff",
    "ln_bante_older",
    # 競輪は絶対値でなくレース内の相対勝負なので、z-scoreも渡す
    "ln_score_z",
    "ln_win_rate_z",
]

# WINTICKETが公開している「本命/対抗/単穴/連下」の印。
# 保存はするが**学習には使わない**。
# 2,464レースの検証で、これを学習から外すと的中率は下がるものの
# 回収率が明確に上がった(印あり81.7% → 印なし88.0%、5分割の平均)。
# 公開印は既にオッズへ織り込まれているため、それに乗るほど
# 「人気どおりに当たるが配当が安い」買い方になり、控除率に負ける。
MARK_FEATURE_NAMES = ["ai_honmei", "ai_taiko", "ai_tanana", "ai_renshita"]

# 実際にモデルへ渡す特徴量
# 2段目(ライン模型の出力)。line_model.STAGE2_FEATURE_NAMES と同じ並びにする。
# ここで直接importすると循環参照になるため名前だけ持つ。
STAGE2_FEATURE_NAMES = ["s2_line_win_p", "s2_line_rank", "s2_line_p_x_head", "s2_line_p_x_bante"]

MODEL_FEATURE_NAMES = (
    [n for n in FEATURE_NAMES if n not in MARK_FEATURE_NAMES] + LINE_FEATURE_NAMES + STAGE2_FEATURE_NAMES
)


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
        "pos_win_rate": _pos_stat(entrant, "win_rate", default=0.12),
        "pos_top3_rate": _pos_stat(entrant, "top3_rate", default=0.35),
        "venue_top3_rate": _block_rate(entrant.get("venue_stats"), min_total=4),
        "track_top3_rate": _block_rate(entrant.get("track_stats"), min_total=8),
        "hour_top3_rate": _block_rate(entrant.get("hour_stats"), min_total=8),
        "line_strength_score": _line_strength(entrant, "rank_score"),
        "line_strength_back": _line_strength(entrant, "rank_back"),
        "bank_style_fit": _bank_style_fit(style, race.get("bank") or {}),
        "is_girls": 1.0 if race.get("is_girls") else 0.0,
        "class_s": _class_flag(race, "S"),
        "class_a": _class_flag(race, "A"),
        "is_night": 1.0 if race.get("hour_type") == "hourTypeNight" else 0.0,
        "is_midnight": 1.0 if race.get("hour_type") == "hourTypeMidnight" else 0.0,
        "is_rain": 1.0 if (race.get("weather_info") or {}).get("is_rain") else 0.0,
    }
    row.update(build_line_row(race, car_no))
    row.update(build_ex_row(race, entrant))
    # 印(ai_*)も保存はする。学習に使うかは MODEL_FEATURE_NAMES 側で決める。
    # 4桁に丸める: 勾配ブースティングにそれ以上の精度は不要で、
    # 0.012999999999999545 のような値をそのまま保存するとDBが無駄に膨らむ。
    return {name: round(float(row.get(name, 0.0)), 4) for name in FEATURE_NAMES + LINE_FEATURE_NAMES}


def _style_axis(style: str) -> float:
    return {"逃": 1.0, "両": 0.3, "追": -1.0}.get(str(style or ""), 0.0)


def _bank_style_fit(style: str, bank: dict) -> float:
    """脚質とバンク傾向の適合度を 0(不適)〜1(適)へ。中立は0.5。"""
    bias = bank.get("bank_bias")
    axis = _style_axis(style)
    if bias is None or axis == 0.0:
        return 0.5
    # bias*axis は -1..1。0.5中心にスケール。
    return max(0.0, min(1.0, 0.5 + float(bias) * axis * 0.5))


def _class_flag(race: dict, letter: str) -> float:
    text = str(race.get("race_class_official") or race.get("race_class") or "")
    return 1.0 if text.startswith(f"{letter}級") or f"{letter}級" in text[:3] else 0.0


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


def _pos_stat(entrant: dict, key: str, default: float) -> float:
    """今回のライン位置に対応する位置別成績(先頭勝率/番手勝率など)。"""
    line_rank = entrant.get("line_rank")
    if not line_rank:
        return default
    if line_rank.get("line_len", 1) <= 1:
        pos_key = "single"
    else:
        pos_key = {0: "front", 1: "second"}.get(line_rank.get("pos"), "third")
    block = (entrant.get("position_stats") or {}).get(pos_key)
    if not block or block.get("total", 0) < 5:
        return default
    return float(block.get(key) or default)


def _block_rate(block: dict | None, min_total: int) -> float:
    """成績ブロックのtop3率。サンプル不足は中立0.35。"""
    if not block or block.get("total", 0) < min_total:
        return 0.35
    return float(block.get("top3_rate") or 0.35)


def _line_strength(entrant: dict, rank_key: str) -> float:
    line_rank = entrant.get("line_rank")
    if not line_rank:
        return 0.5
    span = max(1, int(line_rank.get("line_count") or 1) - 1)
    return 1.0 - float(line_rank.get(rank_key) or 0) / span


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


# 名前つき特徴量が欠けているときの中立デフォルト(古い保存データや取得失敗の穴埋め用)。
FEATURE_DEFAULTS: dict[str, float] = {
    "recent_avg_finish": 0.5,
    "partner_top3_rate": 0.45,
    "head_to_head_ratio": 0.5,
    "pos_win_rate": 0.12,
    "pos_top3_rate": 0.35,
    "venue_top3_rate": 0.35,
    "track_top3_rate": 0.35,
    "hour_top3_rate": 0.35,
    "line_strength_score": 0.5,
    "line_strength_back": 0.5,
    "bank_style_fit": 0.5,
}


def feature_vector(features: dict[str, float], names: list[str] = FEATURE_NAMES) -> list[float]:
    """特徴量dictを固定順ベクトルへ。欠損は中立デフォルト(なければ0)で埋める。"""
    return [float(features.get(name, FEATURE_DEFAULTS.get(name, 0.0))) for name in names]


_EX_FIELDS = {
    "exSplitLine": "split_line",
    "exLeftBehind": "left_behind",
    "exSpurt": "spurt",
    "exThrust": "thrust",
    "exSnatch": "snatch",
    "exCompete": "compete",
}


def build_ex_row(race: dict, entrant: dict) -> dict[str, float]:
    """EXデータを全項目そのまま渡す。

    保有率は項目ごとに 10〜77% とばらつく。欠損を0で埋めるだけだと
    「成功率0%」と「データなし」が同じ値になり区別できないので、
    値と "_has" フラグを対で渡す。データの有無自体も
    (出走歴が十分にある選手かどうかの目印として)情報になる。
    """
    ex = entrant.get("ex") or {}
    ex = ex if isinstance(ex, dict) else {}
    row: dict[str, float] = {}
    for field, short in _EX_FIELDS.items():
        value = ex.get(field)
        row[f"exf_{short}"] = (float(value) / 100.0) if value is not None else 0.0
        row[f"exf_{short}_has"] = 1.0 if value is not None else 0.0
    present = [float(ex[f]) for f in ("exSpurt", "exThrust", "exSnatch") if ex.get(f) is not None]
    row["exf_attack_count"] = len(present) / 3.0

    # レース内での相対化(誰が離れやすい/ちぎりやすいか)
    values = []
    for other in race.get("entrants") or []:
        oex = other.get("ex") or {}
        v = oex.get("exSplitLine") if isinstance(oex, dict) else None
        values.append(float(v) / 100.0 if v is not None else 0.0)
    mean = sum(values) / len(values) if values else 0.0
    sd = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5 if values else 0.0
    row["exf_split_line_z"] = ((row["exf_split_line"] - mean) / sd) if sd > 0 else 0.0
    return row


def _lines_of(race: dict) -> list[list[int]]:
    """隊列を車番のリストへ整える。隊列に載っていない車は単騎として足す。"""
    entrants = race.get("entrants") or []
    valid = {int(e.get("car_no") or 0) for e in entrants if e.get("car_no")}
    lines: list[list[int]] = []
    seen: set[int] = set()
    for raw in race.get("lineup") or []:
        line = [int(c) for c in raw if int(c) in valid and int(c) not in seen]
        if line:
            lines.append(line)
            seen.update(line)
    for car in sorted(valid - seen):
        lines.append([car])
    return lines


def build_line_row(race: dict, car_no: int) -> dict[str, float]:
    """ライン(隊列)の力学を数値にする。

    競輪は個人ではなくラインの勝負で、着順は「どのラインが主導権を取り、
    その中の何番手にいるか」で大きく決まる。従来は先頭選手の得点順位しか
    見ておらず、番手の実力・ライン間の力量差・主導権争いの激しさが
    モデルに入っていなかった。
    """
    lines = _lines_of(race)
    if not lines:
        return {name: 0.0 for name in LINE_FEATURE_NAMES}
    by_car = {int(e.get("car_no") or 0): e for e in (race.get("entrants") or [])}

    def score(car: int) -> float:
        return float((by_car.get(car) or {}).get("racing_score") or 0.0)

    def back(car: int) -> float:
        stats = (by_car.get(car) or {}).get("stats") or {}
        return min(float(stats.get("back_count") or 0.0), 12.0) / 12.0

    def style_of(car: int) -> str:
        return str((by_car.get(car) or {}).get("style") or "")

    def home(car: int) -> float:
        stats = (by_car.get(car) or {}).get("stats") or {}
        return min(float(stats.get("home_count") or 0.0), 12.0) / 12.0

    def age_of(car: int) -> float:
        return float((by_car.get(car) or {}).get("age") or 0.0)

    def win_rate(car: int) -> float:
        stats = (by_car.get(car) or {}).get("stats") or {}
        return float(stats.get("win_rate") or 0.0) / 100.0

    own_idx = next((i for i, line in enumerate(lines) if car_no in line), None)
    if own_idx is None:
        return {name: 0.0 for name in LINE_FEATURE_NAMES}
    own = lines[own_idx]
    pos = own.index(car_no)

    leader_scores = [score(line[0]) for line in lines]
    best = max(leader_scores)
    top_idx = leader_scores.index(best)
    others = [leader_scores[i] for i in range(len(lines)) if i != own_idx]
    rival_best = max(others) if others else leader_scores[own_idx]
    avg = sum(score(c) for c in own) / len(own)
    max_len = max(len(line) for line in lines)
    self_power = sum(1 for c in by_car if style_of(c) in ("逃", "両"))

    # レース内での相対化
    cars = list(by_car)
    scores = [score(c) for c in cars]
    med = sorted(scores)[len(scores) // 2] if scores else 0.0
    mean_s = sum(scores) / len(scores) if scores else 0.0
    sd_s = (sum((s - mean_s) ** 2 for s in scores) / len(scores)) ** 0.5 if scores else 0.0
    wrs = [win_rate(c) for c in cars]
    mean_w = sum(wrs) / len(wrs) if wrs else 0.0
    sd_w = (sum((w - mean_w) ** 2 for w in wrs) / len(wrs)) ** 0.5 if wrs else 0.0
    bante = own[1] if len(own) > 1 else None

    return {
        "ln_leader_home": home(own[0]),
        "ln_bante_score_diff": ((score(bante) - med) / 10.0) if bante else 0.0,
        "ln_bante_older": 1.0 if (bante and age_of(bante) > age_of(own[0])) else 0.0,
        "ln_score_z": ((score(car_no) - mean_s) / sd_s) if sd_s > 0 else 0.0,
        "ln_win_rate_z": ((win_rate(car_no) - mean_w) / sd_w) if sd_w > 0 else 0.0,
        "ln_solo": 1.0 if len(own) == 1 else 0.0,
        "ln_pos": min(pos, 3) / 3.0,
        "ln_size_rel": len(own) / max_len if max_len else 0.0,
        "ln_own_avg_score": (avg - 74.0) / 10.0,
        "ln_score_gap": (score(own[0]) - rival_best) / 10.0,
        "ln_is_top_line": 1.0 if own_idx == top_idx else 0.0,
        "ln_leader_back": back(own[0]),
        "ln_leader_escape": 1.0 if style_of(own[0]) == "逃" else 0.0,
        "ln_selfpower_ratio": self_power / max(1, len(by_car)),
        "ln_line_count": min(len(lines), 5) / 5.0,
        "ln_second_of_top": 1.0 if (own_idx == top_idx and pos == 1) else 0.0,
        "ln_score_in_line": (score(car_no) - avg) / 10.0,
    }


def _line_context(car_no: int, lineup: list[list[int]]) -> tuple[int, int]:
    for line in lineup or []:
        if car_no in line:
            return len(line), line.index(car_no)
    return 0, -1
