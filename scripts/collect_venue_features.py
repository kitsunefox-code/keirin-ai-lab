from __future__ import annotations

"""競輪場のバンク特徴を要約してDBに記憶する。

WINTICKETの出走表を1本取得すると全競輪場のバンク解説(bankFeature)が付いてくるので、
それをvenuesテーブルへ蓄積済み(save_venue)。本スクリプトはその解説文から
「逃げ有利/差し有利・カマシ・直線・風・カント」等のクセを抽出し、
短い傾向メモ(net_notes)として各競輪場に1度だけ記録する。

python scripts\\collect_venue_features.py            # 未記録の競輪場だけ要約
python scripts\\collect_venue_features.py --refetch  # 出走表を1本取得して最新バンクデータを補充してから要約
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from keirin_ai.sources import fetch_url, parse_winticket_racecard
from keirin_ai.storage import all_venues, connect, save_venue, save_venue_net_notes, venue_ids_missing_net_notes
from keirin_ai.winticket_state import enrich_race_from_state


# バンク解説文から拾う傾向キーワード → 短いタグ
TENDENCY_RULES = [
    (("逃げ切り", "逃げが決ま", "先行有利", "逃げ有利"), "逃げ残りやすい"),
    (("差し", "追い込み", "追込", "強襲"), "差し・追込が届く"),
    (("捲り", "まくり"), "捲りが決まりやすい"),
    (("カマシ", "かまし"), "カマシ注意"),
    (("直線が長", "直線は長"), "直線長め"),
    (("直線が短", "直線は短"), "直線短め"),
    (("カントがきつ", "カントが急", "急カント"), "急カント"),
    (("カントが浅", "カントが緩", "皿状"), "浅カント"),
    (("風", "海風", "向かい風", "追い風"), "風の影響あり"),
    (("競り", "内締め", "位置取り"), "位置争い激しい"),
    (("有利不利は", "クセのない", "標準的"), "標準的バンク"),
]


def summarize_bank_feature(text: str, kimarite: dict | None, track_distance, straight, bias) -> str:
    """バンク解説文＋決まり手分布から短い傾向メモを作る。"""
    tags: list[str] = []
    for keywords, tag in TENDENCY_RULES:
        if any(k in text for k in keywords) and tag not in tags:
            tags.append(tag)
    parts: list[str] = []
    if track_distance:
        parts.append(f"周長{track_distance}m")
    if straight:
        parts.append(f"みなし直線{straight}m")
    if kimarite:
        parts.append(f"決まり手 逃{kimarite.get('逃げ',0)*100:.0f}/捲{kimarite.get('捲り',0)*100:.0f}/差{kimarite.get('差し',0)*100:.0f}")
    if bias is not None:
        if bias > 0.15:
            parts.append("総合: 逃げ・先行有利")
        elif bias < -0.15:
            parts.append("総合: 差し・追込有利")
        else:
            parts.append("総合: 脚質の有利不利は小さい")
    if tags:
        parts.append("クセ: " + "、".join(tags[:5]))
    return " / ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize each velodrome's bank characteristics into notes.")
    parser.add_argument("--refetch", default="", help="このWINTICKET出走表URLを取得して全場のバンクデータを補充する")
    parser.add_argument("--all", action="store_true", help="記録済みも含め全競輪場を再要約する")
    args = parser.parse_args()

    with connect() as conn:
        if args.refetch:
            html = fetch_url(args.refetch)
            race = parse_winticket_racecard(html, args.refetch)
            race = enrich_race_from_state(race, html)
            for venue in race.get("all_venues") or []:
                save_venue(conn, venue)

        venues = all_venues(conn)
        target_ids = None if args.all else set(venue_ids_missing_net_notes(conn))
        summarized = []
        for venue in venues:
            if target_ids is not None and venue["venue_id"] not in target_ids:
                continue
            notes = summarize_bank_feature(
                venue.get("bank_feature") or "",
                venue.get("kimarite"),
                venue.get("track_distance"),
                venue.get("straight"),
                venue.get("bank_bias"),
            )
            if notes:
                save_venue_net_notes(conn, venue["venue_id"], notes)
                summarized.append({"name": venue["name"], "notes": notes})

    print(json.dumps({"summarized_count": len(summarized), "venues": summarized}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
