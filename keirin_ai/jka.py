from __future__ import annotations

"""JKA公式(keirin.jp)の選手プロフィールから公式成績を取り込む。

https://keirin.jp/pc/racerprofile?snum=登録番号 は静的HTMLで以下を含む:
- 期別 / 現在の級班 / 次期級班(昇級・降級の予定) / 脚質 / 今期得点(適用中の競走得点)
- 近況成績: 直近4ヶ月の着別回数・勝率・連対率・競走得点
- 級班の履歴情報: 数年分の昇降級の記録(公式の長期トレンド)
- 通算成績

※月別の得点遷移グラフはXHR描画のため取得対象外(必要データは上記で足りる)。
"""

import re

from keirin_ai.sources import fetch_url

PROFILE_URL = "https://keirin.jp/pc/racerprofile?snum={player_id}"

# 級班の強さ順(小さいほど上位)。昇級/降級の判定に使う。
CLASS_RANK = {
    "Ｓ級Ｓ班": 0,
    "Ｓ級１班": 1,
    "Ｓ級２班": 2,
    "Ａ級１班": 3,
    "Ａ級２班": 4,
    "Ａ級３班": 5,
    "Ｌ級１班": 3,  # ガールズは1班のみ(比較対象が無いので中位扱い)
}


def fetch_player_profile(player_id: str) -> dict | None:
    """公式プロフィールを取得してパースする。取れなければNone(捏造しない)。"""
    html = fetch_url(PROFILE_URL.format(player_id=player_id))
    flat = re.sub(r"\s+", " ", html)
    profile = _parse_profile(flat)
    return profile


def _parse_profile(flat: str) -> dict | None:
    out: dict = {}

    # --- 期別/級班/次期級班/脚質/今期得点 (ヘッダ行に続くデータ行) ---
    m = re.search(
        r"期別</td>.*?今期得点</td>\s*</tr>\s*<tr>\s*"
        r'<td class="al-c">(\d+)期</td>\s*'
        r'<td class="al-c">([^<]+)</td>\s*'
        r'<td class="al-c">[^<]*</td>\s*'
        r'<td class="al-c">([^<]*)</td>\s*'
        r'<td class="al-c">([^<]*)</td>\s*'
        r'<td class="al-c">(?:<a[^>]*>)?([\d.]+)',
        flat,
    )
    if m:
        out["term"] = int(m.group(1))
        out["class_now"] = m.group(2).strip()
        out["class_next"] = m.group(3).strip()
        out["style"] = m.group(4).strip()
        out["score_now"] = float(m.group(5))

    # --- 級班の履歴情報 (級班, 年月日) ---
    hist = []
    hm = re.search(r"級班の履歴情報.*?<tbody[^>]*>(.*?)</tbody>", flat)
    if hm:
        for row in re.findall(r"<tr>(.*?)</tr>", hm.group(1)):
            cells = re.findall(r'<td class="al-c">\s*([^<]+?)\s*<', row)
            if len(cells) >= 2 and re.match(r"\d{4}/\d{2}/\d{2}", cells[1]):
                hist.append({"class": cells[0].strip(), "date": cells[1].strip()})
    out["class_history"] = hist

    # --- 近況成績(直近4ヶ月)。「通算成績」アンカーより前の成績テーブル ---
    total_anchor = flat.find('id="total"')
    recent_seg = flat[: total_anchor if total_anchor != -1 else len(flat)]
    recent = _parse_seiseki_row(recent_seg)
    if recent:
        out["recent"] = recent

    # --- 通算成績 ---
    if total_anchor != -1:
        total = _parse_seiseki_row(flat[total_anchor:])
        if total:
            out["total"] = total

    return out if out.get("score_now") is not None or out.get("recent") else None


def _parse_seiseki_row(seg: str) -> dict | None:
    """1着/2着/3着/着外/…/競走得点 のヘッダに続くデータ行をパースする。"""
    m = re.search(
        r"1着</td>.*?競走得点</td>\s*</tr>\s*<tr>(.*?)</tr>",
        seg,
    )
    if not m:
        return None
    cells = re.findall(r"<p>\s*([^<]+?)\s*</p>", m.group(1))
    if len(cells) < 13:
        return None

    def _num(text: str) -> float:
        t = str(text).replace("回", "").replace("%", "").strip()
        try:
            return float(t)
        except ValueError:
            return 0.0

    return {
        "first": int(_num(cells[0])),
        "second": int(_num(cells[1])),
        "third": int(_num(cells[2])),
        "out": int(_num(cells[3])),
        "starts": int(_num(cells[6])),
        "win_rate": _num(cells[7]),
        "two_rate": _num(cells[8]),
        "three_rate": _num(cells[9]),
        "score": _num(cells[12]),
    }


def class_move(class_now: str, class_next: str) -> str | None:
    """次期級班から昇級/降級を判定する。'-'や同一なら None。"""
    now = CLASS_RANK.get(str(class_now or "").strip())
    nxt = CLASS_RANK.get(str(class_next or "").strip())
    if now is None or nxt is None or now == nxt:
        return None
    return "up" if nxt < now else "down"
