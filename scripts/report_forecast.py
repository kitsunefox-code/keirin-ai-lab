from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a compact Markdown betting research report.")
    parser.add_argument("path", nargs="?", default="data/today_after_1500_forecast.json")
    parser.add_argument("--out", default="data/today_after_1500_report.md")
    args = parser.parse_args()

    data = json.loads(Path(args.path).read_text(encoding="utf-8"))
    forecasts = data.get("forecasts", [])
    strong = [race for race in forecasts if top_prob(race) >= 0.65]
    medium = [race for race in forecasts if 0.45 <= top_prob(race) < 0.65]
    wide = [race for race in forecasts if top_prob(race) < 0.45]

    lines = [
        f"# {data.get('target_date')} {data.get('after')}以降 競輪AI予想",
        "",
        "研究用の暫定予想です。的中や利益を保証するものではありません。",
        "",
        f"- 対象: {len(forecasts)}R",
        f"- 軸強め: {len(strong)}R",
        f"- 中位: {len(medium)}R",
        f"- 混戦: {len(wide)}R",
        "",
        "## 軸強め",
        "",
    ]
    lines.extend(format_rows(strong))
    lines.extend(["", "## 中位", ""])
    lines.extend(format_rows(medium))
    lines.extend(["", "## 混戦", ""])
    lines.extend(format_rows(wide))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out)


def format_rows(races: list[dict]) -> list[str]:
    if not races:
        return ["該当なし"]
    rows = []
    for race in races:
        top3 = " / ".join(
            f"{runner.get('car_no')}{runner.get('name')}({int((runner.get('probability') or 0) * 100)}%)"
            for runner in race.get("top3", [])
            if runner
        )
        tickets = ", ".join(race.get("tickets", [])[:3])
        rows.append(f"- {race.get('start_time')} {race.get('venue')} {race.get('race_no')}R: {top3} | {tickets}")
    return rows


def top_prob(race: dict) -> float:
    top3 = race.get("top3", [])
    if not top3:
        return 0.0
    return float(top3[0].get("probability") or 0.0)


if __name__ == "__main__":
    main()
