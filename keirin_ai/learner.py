from __future__ import annotations

import json
import math
import random
from pathlib import Path

from keirin_ai.features import FEATURE_NAMES, MODEL_FEATURE_NAMES, dot, feature_vector
from keirin_ai.line_model import (
    LINE_MODEL_PATH,
    STAGE2_FEATURE_NAMES,
    stage2_features,
    train_line_model,
)
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


def _lgbm_vector(model: dict, features: dict[str, float]):
    """boosterに渡すベクトルを作る。特徴量の本数が食い違う場合は None を返す。

    model_weights.json(特徴量の一覧)と model_lgbm.txt(実体)は同時に
    書き出されるので通常は一致するが、片方だけ古いまま配布されると
    LightGBMが例外を投げて予想が丸ごと止まる。そこは学習前の一時的な状態
    でしかないので、静かに「学習なし」へ落として予想自体は続ける。
    """
    booster = _load_booster(ROOT / model["lgbm_model"])
    names = model.get("features") or MODEL_FEATURE_NAMES
    if booster.num_feature() != len(names):
        return None, None
    return booster, feature_vector(features, names)


def predict_logit(model: dict | None, features: dict[str, float]) -> float:
    if not model:
        return 0.0
    if model.get("backend") == "lightgbm" and _HAS_LGBM:
        booster, vector = _lgbm_vector(model, features)
        if booster is None:
            return 0.0
        return float(booster.predict([vector], raw_score=True)[0])
    return dot(model.get("weights", {}), features)


def predict_probability(model: dict | None, features: dict[str, float]) -> float:
    if not model:
        return 0.5
    if model.get("backend") == "lightgbm" and _HAS_LGBM:
        booster, vector = _lgbm_vector(model, features)
        if booster is None:
            return 0.5
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
        _train_and_attach_line_stage(rows)
        return _train_lightgbm(rows, model_path)
    return _train_logistic(rows, model_path)


def _train_and_attach_line_stage(rows: list[dict], folds: int = 4) -> None:
    """1段目のライン模型を学習し、その確率を各行の特徴量へ足す。

    学習データに渡す確率は out-of-fold で作る。同じデータで学習した模型の
    出力をそのまま特徴量にすると、ライン確率が答えを覚えてしまい、
    見かけの成績だけが良くなるため。
    """
    by_race: dict[str, list[dict]] = {}
    for row in rows:
        by_race.setdefault(row["race_key"], []).append(row)

    races = []
    for key, members in by_race.items():
        fbc = {int(m["car_no"] or 0): m["features"] for m in members}
        winner = next((int(m["car_no"] or 0) for m in members if m.get("label") == 1), None)
        races.append({"race_key": key, "features_by_car": fbc, "winner": winner})

    labelled = [r for r in races if r["winner"]]
    if len(labelled) < 200:
        # 材料が足りないときは s2_* を0のままにする(模型なしと同じ扱い)
        for row in rows:
            row["features"].update({name: 0.0 for name in STAGE2_FEATURE_NAMES})
        return

    # out-of-fold で学習データ側の確率を作る
    keys = sorted(r["race_key"] for r in labelled)
    fold_of = {k: i % folds for i, k in enumerate(keys)}
    for f in range(folds):
        subset = [r for r in labelled if fold_of[r["race_key"]] != f]
        booster = train_line_model(subset, model_path=LINE_MODEL_PATH.with_suffix(".fold.txt"))
        if booster is None:
            continue
        for race in (r for r in races if fold_of.get(r["race_key"]) == f):
            s2 = stage2_features(race["features_by_car"], model=booster)
            for member in by_race[race["race_key"]]:
                member["features"].update(s2.get(int(member["car_no"] or 0), {}))

    # 本番用は全期間で学習して保存する(予想時にはこれを使う)
    train_line_model(labelled, model_path=LINE_MODEL_PATH)

    # foldに割り当てられなかった行(勝者不明など)は0で埋める
    for row in rows:
        for name in STAGE2_FEATURE_NAMES:
            row["features"].setdefault(name, 0.0)
    try:
        LINE_MODEL_PATH.with_suffix(".fold.txt").unlink(missing_ok=True)
    except Exception:
        pass


def _relevance(finish: int | None) -> int:
    """着順を「並べ方の良さ」の点数へ。1着を最も重く、3着まで評価する。"""
    return {1: 3, 2: 2, 3: 1}.get(int(finish or 99), 0)


def _train_lightgbm(rows: list[dict], model_path: Path | str) -> dict:
    """レースを1グループとしたランキング学習(LambdaRank)で並び順を直接学習する。

    以前は「1着かどうか」の二値分類だったが、欲しいのは着順の並びであり
    目的がずれていた(2着・3着の情報も捨てていた)。
    2,570レースの検証では5シードすべてで二値分類を上回った:
      回収率 85.5% → 88.9% / 2車単的中 26.8% → 28.3% / Top1 42.2% → 43.2%
    """
    # レース単位で時系列split(同一レースがtrain/validに跨がらないようにリークを防ぐ)。
    race_order = sorted({row["race_key"] for row in rows})
    split = max(1, int(len(race_order) * 0.85))
    valid_races = set(race_order[split:])

    # ランキング学習はレースごとに行が連続している必要がある
    ordered = sorted(rows, key=lambda r: (r["race_key"], int(r.get("car_no") or 0)))
    train_rows = [r for r in ordered if r["race_key"] not in valid_races]
    valid_rows = [r for r in ordered if r["race_key"] in valid_races]
    if not valid_rows:  # レースが極端に少ない場合は全件学習
        valid_rows = train_rows

    X_train = [feature_vector(r["features"], MODEL_FEATURE_NAMES) for r in train_rows]
    X_valid = [feature_vector(r["features"], MODEL_FEATURE_NAMES) for r in valid_rows]
    y_train = [_relevance(r.get("finish_position")) for r in train_rows]
    y_valid = [r["label"] for r in valid_rows]
    valid_row_races = [r["race_key"] for r in valid_rows]

    def group_sizes(items: list[dict]) -> list[int]:
        sizes: list[int] = []
        last = None
        for r in items:
            if r["race_key"] != last:
                sizes.append(0)
                last = r["race_key"]
            sizes[-1] += 1
        return sizes

    params = {
        "objective": "lambdarank",
        "metric": "ndcg",
        "ndcg_eval_at": [1, 3],
        "label_gain": [0, 1, 3, 7],
        "num_leaves": 15,
        "learning_rate": 0.04,
        "feature_fraction": 0.85,
        "bagging_fraction": 0.85,
        "bagging_freq": 1,
        "min_data_in_leaf": 25,
        "lambda_l2": 1.0,
        "verbose": -1,
    }
    train_set = lgb.Dataset(
        np.array(X_train, dtype=float), label=np.array(y_train, dtype=float), group=group_sizes(train_rows)
    )
    booster = lgb.train(params, train_set, num_boost_round=300)
    booster.save_model(str(LGBM_PATH))

    metrics = _evaluate_lgbm(booster, X_valid, y_valid, valid_row_races)
    importance = _importance(booster)
    model = {
        "name": "lightgbm-rank",
        "backend": "lightgbm",
        "objective": "lambdarank",
        "version": "0.3",
        "target": "finish_order",
        "features": MODEL_FEATURE_NAMES,
        "lgbm_model": str(LGBM_PATH.relative_to(ROOT)).replace("\\", "/"),
        "training": {
            "rows": len(rows),
            "races": len(race_order),
            "positive_rows": int(sum(row["label"] for row in rows)),
            "best_iteration": int(booster.num_trees()),
            "valid_races": len(set(valid_row_races)),
        },
        "metrics": metrics,
        "feature_importance": importance,
    }
    _save_model(model, model_path)
    return model


def _evaluate_lgbm(booster, X_valid: list, y_valid: list, race_keys: list) -> dict:
    """並び順の質で評価する。

    LambdaRankの出力は確率ではなく順位付けのためのスコアなので、
    LogLossは意味を成さない。Top1精度(予測1位が実際に1着か)と
    Top3内率(予測1位が3着以内か)で測る。
    """
    if not X_valid:
        return {}
    scores = booster.predict(np.array(X_valid, dtype=float))
    by_race: dict[str, list[tuple[float, float]]] = {}
    for score, label, race_key in zip(scores, y_valid, race_keys):
        by_race.setdefault(race_key, []).append((float(score), label))
    top1_hits = sum(1 for rows in by_race.values() if max(rows, key=lambda r: r[0])[1] == 1)
    return {
        "top1_accuracy": round(top1_hits / max(1, len(by_race)), 4),
        "eval_split": "time-based 15% holdout",
    }


def _importance(booster) -> dict:
    gains = booster.feature_importance(importance_type="gain")
    pairs = sorted(zip(MODEL_FEATURE_NAMES, gains), key=lambda item: item[1], reverse=True)
    return {name: round(float(gain), 1) for name, gain in pairs[:12]}


def _train_logistic(rows: list[dict], model_path: Path | str) -> dict:
    weights = {name: 0.0 for name in MODEL_FEATURE_NAMES}
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
            for name in MODEL_FEATURE_NAMES:
                value = float(features.get(name, 0.0))
                weights[name] -= lr * (error * value + l2 * weights[name])

    metrics = _evaluate_logistic(rows, weights)
    model = {
        "name": "online-logistic-win",
        "backend": "linear",
        "version": "0.1",
        "target": "win",
        "features": MODEL_FEATURE_NAMES,
        "weights": {name: round(weights[name], 6) for name in MODEL_FEATURE_NAMES},
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
        "features": MODEL_FEATURE_NAMES,
        "weights": {name: 0.0 for name in MODEL_FEATURE_NAMES},
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
