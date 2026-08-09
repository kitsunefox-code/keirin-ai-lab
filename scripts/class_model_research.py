from __future__ import annotations

"""級班別にモデルを分ける効果を測る。

先行調査によれば決まり手の分布は級班で大きく違う:
  S級9車立て   逃12.5% / 捲35.4% / 差52.0%
  チャレンジ    逃38.8% / 捲25.8% / 差35.3%
  ガールズ      逃21.0% / 捲48.3% / 差30.6%
レースの決まり方そのものが別物なので、単一モデルで全部を扱うのは
損をしている可能性がある。

ただしデータは A級1,702 / S級657 / L級251レースしかなく、
分割すると1モデルあたりの学習量が減る。分けた方が得か、
まとめた方が得かは実測でしか分からない。

比較するもの:
  単一         全級班を1つのモデルで学習(現行)
  級班別       A級 / S級 / L級 で別々に学習し、予想時に振り分け
  単一+重み    1つのモデルだが、同じ級班のレースを重く学習する

python scripts/class_model_research.py --db <path>
"""

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402

from keirin_ai.features import FEATURE_NAMES, LINE_FEATURE_NAMES, MARK_FEATURE_NAMES  # noqa: E402
from scripts.ablation_study import roi_ci  # noqa: E402
from scripts.line_features_research import _json, _norm_date  # noqa: E402
from scripts.ranking_research import evaluate, relevance  # noqa: E402

PARAMS = {
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


def class_group(text: str) -> str:
    """級班をまとめる。数が少ない区分は寄せる。"""
    t = str(text or "")
    if t.startswith("L"):
        return "L"  # ガールズ(ラインが無いので本来別物)
    if t.startswith("S"):
        return "S"
    if t.startswith("A"):
        return "A"
    return "other"


def load_rows(db: str) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    races = {
        r["race_key"]: r
        for r in conn.execute(
            "select race_key, race_date, race_class_official, result_json, payouts_json from races"
        ).fetchall()
    }
    ents = conn.execute(
        """
        select race_key, car_no, features_json, finish_position, is_win
        from entries where finish_position is not null and features_json is not null
        """
    ).fetchall()
    conn.close()

    rows = []
    for e in ents:
        race = races.get(e["race_key"])
        if not race:
            continue
        date = _norm_date(race["race_date"])
        if not date:
            continue
        rows.append(
            {
                "race_key": e["race_key"],
                "car_no": int(e["car_no"] or 0),
                "features": _json(e["features_json"], {}) or {},
                "label": int(e["is_win"] or 0),
                "finish": int(e["finish_position"] or 99),
                "date": date,
                "cls": class_group(race["race_class_official"]),
                "payouts": race["payouts_json"],
                "result": race["result_json"],
            }
        )
    return rows


def _groups(items: list[dict]) -> list[int]:
    sizes: list[int] = []
    last = None
    for r in items:
        if r["race_key"] != last:
            sizes.append(0)
            last = r["race_key"]
        sizes[-1] += 1
    return sizes


def train(rows: list[dict], names: list[str], seed: int, weights: list[float] | None = None):
    rows = sorted(rows, key=lambda r: (r["race_key"], r["car_no"]))
    X = np.array([[r["features"].get(n, 0.0) for n in names] for r in rows], dtype=float)
    y = np.array([relevance(r["finish"]) for r in rows], dtype=float)
    ds = lgb.Dataset(X, label=y, group=_groups(rows), weight=weights)
    return lgb.train({**PARAMS, "seed": seed, "deterministic": True}, ds, num_boost_round=300)


def predict(model, rows: list[dict], names: list[str]) -> np.ndarray:
    X = np.array([[r["features"].get(n, 0.0) for n in names] for r in rows], dtype=float)
    return model.predict(X)


def _predict_by_class(va: list[dict], models: dict, fallback, names: list[str]) -> np.ndarray:
    """級班ごとにまとめて予測する(1行ずつ呼ぶと桁違いに遅い)。"""
    scores = np.zeros(len(va))
    buckets: dict[str, list[int]] = {}
    for i, r in enumerate(va):
        buckets.setdefault(r["cls"], []).append(i)
    for cls, idxs in buckets.items():
        model = models.get(cls, fallback)
        sub = [va[i] for i in idxs]
        preds = predict(model, sub, names)
        for i, p in zip(idxs, preds):
            scores[i] = p
    return scores


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "keirin_learning.sqlite3"))
    args = ap.parse_args()

    rows = load_rows(args.db)
    dates = sorted({r["date"] for r in rows})
    names = [f for f in FEATURE_NAMES if f not in MARK_FEATURE_NAMES] + LINE_FEATURE_NAMES
    cuts = [dates[int(len(dates) * f)] for f in (0.55, 0.62, 0.70, 0.78, 0.85)]

    by_cls: dict[str, int] = {}
    for r in rows:
        by_cls[r["cls"]] = by_cls.get(r["cls"], 0) + 1
    print(f"教師: {len({r['race_key'] for r in rows}):,}レース / {len(rows):,}行")
    print("級班別の行数:", {k: v for k, v in sorted(by_cls.items(), key=lambda x: -x[1])})
    print()

    results: dict[str, list] = {"単一(現行)": [], "級班別": [], "単一+級班重み": []}
    rets: dict[str, list] = {k: [] for k in results}

    for seed in (1, 7, 42, 123, 2026):
        acc: dict[str, list] = {k: [] for k in results}
        for cut in cuts:
            tr = [r for r in rows if r["date"] < cut]
            va = [r for r in rows if r["date"] >= cut]
            if not tr or not va:
                continue

            # 1) 単一モデル
            m = train(tr, names, seed)
            res = evaluate(va, predict(m, va, names))
            acc["単一(現行)"].append(res)

            # 2) 級班別モデル(該当級班の学習量が足りなければ単一モデルで代替)
            models = {}
            for cls in set(r["cls"] for r in tr):
                sub = [r for r in tr if r["cls"] == cls]
                if len({r["race_key"] for r in sub}) >= 150:
                    models[cls] = train(sub, names, seed)
            acc["級班別"].append(evaluate(va, _predict_by_class(va, models, m, names)))

            # 3) 単一モデルのまま、同じ級班を重く学習する
            #    級班ごとに別モデルを作らずに、分布の違いだけ反映させる狙い
            per_cls = {}
            for cls in set(r["cls"] for r in tr):
                w = [3.0 if r["cls"] == cls else 1.0 for r in sorted(tr, key=lambda x: (x["race_key"], x["car_no"]))]
                if len({r["race_key"] for r in tr if r["cls"] == cls}) >= 150:
                    per_cls[cls] = train(tr, names, seed, weights=w)
            acc["単一+級班重み"].append(evaluate(va, _predict_by_class(va, per_cls, m, names)))

        for k, lst in acc.items():
            if not lst:
                continue
            results[k].append(
                (
                    sum(x["top1"] for x in lst) / len(lst),
                    sum(x["exacta_hit"] for x in lst) / len(lst),
                    sum(x["roi"] for x in lst) / len(lst),
                )
            )
            for x in lst:
                rets[k].extend(x["returns"])

    print(f"{'条件':<18}{'Top1':>8}{'2車単的中':>10}{'回収率':>9}{'95%信頼区間':>20}")
    print("-" * 66)
    for k, v in results.items():
        if not v:
            continue
        lo, hi = roi_ci(rets[k])
        print(
            f"{k:<18}{sum(x[0] for x in v)/len(v)*100:>7.1f}%"
            f"{sum(x[1] for x in v)/len(v)*100:>9.1f}%{sum(x[2] for x in v)/len(v)*100:>8.1f}%"
            f"{f'{lo*100:.1f}〜{hi*100:.1f}%':>20}"
        )

    base = results["単一(現行)"]
    print()
    for k in ("級班別", "単一+級班重み"):
        v = results[k]
        if not v or not base:
            continue
        wins = sum(1 for a, b in zip(v, base) if a[2] > b[2])
        hw = sum(1 for a, b in zip(v, base) if a[1] > b[1])
        d = (sum(x[2] for x in v) / len(v) - sum(x[2] for x in base) / len(base)) * 100
        print(f"{k}: 回収率 {d:+.1f}pt / 回収率で上回った回数 {wins}/{len(v)} / 的中率 {hw}/{len(v)}")


if __name__ == "__main__":
    main()
