from __future__ import annotations

import json
import math
import random
from pathlib import Path

from keirin_ai.features import FEATURE_NAMES, dot, feature_vector
from keirin_ai.storage import DEFAULT_DB_PATH, connect, training_rows


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "data" / "model_weights.json"
LGBM_PATH = ROOT / "data" / "model_lgbm.txt"

# LightGBMへ切り替える最低教師レース数。これ未満は安定する線形モデルを使う。
LGBM_MIN_RACES = 200

try:  # LightGBMは任意依存。無い環境では線形ロジスティックにフォールバック。
    import lightgbm as lgb
    import numpy as np

    _HAS_LGBM = True
except Exception:  # pragma: no cover
    _HAS_LGBM = False


_BOOSTER_CACHE: dict[str, tuple[float, object]] = {}


def load_model(path: Path | str = MODEL_PATH) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_booster(path: Path):
    """LightGBM boosterをmtimeキャッシュ付きで読み込む。"""
    key = str(path)
    mtime = path.stat().st_mtime
    cached = _BOOSTER_CACHE.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    booster = lgb.Booster(model_file=str(path))
    _BOOSTER_CACHE[key] = (mtime, booster)
    return booster


def predict_logit(model: dict | None, features: dict[str, float]) -> float:
    if not model:
        return 0.0
    if model.get("backend") == "lightgbm" and _HAS_LGBM:
        booster = _load_booster(ROOT / model["lgbm_model"])
        vector = feature_vector(features, model.get("features", FEATURE_NAMES))
        return float(booster.predict([vector], raw_score=True)[0])
    return dot(model.get("weights", {}), features)


def predict_probability(model: dict | None, features: dict[str, float]) -> float:
    if not model:
        return 0.5
    if model.get("backend") == "lightgbm" and _HAS_LGBM:
        booster = _load_booster(ROOT / model["lgbm_model"])
        vector = feature_vector(features, model.get("features", FEATURE_NAMES))
        return float(booster.predict([vector])[0])
    return _sigmoid(predict_logit(model, features))


def train_win_model(db_path: Path | str = DEFAULT_DB_PATH, model_path: Path | str = MODEL_PATH) -> dict:
    with connect(db_path) as conn:
        rows = training_rows(conn)

    if not rows:
        model = _empty_model("No result rows yet.")
        _save_model(model, model_path)
        return model

    race_count = len({row["race_key"] for row in rows})
    if _HAS_LGBM and race_count >= LGBM_MIN_RACES:
        return _train_lightgbm(rows, model_path)
    return _train_logistic(rows, model_path)


def _train_lightgbm(rows: list[dict], model_path: Path | str) -> dict:
    # レース単位で時系列split(同一レースがtrain/validに跨がらないようにリークを防ぐ)。
    race_order = sorted({row["race_key"] for row in rows})
    split = max(1, int(len(race_order) * 0.85))
    valid_races = set(race_order[split:])

    X_train, y_train, X_valid, y_valid, valid_row_races = [], [], [], [], []
    for row in rows:
        vector = feature_vector(row["features"], FEATURE_NAMES)
        if row["race_key"] in valid_races:
            X_valid.append(vector)
            y_valid.append(row["label"])
            valid_row_races.append(row["race_key"])
        else:
            X_train.append(vector)
            y_train.append(row["label"])

    if not X_valid:  # レースが極端に少ない場合は全件学習
        X_valid, y_valid, valid_row_races = X_train, y_train, [r["race_key"] for r in rows]

    pos = sum(y_train) or 1
    neg = max(1, len(y_train) - pos)
    params = {
        "objective": "binary",
        "metric": "binary_logloss",
        "num_leaves": 15,
        "learning_rate": 0.04,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 1,
        "min_data_in_leaf": 25,
        "lambda_l2": 1.0,
        "scale_pos_weight": min(6.0, neg / pos),
        "verbose": -1,
    }
    train_set = lgb.Dataset(np.array(X_train, dtype=float), label=np.array(y_train, dtype=float))
    valid_set = lgb.Dataset(np.array(X_valid, dtype=float), label=np.array(y_valid, dtype=float))
    booster = lgb.train(
        params,
        train_set,
        num_boost_round=400,
        valid_sets=[valid_set],
        callbacks=[lgb.early_stopping(40, verbose=False), lgb.log_evaluation(0)],
    )
    booster.save_model(str(LGBM_PATH), num_iteration=booster.best_iteration)

    metrics = _evaluate_lgbm(booster, X_valid, y_valid, valid_row_races)
    importance = _importance(booster)
    model = {
        "name": "lightgbm-win",
        "backend": "lightgbm",
        "version": "0.2",
        "target": "win",
        "features": FEATURE_NAMES,
        "lgbm_model": str(LGBM_PATH.relative_to(ROOT)).replace("\\", "/"),
        "training": {
            "rows": len(rows),
            "races": len(race_order),
            "positive_rows": int(sum(row["label"] for row in rows)),
            "best_iteration": int(booster.best_iteration or booster.num_trees()),
            "valid_races": len(set(valid_row_races)),
        },
        "metrics": metrics,
        "feature_importance": importance,
    }
    _save_model(model, model_path)
    return model


def _evaluate_lgbm(booster, X_valid: list, y_valid: list, race_keys: list) -> dict:
    if not X_valid:
        return {}
    probs = booster.predict(np.array(X_valid, dtype=float))
    losses = []
    by_race: dict[str, list[tuple[float, float]]] = {}
    for prob, label, race_key in zip(probs, y_valid, race_keys):
        prob = float(prob)
        losses.append(-1.0 * (label * math.log(max(prob, 1e-9)) + (1 - label) * math.log(max(1 - prob, 1e-9))))
        by_race.setdefault(race_key, []).append((prob, label))
    top1_hits = sum(1 for rows in by_race.values() if max(rows, key=lambda r: r[0])[1] == 1)
    return {
        "log_loss": round(sum(losses) / max(1, len(losses)), 4),
        "top1_accuracy": round(top1_hits / max(1, len(by_race)), 4),
        "eval_split": "time-based 15% holdout",
    }


def _importance(booster) -> dict:
    gains = booster.feature_importance(importance_type="gain")
    pairs = sorted(zip(FEATURE_NAMES, gains), key=lambda item: item[1], reverse=True)
    return {name: round(float(gain), 1) for name, gain in pairs[:12]}


def _train_logistic(rows: list[dict], model_path: Path | str) -> dict:
    weights = {name: 0.0 for name in FEATURE_NAMES}
    rng = random.Random(42)
    epochs = 700
    lr = 0.08
    l2 = 0.001
    pos_count = sum(row["label"] for row in rows)
    neg_count = max(1, len(rows) - pos_count)
    pos_weight = min(6.0, neg_count / max(1, pos_count))

    train_rows = rows[:]
    for _ in range(epochs):
        rng.shuffle(train_rows)
        for row in train_rows:
            y = float(row["label"])
            features = row["features"]
            p = _sigmoid(dot(weights, features))
            sample_weight = pos_weight if y == 1.0 else 1.0
            error = (p - y) * sample_weight
            for name in FEATURE_NAMES:
                value = float(features.get(name, 0.0))
                weights[name] -= lr * (error * value + l2 * weights[name])

    metrics = _evaluate_logistic(rows, weights)
    model = {
        "name": "online-logistic-win",
        "backend": "linear",
        "version": "0.1",
        "target": "win",
        "features": FEATURE_NAMES,
        "weights": {name: round(weights[name], 6) for name in FEATURE_NAMES},
        "training": {
            "rows": len(rows),
            "races": len({row["race_key"] for row in rows}),
            "positive_rows": int(pos_count),
            "epochs": epochs,
        },
        "metrics": metrics,
    }
    _save_model(model, model_path)
    return model


def _evaluate_logistic(rows: list[dict], weights: dict[str, float]) -> dict:
    by_race: dict[str, list[dict]] = {}
    losses = []
    for row in rows:
        p = _sigmoid(dot(weights, row["features"]))
        y = row["label"]
        losses.append(-1.0 * (y * math.log(max(p, 1e-9)) + (1 - y) * math.log(max(1 - p, 1e-9))))
        by_race.setdefault(row["race_key"], []).append({**row, "p": p})

    top1_hits = 0
    for race_rows in by_race.values():
        predicted = max(race_rows, key=lambda item: item["p"])
        if predicted["label"] == 1:
            top1_hits += 1

    return {
        "log_loss": round(sum(losses) / max(1, len(losses)), 4),
        "top1_accuracy": round(top1_hits / max(1, len(by_race)), 4),
        "eval_split": "in-sample",
    }


def _empty_model(reason: str) -> dict:
    return {
        "name": "online-logistic-win",
        "backend": "linear",
        "version": "0.1",
        "target": "win",
        "features": FEATURE_NAMES,
        "weights": {name: 0.0 for name in FEATURE_NAMES},
        "training": {"rows": 0, "races": 0, "positive_rows": 0, "epochs": 0},
        "metrics": {},
        "warning": reason,
    }


def _save_model(model: dict, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")


def _sigmoid(value: float) -> float:
    value = max(-30.0, min(30.0, value))
    return 1.0 / (1.0 + math.exp(-value))
