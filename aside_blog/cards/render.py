"""학습카드 JSON → HTML (textbook-study-cards 스킬 §3 render.py 를 옮긴 것). LLM 없음.

결과 꼴은 `_context/변액보험_학습카드/` 와 **글자 하나까지 같다** — CSS 는 그 폴더 카드에서 떼어 온
`templates/cards/card.css`, 파일 이름은 `NN_<id>_<제목 24자>.html`, 목차는 `00_목차.html`.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List

from .. import config
from ..job import Card, Job, base_id
from ..log import log

CSS_FILE = config.TEMPLATES / "cards" / "card.css"
# 장마다 돌아가는 색 (진한 색, 연한 색)
PAL = [("#1f4e8c", "#e8f0fb"), ("#0f766e", "#e6f6f3"), ("#7c3aed", "#f1ebfe"),
       ("#b45309", "#fdf2e3"), ("#be123c", "#fdecef"), ("#334155", "#eef2f6")]
TOC_COLOR = ("#1f2a44", "#eef1f7")


def css() -> str:
    return CSS_FILE.read_text(encoding="utf-8")


def fmt(s: Any) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(str(s)))


def palette(chapter: int) -> tuple:
    return PAL[(int(chapter or 1) - 1) % len(PAL)]


def card_body(c: Card, book: str, source: str) -> str:
    d = c.data
    col, light = palette(c.chapter)
    p = [f'<div class="card" style="--c:{col};--l:{light}"><div class="hd"><div class="no">{html.escape(book)} #{c.n:02d}</div>'
         f'<div class="crumb">{html.escape(c.crumb)}</div><h1>{fmt(d["title"])}</h1>'
         f'<p class="sub">{fmt(d.get("subtitle", ""))}</p></div><div class="bd"><p class="intro">{fmt(d.get("intro", ""))}</p>']
    for s in d.get("sections") or []:
        p.append(f'<h2>{fmt(s["heading"])}</h2><ul>' + "".join(f"<li>{fmt(x)}</li>" for x in s.get("points") or []) + "</ul>")
    for t in d.get("tables") or []:
        p.append(f'<h2>{fmt(t.get("title") or "한눈에 비교")}</h2><table><tr>'
                 + "".join(f"<th>{fmt(h)}</th>" for h in t.get("headers") or []) + "</tr>"
                 + "".join("<tr>" + "".join(f"<td>{fmt(x)}</td>" for x in r) + "</tr>" for r in t.get("rows") or [])
                 + "</table>" + (f'<div class="cap">{fmt(t["caption"])}</div>' if t.get("caption") else ""))
    if d.get("terms"):
        p.append('<h2>핵심 용어</h2><div class="terms">'
                 + "".join(f'<div class="term"><b>{fmt(t["term"])}</b>{fmt(t["desc"])}</div>' for t in d["terms"]) + "</div>")
    if d.get("exam_points"):
        p.append('<h2>시험 포인트</h2><div class="exam"><ul>' + "".join(f"<li>{fmt(x)}</li>" for x in d["exam_points"]) + "</ul></div>")
    if d.get("quiz"):
        p.append('<h2>OX 셀프체크</h2><div class="quiz">' + "".join(
            f'<details><summary>Q. {fmt(q["q"])}</summary><div class="ans"><span class="ox {"o" if q.get("o") else "x"}">'
            f'{"O" if q.get("o") else "X"}</span>{fmt(q["a"])}</div></details>' for q in d["quiz"]) + "</div>")
    tags = "".join(f"<span>#{html.escape(t)}</span>" for t in d.get("tags") or [])
    p.append(f'<div class="one"><span>한 줄 정리</span>{fmt(d.get("one_line", ""))}</div></div>'
             f'<div class="ft">{html.escape(source)}<div class="tags">{tags}</div></div></div>')
    return "".join(p)


def page(title: str, body: str, style: str) -> str:
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><style>{style}</style>'
            f"</head><body>{body}</body></html>")


def card_page(job: Job, c: Card) -> str:
    book = job.setting("book")
    return page(f"#{c.n:02d} {c.title} | {book}", card_body(c, book, job.setting("source")), css())


def toc_page(job: Job, cards: List[Card]) -> str:
    book, series = job.setting("book"), job.setting("series")
    rows: List[str] = []
    cur = None
    for c in cards:
        m = c.man
        k = (m.get("chapter"), m.get("sec_no"))
        if k != cur:
            rows.append(("</ul>" if cur else "") + f'<h2>제{m.get("chapter")}장 {m.get("chapter_name", "")} · '
                        f'{m.get("sec_no")}. {m.get("sec_name", "")}</h2><ul>')
            cur = k
        rows.append(f'<li><a href="{html.escape(c.filename)}">#{c.n:02d} {fmt(c.title)}</a> '
                    f'<span style="color:#8a909c">— {fmt(c.subtitle)}</span></li>')
    body = (f'<div class="card" style="--c:{TOC_COLOR[0]};--l:{TOC_COLOR[1]}"><div class="hd"><div class="no">목차</div>'
            f'<h1>{book}</h1><p class="sub">{html.escape(series)}</p></div><div class="bd">' + "".join(rows) + "</ul></div></div>")
    return page(book + " 목차", body, css())


def run(job: Job, **_) -> int:
    cards = job.cards
    if not cards:
        raise SystemExit("학습카드가 아직 없어요 — 먼저 교재에서 카드를 만들거나 카드 폴더를 불러와 주세요")
    out = job.card_html
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.html"):
        old.unlink()
    for c in cards:
        (out / c.filename).write_text(card_page(job, c), encoding="utf-8", newline="\n")
    (out / "00_목차.html").write_text(toc_page(job, cards), encoding="utf-8", newline="\n")
    log(f"학습카드 HTML {len(cards)}장 + 목차를 만들었어요.")
    return len(cards)


def base_ids(cards: List[Dict[str, Any]]) -> List[str]:
    seen: List[str] = []
    for d in cards:
        b = base_id(d["id"])
        if b not in seen:
            seen.append(b)
    return seen
