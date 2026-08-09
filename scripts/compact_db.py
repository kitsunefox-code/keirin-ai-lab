from __future__ import annotations

"""DBをGitHubの100MB制限より十分下に保つ(自動更新の停止を防ぐ)。

data/keirin_learning.sqlite3 はgit管理されており、100MBを超えると
push が pre-receive hook で拒否され、クラウドの自動更新が完全に止まる。
実際に2026-08-04にこれで停止し、5日間サイトが更新されなかった。

やること(いずれも冪等):
1. 結果確定済みレースの「2件目以降の予想」を削除
   実績評価も決済も min(id) しか読まないため影響しない
2. ranking_json から features を除去
   モデル内部の中間値でUIでは未使用。entries.features_json に同じものがある
3. VACUUM で実ファイルを縮める

毎回のクラウド更新から呼ばれる。閾値を超えたときだけ働くので普段は無害。

python scripts/compact_db.py              # 状況を見るだけ
python scripts/compact_db.py --apply      # 実行
python scripts/compact_db.py --apply --threshold-mb 80
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DUP_WHERE = """
from predictions p
join races r on r.race_key = p.race_key
where r.result_json is not null
  and p.id not in (select min(id) from predictions group by race_key)
"""


def db_mb(conn: sqlite3.Connection) -> float:
    page = conn.execute("pragma page_count").fetchone()[0]
    size = conn.execute("pragma page_size").fetchone()[0]
    return page * size / 1024 / 1024


def main() -> None:
    parser = argparse.ArgumentParser(description="Keep the tracked SQLite DB safely under GitHub's 100MB limit.")
    parser.add_argument("--apply", action="store_true", help="実際に圧縮する(既定は状況表示のみ)")
    parser.add_argument(
        "--threshold-mb",
        type=float,
        default=80.0,
        help="この容量を超えていたら圧縮する(既定80MB。100MBの手前で余裕をもって動かす)",
    )
    args = parser.parse_args()

    from keirin_ai.storage import connect, slim_rankings

    report: dict = {}
    with connect() as conn:
        conn.row_factory = sqlite3.Row
        before = db_mb(conn)
        report["size_before_mb"] = round(before, 1)
        report["threshold_mb"] = args.threshold_mb

        dup_rows = conn.execute(f"select count(*) {DUP_WHERE}").fetchone()[0]
        report["duplicate_predictions"] = dup_rows

        if not args.apply:
            report["action"] = "dry-run"
            report["would_compact"] = before > args.threshold_mb
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return

        if before <= args.threshold_mb:
            report["action"] = "skipped(閾値以下)"
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return

        races_before = conn.execute("select count(distinct race_key) from predictions").fetchone()[0]

        # 1) 重複予想を削除(各レースの最初の予想は必ず残す)
        conn.execute(f"delete from predictions where id in (select p.id {DUP_WHERE})")

        # 2) ranking_json から features を除去
        slimmed = 0
        rows = conn.execute("select id, ranking_json from predictions").fetchall()
        for row in rows:
            try:
                ranking = json.loads(row["ranking_json"])
            except Exception:
                continue
            packed = json.dumps(slim_rankings(ranking), ensure_ascii=False, separators=(",", ":"))
            if packed != row["ranking_json"]:
                conn.execute("update predictions set ranking_json=? where id=?", (packed, row["id"]))
                slimmed += 1
        conn.commit()

        # 3) features_json の冗長な浮動小数を4桁へ丸める
        #    0.012999999999999545 のような値がそのまま入っており、
        #    モデルには無意味な桁でDBだけが膨らむ
        rounded = 0
        ent = conn.execute("select rowid, features_json from entries where features_json is not null").fetchall()
        for row in ent:
            try:
                feats = json.loads(row["features_json"])
            except Exception:
                continue
            slim = {k: round(float(v), 4) for k, v in feats.items() if isinstance(v, (int, float))}
            packed = json.dumps(slim, ensure_ascii=False, separators=(",", ":"))
            if packed != row["features_json"]:
                conn.execute("update entries set features_json=? where rowid=?", (packed, row["rowid"]))
                rounded += 1
        conn.commit()
        report["rounded_features"] = rounded

        races_after = conn.execute("select count(distinct race_key) from predictions").fetchone()[0]
        if races_after != races_before:
            report["error"] = f"対象レース数が変化({races_before}->{races_after})。中止。"
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return

        conn.execute("vacuum")
        report.update(
            action="compacted",
            deleted_duplicates=dup_rows,
            slimmed_rankings=slimmed,
            races_kept=races_after,
            size_after_mb=round(db_mb(conn), 1),
        )

    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
