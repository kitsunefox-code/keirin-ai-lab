from __future__ import annotations

"""ライン(隊列)単位で「どのラインが勝者を出すか」を当てる模型。

競輪はライン単位で決着する競技で、ライン内で1-2着が決まる「スジ決着」が
48.5%を占める(64,321レースの先行分析)。選手を個別に評価するだけでは
この構造を捉えきれない。

そこで2段構えにする:
  1段目 このライン模型が「勝者を出すライン」の確率を出す
  2段目 その確率を選手模型へ特徴量として渡す

検証(2,565レース・日付で時系列分割):
  ライン模型そのものの的中率は58%前後(でたらめなら28%前後)。
  2段にすると2車単の的中率が4シード中4回とも改善した(平均+1.2pt)。
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINE_MODEL_PATH = ROOT / "data" / "model_line.txt"
LINE_META_PATH = ROOT / "data" / "model_line.json"

try:
    import lightgbm as lgb
    import numpy as np

    _HAS_LGBM = True
except Exception:  # pragma: no cover
    _HAS_LGBM = False

# ライン1本につき1行ぶんの特徴量
LINE_MODEL_FEATURES = [
    "lm_size",            # ライン人数
    "lm_leader_score_z",  # 先頭のレース内相対得点
    "lm_leader_back",     # 先頭のバック回数(主導権を取れるか)
    "lm_leader_home",     # 先頭のホーム回数
    "lm_leader_escape",   # 先頭が逃げ脚質か
    "lm_avg_score_z",     # ライン平均の相対得点
    "lm_max_score_z",     # ライン内最高の相対得点
    "lm_gap_to_best",     # 他ライン最強先頭との得点差
    "lm_line_count",      # レース内のライン数
    "lm_bante_score_z",   # 番手の相対得点
    "lm_bante_older",     # 番手が先頭より年上か
    "lm_selfpower_ratio", # レース内の自力型比率(主導権争いの激しさ)
]

# 選手模型へ渡す2段目の特徴量
STAGE2_FEATURE_NAMES = [
    "s2_line_win_p",     # 自ラインが勝者を出す確率
    "s2_line_rank",      # その確率のレース内順位
    "s2_line_p_x_head",  # 確率 × 自分が先頭
    "s2_line_p_x_bante", # 確率 × 自分が番手
]

_CACHE: dict[str, tuple[float, object]] = {}


def _booster(path: Path):
    if not _HAS_LGBM or not path.exists():
        return None
    key = str(path)
    mtime = path.stat().st_mtime
    hit = _CACHE.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    booster = lgb.Booster(model_file=str(path))
    _CACHE[key] = (mtime, booster)
    return booster


def group_lines(features_by_car: dict[int, dict]) -> list[list[int]]:
    """選手ごとの ln_* 特徴から隊列を復元する。

    同じラインの選手は ln_own_avg_score / ln_score_gap / ln_size_rel が一致するため、
    それを鍵にまとめ、ln_pos の小さい順(先頭→番手→…)に並べる。
    """
    groups: dict[tuple, list[int]] = {}
    for car, f in features_by_car.items():
        key = (
            round(float(f.get("ln_own_avg_score") or 0.0), 4),
            round(float(f.get("ln_score_gap") or 0.0), 4),
            round(float(f.get("ln_size_rel") or 0.0), 4),
        )
        groups.setdefault(key, []).append(car)
    lines = []
    for cars in groups.values():
        cars.sort(key=lambda c: (float(features_by_car[c].get("ln_pos") or 0.0), c))
        lines.append(cars)
    return lines


def build_line_inputs(features_by_car: dict[int, dict]) -> tuple[list[list[int]], list[dict]]:
    """隊列と、ライン1本ぶんの特徴量を作る。"""
    lines = group_lines(features_by_car)
    if not lines:
        return [], []
    zs = [float(f.get("ln_score_z") or 0.0) for f in features_by_car.values()]
    mean_z = sum(zs) / len(zs) if zs else 0.0

    rows = []
    for line in lines:
        head = features_by_car[line[0]]
        bante = features_by_car[line[1]] if len(line) > 1 else None
        member_z = [float(features_by_car[c].get("ln_score_z") or 0.0) for c in line]
        rows.append(
            {
                "lm_size": len(line) / 4.0,
                "lm_leader_score_z": float(head.get("ln_score_z") or 0.0),
                "lm_leader_back": float(head.get("ln_leader_back") or 0.0),
                "lm_leader_home": float(head.get("ln_leader_home") or 0.0),
                "lm_leader_escape": float(head.get("ln_leader_escape") or 0.0),
                "lm_avg_score_z": (sum(member_z) / len(member_z)) - mean_z,
                "lm_max_score_z": max(member_z),
                "lm_gap_to_best": float(head.get("ln_score_gap") or 0.0),
                "lm_line_count": float(head.get("ln_line_count") or 0.0),
                "lm_bante_score_z": float(bante.get("ln_score_z") or 0.0) if bante else 0.0,
                "lm_bante_older": float(head.get("ln_bante_older") or 0.0),
                "lm_selfpower_ratio": float(head.get("ln_selfpower_ratio") or 0.0),
            }
        )
    return lines, rows


def stage2_features(features_by_car: dict[int, dict], model=None) -> dict[int, dict]:
    """各車へ渡す s2_* を計算する。模型が無い場合は全て0(=無害)。"""
    empty = {car: {name: 0.0 for name in STAGE2_FEATURE_NAMES} for car in features_by_car}
    booster = model if model is not None else _booster(LINE_MODEL_PATH)
    if booster is None or not _HAS_LGBM:
        return empty
    lines, rows = build_line_inputs(features_by_car)
    if len(lines) < 2:
        return empty
    X = np.array([[r[k] for k in LINE_MODEL_FEATURES] for r in rows], dtype=float)
    raw = booster.predict(X)
    total = float(sum(raw)) or 1.0
    probs = [float(p) / total for p in raw]
    order = sorted(range(len(probs)), key=lambda i: -probs[i])
    rank_of = {idx: rank for rank, idx in enumerate(order)}

    out = {}
    for idx, line in enumerate(lines):
        p = probs[idx]
        for pos, car in enumerate(line):
            out[car] = {
                "s2_line_win_p": round(p, 4),
                "s2_line_rank": round(rank_of[idx] / 4.0, 4),
                "s2_line_p_x_head": round(p if pos == 0 else 0.0, 4),
                "s2_line_p_x_bante": round(p if pos == 1 else 0.0, 4),
            }
    for car in features_by_car:
        out.setdefault(car, {name: 0.0 for name in STAGE2_FEATURE_NAMES})
    return out


def train_line_model(races: list[dict], model_path: Path | str = LINE_MODEL_PATH, seed: int = 42):
    """races = [{"features_by_car": {car: feats}, "winner": car_no}, ...] から学習する。"""
    if not _HAS_LGBM:
        return None
    X, y = [], []
    for race in races:
        fbc = race.get("features_by_car") or {}
        winner = race.get("winner")
        if len(fbc) < 2 or not winner:
            continue
        lines, rows = build_line_inputs(fbc)
        if len(lines) < 2:
            continue
        for line, row in zip(lines, rows):
            X.append([row[k] for k in LINE_MODEL_FEATURES])
            y.append(1.0 if winner in line else 0.0)
    if len(X) < 200:
        return None

    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "num_leaves": 15,
        "learning_rate": 0.05,
        "feature_fraction": 0.9,
        "bagging_fraction": 0.9,
        "bagging_freq": 1,
        "min_data_in_leaf": 20,
        "lambda_l2": 1.0,
        "verbose": -1,
        "seed": seed,
        "deterministic": True,
    }
    booster = lgb.train(params, lgb.Dataset(np.array(X, dtype=float), label=np.array(y, dtype=float)), num_boost_round=200)
    path = Path(model_path)
    booster.save_model(str(path))
    meta = {
        "name": "lightgbm-line-win",
        "features": LINE_MODEL_FEATURES,
        "training": {"lines": len(X), "races": len(races), "positive": int(sum(y))},
    }
    Path(str(path).replace(".txt", ".json")).write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    _CACHE.pop(str(path), None)
    return booster
