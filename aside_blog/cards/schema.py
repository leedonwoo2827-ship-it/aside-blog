"""학습카드 JSON 스키마 (textbook-study-cards 스킬 §2) + 받은 뒤 다듬기."""
from __future__ import annotations

import re
from typing import Any, Dict, List

_S = {"type": "string"}
_SL = {"type": "array", "items": _S}

CARD: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": _S, "title": _S, "subtitle": _S, "intro": _S,
        "sections": {"type": "array", "items": {"type": "object", "properties": {"heading": _S, "points": _SL},
                                                "required": ["heading", "points"]}},
        "tables": {"type": "array", "items": {"type": "object",
                                              "properties": {"title": _S, "headers": _SL,
                                                             "rows": {"type": "array", "items": _SL}, "caption": _S},
                                              "required": ["title", "headers", "rows"]}},
        "terms": {"type": "array", "items": {"type": "object", "properties": {"term": _S, "desc": _S},
                                             "required": ["term", "desc"]}},
        "exam_points": _SL,
        "quiz": {"type": "array", "items": {"type": "object", "properties": {"q": _S, "o": {"type": "boolean"}, "a": _S},
                                            "required": ["q", "o", "a"]}},
        "one_line": _S, "tags": _SL,
    },
    "required": ["id", "title", "subtitle", "intro", "sections", "terms", "exam_points", "quiz", "one_line", "tags"],
}
BATCH_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {"cards": {"type": "array", "items": CARD}},
                                "required": ["cards"]}

_TAG = re.compile(r"<[^>]+>")


def _s(x: Any) -> str:
    return _TAG.sub("", str(x or "")).strip()


def normalize(d: Dict[str, Any]) -> Dict[str, Any]:
    """빈 칸 걷기·개수 상한·HTML 태그 제거. 스킬의 분량 규칙(섹션 2~5 × 3~6, 용어 3~6 …)을 넘치면 자른다."""
    out: Dict[str, Any] = {"id": _s(d.get("id")), "title": _s(d.get("title")), "subtitle": _s(d.get("subtitle")),
                           "intro": _s(d.get("intro"))}
    out["sections"] = [{"heading": _s(s.get("heading")), "points": [_s(p) for p in s.get("points") or [] if _s(p)][:6]}
                       for s in d.get("sections") or [] if _s(s.get("heading")) and s.get("points")][:5]
    tables = []
    for t in d.get("tables") or []:
        rows = [[_s(c) for c in r] for r in t.get("rows") or [] if any(_s(c) for c in r)]
        if t.get("headers") and rows:
            tt = {"title": _s(t.get("title")) or "한눈에 비교", "headers": [_s(h) for h in t["headers"]], "rows": rows}
            if _s(t.get("caption")):
                tt["caption"] = _s(t["caption"])
            tables.append(tt)
    if tables:
        out["tables"] = tables[:2]
    terms = [{"term": _s(t.get("term")), "desc": _s(t.get("desc"))} for t in d.get("terms") or [] if _s(t.get("term"))]
    if terms:
        out["terms"] = terms[:6]
    ex = [_s(x) for x in d.get("exam_points") or [] if _s(x)]
    if ex:
        out["exam_points"] = ex[:5]
    quiz = [{"q": re.sub(r"^Q\.\s*", "", _s(q.get("q"))), "o": bool(q.get("o")), "a": _s(q.get("a"))}
            for q in d.get("quiz") or [] if _s(q.get("q"))]
    if quiz:
        out["quiz"] = quiz[:3]
    out["one_line"] = _s(d.get("one_line"))
    out["tags"] = [re.sub(r"[\s#]+", "", _s(t)) for t in d.get("tags") or [] if _s(t)][:8]
    for k in ("_chunk_hash", "_made_at", "_source"):
        if d.get(k):
            out[k] = d[k]
    return out


def problems(d: Dict[str, Any]) -> List[str]:
    p = []
    if not d.get("title"):
        p.append("제목 없음")
    if len(d.get("sections") or []) < 1:
        p.append("본문 섹션 없음")
    if not d.get("one_line"):
        p.append("한 줄 정리 없음")
    return p
