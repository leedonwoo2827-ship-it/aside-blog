"""s1-post — 학습카드 한 장 → 네이버 블로그 글 하나 (Codex 텍스트 콜, 할당량).

Codex 는 제목·도입·섹션 문단·마무리·태그·그림 장면만 쓴다. 표·핵심 용어·시험 포인트·OX·한 줄 정리는
**코드가 카드에서 그대로** 붙인다(_assemble) — 숫자를 지어낼 틈을 없앤다.

글(posts/<id>.json) = {data_id, title, blocks[], tags[], category, cover_scene, section_scenes[], body_hash, made_at}
blocks 의 type: image(slot) · heading · paragraph · list · quote · small
  image slot: cover | sec1 | sec2 (Codex 그림 → 없으면 cover 만 디자인 표지) · table-N · terms · quiz (카드에서 찍은 그림)
캐시가 곧 이어하기다: 카드 내용·설정이 그대로면 건너뛴다.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from . import config
from .job import Card, Job
from .log import detail, log, usage

PROMPT = Path(__file__).parent / "prompts" / "blog_post.md"

_S = {"type": "string"}
_SL = {"type": "array", "items": _S}
SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "data_id": _S, "title": _S, "lead": _S,
            "sections": {"type": "array", "items": {"type": "object",
                                                    "properties": {"heading": _S, "text": _S, "bullets": _SL},
                                                    "required": ["heading", "text", "bullets"]}},
            "closing": _S, "tags": _SL, "cover_scene": _S, "section_scenes": _SL,
        },
        "required": ["data_id", "title", "lead", "sections", "closing", "tags", "cover_scene", "section_scenes"],
    }}},
    "required": ["items"],
}


def card_hash(c: Card) -> str:
    pc = config.load()["post"]
    body = {k: v for k, v in c.data.items() if not k.startswith("_")}
    modes = [pc.get(k) for k in ("table_mode", "terms_mode", "quiz_mode", "section_images", "source_footer")]
    return hashlib.sha1(json.dumps([body, modes], ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]


def _brief(job: Job, batch: List[Card]) -> str:
    head = {"book": job.setting("book"), "series": job.setting("series"), "category": job.category}
    cards = [{k: v for k, v in c.data.items() if not k.startswith("_")} | {"crumb": c.crumb} for c in batch]
    return ("## 연재\n" + json.dumps(head, ensure_ascii=False) +
            "\n\n## 학습카드\n" + json.dumps(cards, ensure_ascii=False, indent=1))


def clean_tags(xs: List[str], first: List[str], limit: int) -> List[str]:
    out: List[str] = []
    for t in [*first, *(xs or [])]:
        t = re.sub(r"[\s#,]+", "", str(t))
        if t and t not in out and len(t) <= 30:
            out.append(t)
    return out[:limit]


def assemble(job: Job, c: Card, item: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Codex 글 + 카드 → 블록 목록 (위에서 아래로 에디터에 들어가는 순서)."""
    pc = config.load()["post"]
    d = c.data
    B: List[Dict[str, Any]] = [{"type": "image", "slot": "cover"}]
    if item.get("lead"):
        B.append({"type": "paragraph", "text": item["lead"]})
    secs = item.get("sections") or []
    for i, sec in enumerate(d.get("sections") or []):
        mine = secs[i] if i < len(secs) else {}
        B.append({"type": "heading", "text": mine.get("heading") or sec["heading"]})
        if pc.get("section_images", True) and i < 2:
            B.append({"type": "image", "slot": f"sec{i + 1}", "optional": True})
        if mine.get("text"):
            B.append({"type": "paragraph", "text": mine["text"]})
        B.append({"type": "list", "items": mine.get("bullets") or sec.get("points") or []})
    for k, t in enumerate(d.get("tables") or [], 1):
        B.append({"type": "heading", "text": t.get("title") or "한눈에 비교"})
        if pc.get("table_mode", "image") in ("image", "both"):
            B.append({"type": "image", "slot": f"table-{k}"})
        if pc.get("table_mode") in ("text", "both"):
            hs = t.get("headers") or []
            B.append({"type": "list", "items": [
                f"**{r[0]}** — " + " / ".join(f"{hs[j]}: {x}" if j < len(hs) else x for j, x in enumerate(r[1:], 1))
                for r in t.get("rows") or [] if r]})
        if t.get("caption"):
            B.append({"type": "small", "text": t["caption"]})
    if d.get("terms"):
        B.append({"type": "heading", "text": "핵심 용어"})
        if pc.get("terms_mode", "text") == "image":
            B.append({"type": "image", "slot": "terms"})
        else:
            B.append({"type": "list", "items": [f"**{t['term']}** — {t['desc']}" for t in d["terms"]]})
    if d.get("exam_points"):
        B.append({"type": "heading", "text": "시험 포인트"})
        B.append({"type": "list", "items": [f"★ {x}" for x in d["exam_points"]], "plain": True})
    if d.get("quiz"):
        B.append({"type": "heading", "text": "OX 셀프체크"})
        B.append({"type": "list", "items": [f"Q{k}. {q['q']}" for k, q in enumerate(d["quiz"], 1)], "plain": True})
        mode = pc.get("quiz_mode", "image")
        if mode in ("image", "both"):
            B.append({"type": "paragraph", "text": "정답은 아래 그림에서 확인해 보세요."})
            B.append({"type": "image", "slot": "quiz"})
        if mode in ("text", "both"):
            B.append({"type": "list", "plain": True, "items": [
                f"A{k}. {'O' if q.get('o') else 'X'} — {q['a']}" for k, q in enumerate(d["quiz"], 1)]})
    if d.get("one_line"):
        B.append({"type": "quote", "text": d["one_line"]})
    if item.get("closing"):
        B.append({"type": "paragraph", "text": item["closing"]})
    if pc.get("source_footer", True) and job.setting("source"):
        B.append({"type": "small", "text": job.setting("source")})
    return B


def make_post(job: Job, c: Card, item: Dict[str, Any]) -> Dict[str, Any]:
    pc = config.load()["post"]
    title = re.sub(r"\s+", " ", item.get("title") or c.title).strip()
    if len(title) > int(pc.get("title_max", 60)):
        log(f"  ⚠ 「{c.title}」 제목이 길어서 줄였어요. 결과 보기에서 다듬어 주세요.")
        title = title[:int(pc.get("title_max", 60))].rstrip()
    first = [t for t in (c.data.get("tags") or [])[:3]]
    return {
        "data_id": c.id, "n": c.n, "title": title,
        "blocks": assemble(job, c, item),
        "tags": clean_tags(item.get("tags") or [], first, int(pc.get("tags_max", 30))),
        "category": job.category,
        "cover_scene": item.get("cover_scene", ""), "section_scenes": (item.get("section_scenes") or [])[:2],
        "body_hash": card_hash(c), "made_at": time.strftime("%Y-%m-%d %H:%M"),
    }


def run(job: Job, only=None, force: bool = False, **_) -> None:
    from .llm import codex_transport, models
    from .llm.codex_provider import CodexProvider

    cfg = config.load()
    todo = []
    for c in job.pick(only):
        old = job.raw_post(c.id)
        if not force and old and old.get("body_hash") == card_hash(c):
            continue
        todo.append(c)
    if not todo:
        log("블로그 글은 이미 다 써 두었어요.")
        return
    batch_n = max(1, int(cfg["post"].get("batch", 5)))
    log(f"블로그 글 {len(todo)}개를 쓰는 중이에요 ({batch_n}개씩, 한 번에 1~2분) …")
    model = models.resolve()
    models.apply(model)
    p = CodexProvider(model=model, effort=cfg["effort"].get("post", "medium"),
                      on_activity=lambda t: detail(f"    … {t}"))
    system = PROMPT.read_text(encoding="utf-8")
    for i in range(0, len(todo), batch_n):
        batch = todo[i:i + batch_n]
        detail(f"  [{i // batch_n + 1}/{(len(todo) + batch_n - 1) // batch_n}] {batch[0].id} … {batch[-1].id} · {model}")
        raw = p.structured(system, [{"role": "user", "content": _brief(job, batch)}], schema=SCHEMA)
        got = {it.get("data_id"): it for it in raw.get("items", [])}
        for c in batch:
            item = got.get(c.id)
            if not item:
                log(f"  ⚠ 「{c.title}」 글을 받지 못했어요 — 한 번 더 누르면 이것만 다시 써요")
                continue
            config.write_json(job.posts / f"{c.id}.json", make_post(job, c, item))
            log(f"  ✓ #{c.n:02d} {item.get('title') or c.title}")
        if usage():
            log(usage())
    detail(codex_transport.limits_line())


def offline(job: Job, only=None, force: bool = False, **_) -> None:
    """Codex 없이 카드만으로 글을 조립한다(로그인 전 미리보기·시험용). 문단은 카드 intro·포인트 그대로."""
    n = 0
    for c in job.pick(only):
        if not force and job.raw_post(c.id):
            continue
        d = c.data
        item = {"title": f"{c.title} — {d.get('subtitle', '')}".strip(" —"), "lead": d.get("intro", ""),
                "sections": [{"heading": s["heading"], "text": "", "bullets": s.get("points") or []}
                             for s in d.get("sections") or []],
                "closing": "", "tags": d.get("tags") or [], "cover_scene": "", "section_scenes": []}
        config.write_json(job.posts / f"{c.id}.json", make_post(job, c, item))
        n += 1
    log(f"카드 그대로 글 {n}개를 조립했어요 (Codex 없이).")
