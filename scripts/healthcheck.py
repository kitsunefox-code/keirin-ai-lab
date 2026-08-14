from __future__ import annotations

"""配信物が健全かを最後に確かめ、壊れていればジョブを失敗させる。

2026-08-11〜14、サイトの予想が0件のまま4日間配信され続けた。
Actionsは毎回「成功」と表示され、誰も気づけなかった。原因(save_venueの
バインド数不足)より、それが4日間見えなかったことのほうが問題だった。

ここが赤くなればGitHubから通知が飛ぶ。「更新が止まったのに気づかない」を
無くすのが目的なので、判定は配信されるJSONそのものに対して行う。

誤検知を避けるため、落とすのは「明らかに壊れている」場合だけにする:
  - 開催候補があるのに予想が0件      → 予想生成が壊れている
  - 生成時刻が古すぎる                → ジョブが回っていない/pushできていない
  - 対象日が今日でない                → 前日のまま止まっている
開催候補が0件(=そもそも今日レースが無い)のときは警告に留める。

python scripts/healthcheck.py [--max-age-hours 3]
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JST = timezone(timedelta(hours=9))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(ROOT / "app" / "static-api" / "today.json"))
    ap.add_argument("--max-age-hours", type=float, default=3.0)
    args = ap.parse_args()

    path = Path(args.file)
    problems: list[str] = []
    warnings: list[str] = []

    if not path.exists():
        print(f"::error::{path} が存在しない。配信物が作られていない")
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"::error::{path} が壊れていて読めない: {exc}")
        return 1

    forecasts = data.get("forecasts") or []
    source = data.get("source") or {}
    candidates = source.get("candidates") or []
    target_date = str(source.get("target_date") or "")
    today = datetime.now(JST).date().isoformat()

    # 1) 開催があるのに予想が0件
    if candidates and not forecasts:
        problems.append(
            f"開催候補が{len(candidates)}場あるのに予想が0件。予想生成が壊れている"
        )
    elif not candidates:
        warnings.append("開催候補が0場。今日はレースが無いか、開催一覧の取得に失敗している")

    # 2) 生成時刻が古い
    raw = str(data.get("generated_at") or "")
    if not raw:
        problems.append("generated_at が無い")
    else:
        try:
            gen = datetime.fromisoformat(raw)
            if gen.tzinfo is None:
                gen = gen.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - gen).total_seconds() / 3600
            if age > args.max_age_hours:
                problems.append(
                    f"生成が{age:.1f}時間前で古い(上限{args.max_age_hours}時間)。ジョブが回っていない"
                )
        except Exception:
            problems.append(f"generated_at を読めない: {raw!r}")

    # 3) 対象日がずれている
    if target_date and target_date != today:
        problems.append(f"対象日が {target_date} で今日({today})ではない。前日のまま止まっている")

    print(
        json.dumps(
            {
                "対象日": target_date,
                "今日(JST)": today,
                "開催候補": len(candidates),
                "予想": len(forecasts),
                "発走前": (data.get("summary") or {}).get("count"),
                "生成時刻": raw,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    for w in warnings:
        print(f"::warning::{w}")
    for p in problems:
        print(f"::error::{p}")

    if problems:
        print("\n配信物が健全でないためジョブを失敗させる。上のerrorを見ること。")
        return 1
    print("\n健全。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
