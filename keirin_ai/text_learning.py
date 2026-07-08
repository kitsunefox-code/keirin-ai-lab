from __future__ import annotations

import hashlib
import re
from html.parser import HTMLParser
from urllib.parse import urlparse


ALLOWED_TEXT_DOMAINS = {
    "keirin.jp",
    "www.keirin.jp",
    "keirin.netkeiba.com",
    "www.winticket.jp",
    "winticket.jp",
}


RULES = {
    "injury": ["ケガ", "怪我", "落車", "骨折", "復帰", "欠場明け"],
    "penalty": ["失格", "斡旋停止", "違反", "制裁"],
    "fatigue": ["疲れ", "疲労", "連戦", "中0", "重い"],
    "rebound": ["復調", "上向き", "戻って", "良くな", "修正"],
    "confidence": ["自信", "手応え", "余裕", "仕上が", "好調"],
    "regret": ["悔し", "反省", "ミス", "甘さ", "迷い"],
    "local": ["地元", "ホーム", "声援", "応援"],
    "line_relation": ["ライン", "番手", "連係", "同県", "先輩", "後輩", "師匠", "弟子"],
    "aggressive": ["先行", "自力", "前々", "積極", "ブン駆け", "カマシ"],
    "veteran": ["ベテラン", "経験", "巧者", "職人"],
    "rookie": ["新人", "デビュー", "129期", "ルーキー", "本デビュー"],
    "girls": ["ガールズ", "女子", "女王"],
}


class PlainTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = False
        self.title = ""
        self._in_title = False
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip = True
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self._skip = False
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._in_title:
            self.title += text
        self.parts.append(text)


def extract_text_features(html_text: str, url: str, kind: str = "article") -> dict:
    domain = urlparse(url).netloc
    if domain not in ALLOWED_TEXT_DOMAINS:
        raise ValueError(f"unsupported domain: {domain}")

    parser = PlainTextParser()
    parser.feed(html_text)
    text = normalize_text(" ".join(parser.parts))
    tags = score_tags(text)
    return {
        "url": url,
        "domain": domain,
        "kind": kind,
        "title": normalize_text(parser.title)[:180],
        "fingerprint": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "excerpt": make_excerpt(text),
        "tags": tags,
        "signal_score": round(sum(item["score"] for item in tags.values()), 3),
        "word_count": len(text),
    }


def is_learnable_document(doc: dict) -> tuple[bool, str]:
    url = doc.get("url") or ""
    title = doc.get("title") or ""
    word_count = int(doc.get("word_count") or 0)
    if "_rss." in url:
        return False, "rss feed placeholder"
    if not title and word_count < 200:
        return False, "empty page"
    if "404 Not Found" in title:
        return False, "404 page"
    if word_count < 120:
        return False, "too little text"
    return True, "ok"


def score_tags(text: str) -> dict[str, dict]:
    tags = {}
    for tag, phrases in RULES.items():
        hits = [phrase for phrase in phrases if phrase in text]
        if hits:
            tags[tag] = {
                "score": min(3.0, 0.7 + 0.35 * len(hits)),
                "hits": hits[:6],
            }
    return tags


def make_excerpt(text: str, limit: int = 120) -> str:
    text = normalize_text(text)
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()
