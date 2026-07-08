from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PhraseRule:
    phrase: str
    weight: float
    bucket: str


RULES: tuple[PhraseRule, ...] = (
    PhraseRule("状態はいい", 1.5, "condition"),
    PhraseRule("上向き", 1.2, "condition"),
    PhraseRule("仕上が", 1.1, "condition"),
    PhraseRule("余裕", 0.9, "condition"),
    PhraseRule("手応え", 0.9, "condition"),
    PhraseRule("自信", 1.0, "mental"),
    PhraseRule("意地", 0.8, "mental"),
    PhraseRule("集中", 0.8, "mental"),
    PhraseRule("一生懸命", 0.6, "mental"),
    PhraseRule("修正", 0.6, "recovery"),
    PhraseRule("積極", 0.8, "intent"),
    PhraseRule("前前", 0.7, "intent"),
    PhraseRule("力を出し切", 0.8, "intent"),
    PhraseRule("自力", 0.4, "intent"),
    PhraseRule("先行", 0.5, "intent"),
    PhraseRule("カマシ", 0.4, "intent"),
    PhraseRule("落車", -1.3, "risk"),
    PhraseRule("ケガ", -1.2, "risk"),
    PhraseRule("失格", -1.0, "risk"),
    PhraseRule("不安", -1.0, "mental"),
    PhraseRule("重い", -0.7, "condition"),
    PhraseRule("疲れ", -0.8, "condition"),
    PhraseRule("脚の余裕がなかった", -1.0, "condition"),
    PhraseRule("ダメ", -0.9, "condition"),
    PhraseRule("離れ", -0.8, "risk"),
    PhraseRule("ミス", -0.8, "risk"),
    PhraseRule("甘さ", -0.6, "mental"),
    PhraseRule("課題", -0.4, "recovery"),
    PhraseRule("迷", -0.6, "mental"),
    PhraseRule("中0", -0.5, "condition"),
)


def analyze_comment(comment: str | None) -> dict:
    text = (comment or "").strip()
    if not text:
        return {
            "tone": "材料なし",
            "score": 0.0,
            "buckets": {},
            "hits": [],
            "summary": "コメント材料がありません。",
        }

    score = 0.0
    hits: list[dict] = []
    buckets: dict[str, float] = {}
    for rule in RULES:
        if rule.phrase in text:
            score += rule.weight
            buckets[rule.bucket] = round(buckets.get(rule.bucket, 0.0) + rule.weight, 2)
            hits.append({"phrase": rule.phrase, "weight": rule.weight, "bucket": rule.bucket})

    score = round(max(-3.0, min(3.0, score)), 2)
    tone = _tone(score, buckets)
    return {
        "tone": tone,
        "score": score,
        "buckets": buckets,
        "hits": hits[:8],
        "summary": _summary(tone, score, buckets),
    }


def _tone(score: float, buckets: dict[str, float]) -> str:
    if score >= 1.4:
        return "強気"
    if score >= 0.45:
        return "上向き"
    if score <= -1.2:
        return "不安大"
    if score <= -0.35:
        return "不安含み"
    if buckets.get("intent", 0) >= 0.8:
        return "積極策"
    return "中立"


def _summary(tone: str, score: float, buckets: dict[str, float]) -> str:
    if tone in {"強気", "上向き"}:
        return "状態面か意欲面のプラス材料があります。"
    if tone in {"不安大", "不安含み"}:
        return "落車、疲れ、迷いなどのリスク材料があります。"
    if buckets.get("intent", 0) > 0:
        return "戦法意図は見えますが、状態評価は控えめです。"
    return "コメントだけでは強弱を決めにくいです。"
