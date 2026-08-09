from __future__ import annotations

"""2段構成モデルの検証: ①どのラインが勝つかを当てる ②その確率を選手モデルへ渡す。

競輪はライン単位で決着する競技で、ライン内で1-2着が決まる「スジ決着」が48.5%
(64,321レースの先行分析)。選手を個別に評価するだけではこの構造を捉えきれない。

そこで先に「ライン単位の勝率」を学習し、その出力を選手モデルの特徴量として渡す。
先行分析では主導権ラインの予測に成功しているが、決まり手やバック取得の記録が
手元に無いため、ここでは「勝者を出したライン」を正解として学習する。

リーク対策:
- ライン模型は学習期間のレースだけで学習する
- 学習データ側へ渡す確率は out-of-fold(自分自身を含まないfoldの模型)で作る。
  同じデータで学習した模型の出力をそのまま特徴量にすると、
  ライン確率が答えを覚えてしまい、検証成績だけが良く見える

python scripts/two_stage_research.py --db <path>
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lightgbm as lgb  # noqa: E402
import numpy as np  # noqa: E402

from keirin_ai.features import FEATURE_NAMES, LINE_FEATURE_NAMES, MARK_FEATURE_NAMES  # noqa: E402
from scripts.ablation_study import roi_ci, train_eval  # noqa: E402
from scripts.line_features_research import _json, load_rows  # noqa: E402

# ライン模型が使う特徴量(ライン1本につき1行)
LINE_MODEL_FEATURES = [
    "lm_size",
    "lm_leader_score_z",
    "lm_leader_back",
    "lm_leader_home",
    "lm_leader_escape",
    "lm_leader_allround",
    "lm_avg_score_z",
    "lm_max_score_z",
    "lm_gap_to_best",
    "lm_line_count",
    "lm_bante_score_z",
    "lm_bante_older",
    "lm_selfpower_ratio",
]

# 2段目の選手モデルへ渡す特徴量
STAGE2_FEATURES = ["s2_line_win_p", "s2_line_rank", "s2_line_p_x_head", "s2_line_p_x_bante"]


def group_by_race(rows: list[dict]) -> dict[str, list[dict]]:
    races: dict[str, list[dict]] = {}
    for r in rows:
        races.setdefault(r["race_key"], []).append(r)
    return races


def lines_of_race(members: list[dict]) -> list[list[dict]]:
    """ln_* 特徴から隊列を復元する(ln_pos が 0 の選手が先頭)。"""
    by_car = {m["car_no"]: m for m in members}
    # ln_pos は min(pos,3)/3 で保存されている
    ordered = sorted(members, key=lambda m: (m["features"].get("ln_pos", 0.0), m["car_no"]))
    # 隊列そのものは保持していないので、ln_own_avg_score が同じ選手を同一ラインとみなす
    groups: dict[tuple, list[dict]] = {}
    for m in ordered:
        f = m["features"]
        key = (round(f.get("ln_own_avg_score", 0.0), 4), round(f.get("ln_score_gap", 0.0), 4), round(f.get("ln_size_rel", 0.0), 4))
        groups.setdefault(key, []).append(m)
    out = []
    for g in groups.values():
        g.sort(key=lambda m: m["features"].get("ln_pos", 0.0))
        out.append(g)
    _ = by_car
    return out


def build_line_rows(races: dict[str, list[dict]]) -> tuple[list[dict], dict[str, list]]:
    """ライン1本=1行のデータを作る。戻り値=(行, レースごとのライン一覧)"""
    line_rows = []
    per_race = {}
    for key, members in races.items():
        lines = lines_of_race(members)
        if len(lines) < 2:
            continue
        scores = [m["features"].get("ln_score_z", 0.0) for m in members]
        mean_z = sum(scores) / len(scores) if scores else 0.0
        info = []
        for idx, line in enumerate(lines):
            head = line[0]
            hf = head["features"]
            bante = line[1] if len(line) > 1 else None
            row = {
                "lm_size": len(line) / 4.0,
                "lm_leader_score_z": hf.get("ln_score_z", 0.0),
                "lm_leader_back": hf.get("ln_leader_back", 0.0),
                "lm_leader_home": hf.get("ln_leader_home", 0.0),
                "lm_leader_escape": hf.get("ln_leader_escape", 0.0),
                "lm_leader_allround": 1.0 if hf.get("style_allround") else 0.0,
                "lm_avg_score_z": sum(m["features"].get("ln_score_z", 0.0) for m in line) / len(line) - mean_z,
                "lm_max_score_z": max(m["features"].get("ln_score_z", 0.0) for m in line),
                "lm_gap_to_best": hf.get("ln_score_gap", 0.0),
                "lm_line_count": hf.get("ln_line_count", 0.0),
                "lm_bante_score_z": (bante["features"].get("ln_score_z", 0.0) if bante else 0.0),
                "lm_bante_older": hf.get("ln_bante_older", 0.0),
                "lm_selfpower_ratio": hf.get("ln_selfpower_ratio", 0.0),
            }
            won = 1 if any(m["label"] == 1 for m in line) else 0
            line_rows.append({"race_key": key, "line_idx": idx, "date": members[0]["date"], "features": row, "label": won})
            info.append((idx, [m["car_no"] for m in line]))
        per_race[key] = info
    return line_rows, per_race


def train_line_model(rows: list[dict], seed: int = 42):
    X = np.array([[r["features"][k] for k in LINE_MODEL_FEATURES] for r in rows], dtype=float)
    y = np.array([r["label"] for r in rows], dtype=float)
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
    return lgb.train(params, lgb.Dataset(X, label=y), num_boost_round=200)


def predict_line(model, rows: list[dict]) -> np.ndarray:
    X = np.array([[r["features"][k] for k in LINE_MODEL_FEATURES] for r in rows], dtype=float)
    return model.predict(X)


def attach_stage2(rows: list[dict], line_rows: list[dict], probs: np.ndarray, races: dict[str, list[dict]]) -> None:
    """ライン確率を選手行へ配る(レース内で合計1に正規化)。"""
    by_race: dict[str, list] = {}
    for lr, p in zip(line_rows, probs):
        by_race.setdefault(lr["race_key"], []).append((lr["line_idx"], float(p)))

    for key, items in by_race.items():
        total = sum(p for _, p in items) or 1.0
        norm = {idx: p / total for idx, p in items}
        ranked = sorted(norm.items(), key=lambda x: -x[1])
        rank_of = {idx: i for i, (idx, _) in enumerate(ranked)}
        members = races.get(key) or []
        lines = lines_of_race(members)
        for idx, line in enumerate(lines):
            p = norm.get(idx, 0.0)
            for pos, m in enumerate(line):
                m["features"]["s2_line_win_p"] = p
                m["features"]["s2_line_rank"] = rank_of.get(idx, 0) / 4.0
                m["features"]["s2_line_p_x_head"] = p if pos == 0 else 0.0
                m["features"]["s2_line_p_x_bante"] = p if pos == 1 else 0.0


def run(rows: list[dict], cut: str, folds: int = 4, seed: int = 42, verbose: bool = True) -> None:
    races = group_by_race(rows)
    line_rows, _ = build_line_rows(races)
    tr_lines = [r for r in line_rows if r["date"] < cut]
    va_lines = [r for r in line_rows if r["date"] >= cut]
    if not tr_lines or not va_lines:
        return

    # 学習側は out-of-fold で確率を作る(自分を学習に含んだ模型で予測しない)
    keys = sorted({r["race_key"] for r in tr_lines})
    fold_of = {k: i % folds for i, k in enumerate(keys)}
    oof = np.zeros(len(tr_lines))
    for f in range(folds):
        sub = [r for r in tr_lines if fold_of[r["race_key"]] != f]
        hold_idx = [i for i, r in enumerate(tr_lines) if fold_of[r["race_key"]] == f]
        if not sub or not hold_idx:
            continue
        m = train_line_model(sub, seed=seed)
        oof[hold_idx] = predict_line(m, [tr_lines[i] for i in hold_idx])

    # 検証側は学習期間の全データで作った模型で予測する
    final = train_line_model(tr_lines, seed=seed)
    va_pred = predict_line(final, va_lines)

    # ライン模型そのものの精度
    hit = 0
    n = 0
    by_race: dict[str, list] = {}
    for lr, p in zip(va_lines, va_pred):
        by_race.setdefault(lr["race_key"], []).append((float(p), lr["label"]))
    for items in by_race.values():
        items.sort(key=lambda x: -x[0])
        hit += 1 if items[0][1] == 1 else 0
        n += 1
    base = 1.0 / (sum(len(v) for v in by_race.values()) / max(1, n))
    if verbose: print(f"  ライン模型の的中率: {hit/n*100:.1f}%  (でたらめなら {base*100:.1f}%)  検証{n}レース")

    attach_stage2(rows, tr_lines, oof, races)
    attach_stage2(rows, va_lines, va_pred, races)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "keirin_learning.sqlite3"))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    rows = load_rows(args.db)
    dates = sorted({r["date"] for r in rows})
    print(f"教師: {len({r['race_key'] for r in rows}):,}レース / {len(rows):,}行")
    print(f"期間: {dates[0]} 〜 {dates[-1]}\n")

    no_mark = [f for f in FEATURE_NAMES if f not in MARK_FEATURE_NAMES]
    base_feats = no_mark + LINE_FEATURE_NAMES

    cuts = [dates[int(len(dates) * f)] for f in (0.55, 0.62, 0.70, 0.78, 0.85)]

    results: dict[str, list] = {"1段(現行の最良)": [], "2段(ライン確率つき)": []}
    rets: dict[str, list] = {"1段(現行の最良)": [], "2段(ライン確率つき)": []}
    for cut in cuts:
        if not args.quiet: print(f"[分割 {cut}]")
        run(rows, cut, seed=args.seed, verbose=not args.quiet)
        a = train_eval(rows, base_feats, cut, seed=args.seed)
        b = train_eval(rows, base_feats + STAGE2_FEATURES, cut, seed=args.seed)
        if not a or not b:
            continue
        results["1段(現行の最良)"].append((a["top1"], a["exacta_hit"], a["roi"]))
        results["2段(ライン確率つき)"].append((b["top1"], b["exacta_hit"], b["roi"]))
        rets["1段(現行の最良)"].extend(a["returns"])
        rets["2段(ライン確率つき)"].extend(b["returns"])
        if not args.quiet: print(f"  1段 回収率 {a['roi']*100:5.1f}%  →  2段 回収率 {b['roi']*100:5.1f}%")

    print(f"\n{'条件':<24}{'Top1':>8}{'2車単的中':>10}{'回収率':>9}{'95%信頼区間':>20}")
    print("-" * 72)
    for label, vals in results.items():
        if not vals:
            continue
        t = sum(v[0] for v in vals) / len(vals)
        h = sum(v[1] for v in vals) / len(vals)
        r = sum(v[2] for v in vals) / len(vals)
        lo, hi = roi_ci(rets[label])
        print(f"{label:<24}{t*100:>7.1f}%{h*100:>9.1f}%{r*100:>8.1f}%{f'{lo*100:.1f}〜{hi*100:.1f}%':>20}")


if __name__ == "__main__":
    main()
