"""s0-cards — 학습카드 만들기.

두 길:
  pdf    교재 조각(source/chunks) → Codex 가 카드 JSON 을 쓴다 (할당량). 스킬의 「서브에이전트 병렬」 대신
         조각 몇 개씩 묶어 Codex 에 순서대로 보낸다. 조각 글이 그대로면 건너뛴다 = 이어하기.
  cards  이미 만든 학습카드 HTML 폴더(예: _context/변액보험_학습카드) → 그대로 읽어 온다 (LLM 없음).
끝나면 언제나 cards/html 을 다시 굽는다(render).
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Dict, List

from . import config
from .cards import parse_html, render
from .cards.schema import BATCH_SCHEMA, normalize, problems
from .job import Job, base_id
from .log import detail, log, usage

PROMPT = Path(__file__).parent / "prompts" / "card_json.md"


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def import_folder(job: Job, folder: Path) -> int:
    """카드 폴더 → cards/json + source/manifest.json, 책 이름·연재 문구·출처를 job.json 에."""
    got = parse_html.parse_folder(folder)
    job.card_json.mkdir(parents=True, exist_ok=True)
    for old in job.card_json.glob("*.json"):
        old.unlink()
    stamp = time.strftime("%Y-%m-%d %H:%M")
    for d in got["cards"]:
        d.update({"_source": "html", "_made_at": stamp})
        config.write_json(job.card_json / f"{d['id']}.json", d)
    config.write_json(job.source / "manifest.json", got["manifest"])
    meta = config.read_json(job.dir / "job.json", {}) or {}
    for k in ("book", "series", "source"):
        if got.get(k) and not meta.get(k):
            meta[k] = got[k]
    config.write_json(job.dir / "job.json", meta)
    log(f"학습카드 {len(got['cards'])}장을 불러왔어요.")
    return len(got["cards"])


def _batches(chunks: List[Dict[str, Any]], size: int, max_lines: int) -> List[List[Dict[str, Any]]]:
    out: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    lines = 0
    for c in chunks:
        if cur and (len(cur) >= size or lines + c["lines"] > max_lines):
            out.append(cur)
            cur, lines = [], 0
        cur.append(c)
        lines += c["lines"]
    if cur:
        out.append(cur)
    return out


def _brief(job: Job, batch: List[Dict[str, Any]], split_lines: int) -> str:
    parts = [f"## 책\n{job.setting('book')} — 출처: {job.setting('source')}\n"]
    for c in batch:
        hint = (f" — {c['lines']}줄로 길다. 내용별로 2~5장으로 나누고 id 뒤에 a,b,c… 를 붙여라"
                if c["lines"] >= split_lines else "")
        parts.append(f"## 조각 {c['id']}{hint}\n메타: 제{c['chapter']}장 {c['chapter_name']} › {c['sec_no']}. "
                     f"{c['sec_name']} › {c.get('sub_name') or '(절 본문)'}\n```\n{c['text']}\n```")
    return "\n\n".join(parts)


def from_pdf(job: Job, only=None, force: bool = False, **_) -> None:
    from .llm import codex_transport, models
    from .llm.codex_provider import CodexProvider

    cfg = config.load()
    ccfg = cfg["cards"]
    man = job.manifest
    if not man:
        raise SystemExit("교재 조각이 없어요 — 먼저 「교재에서 글 뽑기」를 눌러 주세요")
    have: Dict[str, List[Dict[str, Any]]] = {}
    for f in job.card_json.glob("*.json") if job.card_json.exists() else []:
        d = config.read_json(f) or {}
        have.setdefault(base_id(d.get("id", "")), []).append(d)
    want = set(only or [])
    skip = set(job.get("skip_chunks") or [])
    todo = []
    for m in man:
        if m["id"] in skip and m["id"] not in want:
            continue
        if want and m["id"] not in want and not any(x.startswith(m["id"]) for x in want):
            continue
        text = (job.source / "chunks" / f"{m['id']}.txt").read_text(encoding="utf-8")
        h = _hash(text)
        old = have.get(m["id"]) or []
        if not force and old and all(d.get("_chunk_hash") == h for d in old):
            continue
        todo.append({**m, "text": text, "hash": h})
    if not todo:
        log("학습카드는 이미 다 만들어 두었어요.")
        return
    batches = _batches(todo, int(ccfg.get("batch", 3)), int(ccfg.get("max_chunk_lines", 400)))
    log(f"학습카드를 쓰는 중이에요 — 조각 {len(todo)}개, {len(batches)}번에 나눠서 (한 번에 1~3분)")
    model = models.resolve()
    models.apply(model)
    p = CodexProvider(model=model, effort=cfg["effort"].get("cards", "medium"),
                      on_activity=lambda t: detail(f"    … {t}"))
    system = PROMPT.read_text(encoding="utf-8")
    made = 0
    for bi, batch in enumerate(batches, 1):
        detail(f"  [{bi}/{len(batches)}] {batch[0]['id']} … {batch[-1]['id']} · {model}")
        raw = p.structured(system, [{"role": "user", "content": _brief(job, batch, int(ccfg.get("split_lines", 150)))}],
                           schema=BATCH_SCHEMA)
        got: Dict[str, List[Dict[str, Any]]] = {}
        for d in raw.get("cards") or []:
            got.setdefault(base_id(str(d.get("id", ""))), []).append(d)
        for c in batch:
            cards = got.get(c["id"]) or []
            if not cards:
                log(f"  ⚠ {c['id']} {c.get('sub_name') or c['sec_name']} — 카드를 받지 못했어요. 한 번 더 누르면 이것만 다시 써요")
                continue
            for d in have.get(c["id"]) or []:
                (job.card_json / f"{d['id']}.json").unlink(missing_ok=True)
            if len(cards) > 1:      # 나눈 카드는 a,b,c 로 맞춘다
                for k, d in enumerate(cards):
                    d["id"] = f"{c['id']}{'abcdefgh'[k]}"
            else:
                cards[0]["id"] = c["id"]
            for d in cards:
                d.update({"_chunk_hash": c["hash"], "_made_at": time.strftime("%Y-%m-%d %H:%M"), "_source": "pdf"})
                d = normalize(d)
                bad = problems(d)
                if bad:
                    detail(f"  {d['id']} 점검: {', '.join(bad)}")
                config.write_json(job.card_json / f"{d['id']}.json", d)
                made += 1
                log(f"  ✓ {d['id']} {d['title']}")
        if usage():
            log(usage())
    detail(codex_transport.limits_line())
    log(f"학습카드 {made}장을 새로 썼어요.")


def run(job: Job, only=None, force: bool = False, **kw) -> None:
    if job.get("kind") == "cards":
        folder = Path(job.get("cards_dir") or "")
        if not folder.is_dir():
            raise SystemExit(f"카드 폴더를 찾을 수 없어요: {folder}")
        import_folder(job, folder)
    else:
        from_pdf(job, only=only, force=force)
    job.__dict__.pop("cards", None)     # cached_property 다시 읽기
    if job.cards:
        render.run(job)

