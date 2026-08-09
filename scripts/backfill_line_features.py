from __future__ import annotations

"""過去の entries.features_json にライン特徴量を追記する。

ライン特徴を新設しても、過去データに入っていなければ学習時に全部ゼロになり
「効かない特徴量」として扱われてしまう。保存済みの隊列(races.lineup_json)と
選手情報から再計算して追記する。

冪等: 何度実行しても結果は同じ。既存の特徴量は上書きしない(ln_* のみ追加)。

python scripts/backfill_line_features.py --db <path> [--apply]
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.features import LINE_FEATURE_NAMES, build_line_row  # noqa: E402


def _json(raw, default=None):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "keirin_learning.sqlite3"))
    ap.add_argument("--apply", action="store_true", help="実際に書き込む(既定は件数確認のみ)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    races = {
        r["race_key"]: r["lineup_json"]
        for r in conn.execute("select race_key, lineup_json from races").fetchall()
    }
    ents = conn.execute(
        """
        select race_key, car_no, racing_score, style, age, features_json
        from entries where features_json is not null
        """
    ).fetchall()

    by_race: dict[str, list] = {}
    for e in ents:
        by_race.setdefault(e["race_key"], []).append(e)

    updated = skipped = 0
    for key, members in by_race.items():
        lineup = _json(races.get(key), []) or []
        # build_line_row が期待する形(race.entrants)へ組み直す。
        # stats は features_json から復元する(back_count/home_count/win_rate は
        # 正規化済みなので、build_line_row の正規化と揃うよう元の尺度へ戻す)
        entrants = []
        feats_by_car = {}
        for m in members:
            car = int(m["car_no"] or 0)
            f = _json(m["features_json"], {}) or {}
            feats_by_car[car] = f
            entrants.append(
                {
                    "car_no": car,
                    "racing_score": m["racing_score"],
                    "style": m["style"],
                    "age": m["age"],
                    "stats": {
                        "back_count": float(f.get("back_count") or 0.0) * 12.0,
                        "home_count": float(f.get("home_count") or 0.0) * 12.0,
                        "win_rate": float(f.get("win_rate") or 0.0) * 100.0,
                    },
                }
            )
        race = {"lineup": lineup, "entrants": entrants}
        for m in members:
            car = int(m["car_no"] or 0)
            f = feats_by_car[car]
            if all(k in f for k in LINE_FEATURE_NAMES):
                skipped += 1
                continue
            f.update(build_line_row(race, car))
            if args.apply:
                conn.execute(
                    "update entries set features_json=? where race_key=? and car_no=?",
                    (json.dumps(f, ensure_ascii=False), key, car),
                )
            updated += 1

    if args.apply:
        conn.commit()
    conn.close()
    print(json.dumps(
        {"races": len(by_race), "updated": updated, "already_had": skipped, "applied": args.apply},
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()
