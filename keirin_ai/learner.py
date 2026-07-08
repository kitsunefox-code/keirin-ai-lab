from __future__ import annotations

import json
import math
import random
from pathlib import Path

from keirin_ai.features import FEATURE_NAMES, dot
from keirin_ai.storage import DEFAULT_DB_PATH, connect, training_rows


ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "data" / "model_weights.json"


def load_model(path: Path | str = MODEL_PATH) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def predict_logit(model: dict | None, features: dict[str, float]) -> float:
    if not model:
        return 0.0
    return dot(model.get("weights", {}), features)


def predict_probability(model: dict | None, features: dict[str, float]) -> float:
    return _sigmoid(predict_logit(model, features))


def train_win_model(db_path: Path | str = DEFAULT_DB_PATH, model_path: Path | str = MODEL_PATH) -> dict:
    with connect(db_path) as conn:
        rows = training_rows(conn)

    if not rows:
        model = _empty_model("No result rows yet.")
        _save_model(model, model_path)
        return model

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

    metrics = _evaluate(rows, weights)
    model = {
        "name": "online-logistic-win",
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


def _evaluate(rows: list[dict], weights: dict[str, float]) -> dict:
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
    }


def _empty_model(reason: str) -> dict:
    return {
        "name": "online-logistic-win",
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
