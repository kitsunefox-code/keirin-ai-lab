from __future__ import annotations

"""結果確定済みレースの「2件目以降の予想」を削除してDBを縮める。

背景: DBがGitHubの上限100MBに迫り、超えると毎朝の自動更新(push)が止まる。
predictions.ranking_json が全体の6割超を占めており、その多くは
同じレースに対する再予想だった。

安全性:
- 各レースの「最初の予想」は必ず残す。これが発走前にコミットされた
  AIの答えであり、実績評価(results_view)と決済(settle_original)は
  どちらも min(id) / order by id asc limit 1 しか読まない
- 未確定レースは一切触らない。今日の予想表示(forecast_view)は
  最新予想を使うため
- git履歴に全バージョンが残るので、必要なら過去コミットから復元できる

python scripts/prune_predictions.py --dry-run   # 何が消えるか確認
python scripts/prune_predictions.py --apply     # 実行
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TARGET_SQL = """
from predictions p
join races r on r.race_key = p.race_key
where r.result_json is not null
  and p.id not in (select min(id) from predictions group by race_key)
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Prune redundant predictions for settled races.")
    parser.add_argument("--apply", action="store_true", help="実際に削除する(既定は確認のみ)")
    args = parser.parse_args()

    from keirin_ai.storage import connect

    with connect() as conn:
        conn.row_factory = sqlite3.Row
        size_before = (
            conn.execute("pragma page_count").fetchone()[0] * conn.execute("pragma page_size").fetchone()[0]
        )
        rows, bytes_ = conn.execute(
            f"select count(*), coalesce(sum(length(p.ranking_json)),0) {TARGET_SQL}"
        ).fetchone()
        # 安全確認: 各レースの最初の予想が消えないこと
        risky = conn.execute(
            f"select count(*) {TARGET_SQL} and p.id in (select min(id) from predictions group by race_key)"
        ).fetchone()[0]

        report = {
            "delete_rows": rows,
            "free_mb": round(bytes_ / 1024 / 1024, 1),
            "size_before_mb": round(size_before / 1024 / 1024, 1),
            "first_predictions_at_risk": risky,
            "applied": False,
        }

        if risky:
            report["error"] = "最初の予想が削除対象に含まれています。中止しました。"
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return

        if args.apply and rows:
            before_total = conn.execute("select count(*) from predictions").fetchone()[0]
            before_races = conn.execute("select count(distinct race_key) from predictions").fetchone()[0]
            conn.execute(f"delete from predictions where id in (select p.id {TARGET_SQL})")
            conn.commit()
            after_total = conn.execute("select count(*) from predictions").fetchone()[0]
            after_races = conn.execute("select count(distinct race_key) from predictions").fetchone()[0]
            if after_races != before_races:
                report["error"] = f"レース数が変化しました({before_races}->{after_races})。要調査。"
                print(json.dumps(report, ensure_ascii=False, indent=1))
                return
            conn.execute("vacuum")
            size_after = (
                conn.execute("pragma page_count").fetchone()[0] * conn.execute("pragma page_size").fetchone()[0]
            )
            report.update(
                applied=True,
                rows_before=before_total,
                rows_after=after_total,
                races_kept=after_races,
                size_after_mb=round(size_after / 1024 / 1024, 1),
            )

    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
