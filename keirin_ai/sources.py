from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from urllib.parse import urlparse
from urllib.request import Request, urlopen


ALLOWED_DOMAINS = {
    "www.winticket.jp",
    "winticket.jp",
    "keirin.jp",
    "www.keirin.jp",
    "keirin.netkeiba.com",
}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tokens: list[str] = []
        self._title = False
        self.title = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self._title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._title = False

    def handle_data(self, data: str) -> None:
        text = html.unescape(data)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            return
        if self._title:
            self.title += text
        self.tokens.append(text)


def fetch_url(url: str, timeout: int = 15) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"https", "http"}:
        raise ValueError("http/httpsのURLだけ取得できます。")
    if parsed.netloc not in ALLOWED_DOMAINS:
        raise ValueError("このプロトタイプの自動取得はWINTICKET公開ページだけに制限しています。")

    request = Request(
        url,
        headers={
            "User-Agent": "KeirinAILab/0.1 research prototype; low-frequency personal use",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def parse_winticket_racecard(html_text: str, url: str) -> dict:
    extractor = TextExtractor()
    extractor.feed(html_text)
    tokens = extractor.tokens
    flat = " ".join(tokens)

    title = extractor.title or _first_match(flat, r"([^\s]+競輪.+?出走表)")
    entrants = _parse_entrants(flat)
    lineup = _parse_lineup(tokens)
    result = _parse_result(tokens, entrants)
    meta = _parse_meta(title, flat, tokens)

    return {
        "source": {"name": "WINTICKET", "url": url, "fetched_as": "public-html"},
        "title": title or "WINTICKET 出走表",
        "venue": meta.get("venue"),
        "event": meta.get("event"),
        "race_no": meta.get("race_no"),
        "race_class": meta.get("race_class"),
        "date": meta.get("date"),
        "start_time": meta.get("start_time"),
        "deadline_time": meta.get("deadline_time"),
        "distance": meta.get("distance"),
        "weather": meta.get("weather"),
        "lineup": lineup,
        "result": result,
        "entrants": entrants,
        "raw_quality": {
            "token_count": len(tokens),
            "entrant_count": len(entrants),
            "lineup_count": len(lineup),
        },
    }


def _parse_meta(title: str, flat: str, tokens: list[str]) -> dict:
    meta: dict[str, str | int | None] = {}
    if title:
        venue_match = re.search(r"(.+?)競輪", title)
        race_match = re.search(r"(\d+)レース", title)
        event_match = re.search(r"競輪\s+(.+?)（", title)
        if venue_match:
            meta["venue"] = venue_match.group(1).strip()
        if race_match:
            meta["race_no"] = int(race_match.group(1))
        if event_match:
            meta["event"] = event_match.group(1).strip()

    date_match = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", flat)
    if date_match:
        meta["date"] = date_match.group(1)

    for idx, token in enumerate(tokens):
        if token == "#" and idx + 5 < len(tokens):
            if re.fullmatch(r"\d+\s*R", tokens[idx + 1]):
                meta["race_no"] = int(re.search(r"\d+", tokens[idx + 1]).group(0))
                meta["event"] = tokens[idx + 2]
                meta["race_class"] = tokens[idx + 4] if idx + 4 < len(tokens) else None
                break

    for idx, token in enumerate(tokens):
        if token == "発走" and idx + 1 < len(tokens) and re.fullmatch(r"\d{1,2}:\d{2}", tokens[idx + 1]):
            meta["start_time"] = tokens[idx + 1]
        if token == "締切" and idx + 1 < len(tokens) and re.fullmatch(r"\d{1,2}:\d{2}", tokens[idx + 1]):
            meta["deadline_time"] = tokens[idx + 1]
        if re.fullmatch(r"\d{1,2},?\d{3}m", token):
            meta["distance"] = token

    weather_match = re.search(r"\d{4}年\d{1,2}月\d{1,2}日\s+[^。]+?(\d+\.\d+℃[^ ]+\s+\d+\.\d+m/s)", flat)
    meta["weather"] = weather_match.group(1) if weather_match else None
    return meta


def _parse_entrants(flat: str) -> list[dict]:
    start = flat.find("枠 車 選手名 AI 競走得点")
    if start < 0:
        start = flat.find("枠 車 選手名")
    end_candidates = [idx for idx in [flat.find("並び予想", start), flat.find("勝ち上がり条件", start)] if idx > start]
    end = min(end_candidates) if end_candidates else len(flat)
    table = flat[start:end]

    pattern = re.compile(
        r"(?:(?P<frame>\d)\s+)?"
        r"(?P<car>\d)\s+"
        r"(?P<name>[一-龥ぁ-んァ-ヶー々]{2,12})\s+"
        r"(?P<pref>[一-龥]{2,4})\s+"
        r"(?P<class>[ALS]\d?)\s+"
        r"(?P<age>\d{2})歳\s+"
        r"(?P<term>\d{2,3})期\s+"
        r"(?:(?P<mark>本命|対抗|単穴|連下)\s+)?"
        r"(?P<score>\d{2}\.\d{2})\s+"
        r"(?P<s>\d+)\s+"
        r"(?P<h>\d+)\s+"
        r"(?P<b>\d+)\s+"
        r"(?P<style>[逃追両])\s+"
        r"(?P<escape>\d+)\s+"
        r"(?P<makuri>\d+)\s+"
        r"(?P<sashi>\d+)\s+"
        r"(?P<mark_count>\d+)\s+"
        r"(?P<first>\d+)\s+"
        r"(?P<second>\d+)\s+"
        r"(?P<third>\d+)\s+"
        r"(?P<outside>\d+)\s+"
        r"(?P<win_rate>\d+\.\d+)\s+"
        r"(?P<two_rate>\d+\.\d+)\s+"
        r"(?P<three_rate>\d+\.\d+)\s+"
        r"(?P<gear>\d\.\d{2})\s+"
        r"(?P<comment>.*?)(?=\s+(?:\d\s+)?\d\s+[一-龥ぁ-んァ-ヶー々]{2,12}\s+[一-龥]{2,4}\s+[ALS]\d?\s+\d{2}歳\s+\d{2,3}期|\s+並び予想|\s+結果|\s+勝ち上がり条件|$)"
    )

    entrants = []
    for match in pattern.finditer(table):
        data = match.groupdict()
        entrants.append(
            {
                "frame_no": int(data.get("frame") or data["car"]),
                "car_no": int(data["car"]),
                "name": data["name"],
                "prefecture": data["pref"],
                "class": data["class"],
                "age": int(data["age"]),
                "term": int(data["term"]),
                "ai_mark": data.get("mark") or "",
                "racing_score": float(data["score"]),
                "style": data["style"],
                "gear": data["gear"],
                "comment": _clean_comment(data.get("comment") or ""),
                "stats": {
                    "start_count": int(data["s"]),
                    "home_count": int(data["h"]),
                    "back_count": int(data["b"]),
                    "escape": int(data["escape"]),
                    "makuri": int(data["makuri"]),
                    "sashi": int(data["sashi"]),
                    "mark": int(data["mark_count"]),
                    "first": int(data["first"]),
                    "second": int(data["second"]),
                    "third": int(data["third"]),
                    "outside": int(data["outside"]),
                    "win_rate": float(data["win_rate"]),
                    "two_rate": float(data["two_rate"]),
                    "three_rate": float(data["three_rate"]),
                },
            }
        )
    return entrants


def _parse_lineup(tokens: list[str]) -> list[list[int]]:
    try:
        idx = tokens.index("並び予想")
    except ValueError:
        return []

    lines: list[list[int]] = []
    current: list[int] = []
    for token in tokens[idx + 1 : idx + 80]:
        if token in {"結果", "勝ち上がり条件", "オッズ一覧"}:
            break
        if token == "区切り":
            if current:
                lines.append(current)
                current = []
            continue
        if re.fullmatch(r"\d", token):
            current.append(int(token))
    if current:
        lines.append(current)
    return lines


def _clean_comment(comment: str) -> str:
    comment = re.sub(r"\s+", " ", comment).strip()
    comment = re.sub(r"\s*(結果|勝ち上がり条件).*$", "", comment).strip()
    return comment


def _parse_result(tokens: list[str], entrants: list[dict]) -> dict | None:
    if not entrants:
        return None

    result_starts = [idx for idx, token in enumerate(tokens) if token in {"結果", "レース結果"}]
    entrant_names = {entrant.get("name"): int(entrant.get("car_no") or 0) for entrant in entrants}
    entrant_names = {name: car for name, car in entrant_names.items() if name and car}

    for start in reversed(result_starts):
        after = tokens[start + 1 : start + 10]
        if any(token in after for token in {"基本情報", "直近成績", "前検コメ", "対戦成績", "キャンペーン"}):
            continue
        block_tokens = []
        for token in tokens[start + 1 : start + 260]:
            if token in {"勝ち上がり条件", "オッズ一覧", "レース一覧", "出走表"}:
                break
            block_tokens.append(token)
        order = _order_from_result_block(block_tokens, entrant_names)
        if len(order) >= 2:
            return {
                "finish_order": order,
                "positions": {str(car): pos for pos, car in enumerate(order, start=1)},
                "source": "parsed",
            }
    return None


def _order_from_result_block(tokens: list[str], entrant_names: dict[str, int]) -> list[int]:
    found: list[tuple[int, int]] = []
    for idx, token in enumerate(tokens):
        car_no = entrant_names.get(token)
        if not car_no:
            continue
        finish = _nearest_finish_position(tokens, idx)
        if finish and all(car != car_no for _, car in found):
            found.append((finish, car_no))

    if found:
        found.sort(key=lambda item: item[0])
        return [car for _, car in found]
    return []


def _nearest_finish_position(tokens: list[str], idx: int) -> int | None:
    for offset in range(1, 7):
        pos = idx - offset
        if pos < 0:
            break
        token = tokens[pos]
        match = re.fullmatch(r"([1-9])(?:着)?", token)
        if match:
            return int(match.group(1))
    return None


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""
