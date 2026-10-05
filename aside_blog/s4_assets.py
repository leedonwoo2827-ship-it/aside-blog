"""s4-assets — 카드에서 그림 찍기 + 디자인 표지. 할당량 없음 (Playwright 헤드리스).

네이버 스마트에디터에는 접기 칸(<details>)도, 카드 같은 표 꾸밈도 없다. 그래서
  table-N.png  카드의 표(소제목 포함)를 카드 모양 그대로
  terms.png    핵심 용어 칸 (post.terms_mode: image 일 때 씀)
  quiz.png     OX 셀프체크 — 정답을 **펼친 상태로**
  cover.png    디자인 표지 1536×1024 — Codex 그림(images/<id>-cover.png)이 없을 때의 대표이미지
를 assets/<id>/ 에 남긴다. 카드 HTML(cards/html)이 먼저 있어야 한다.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, List

from . import config
from .cards import render
from .job import Card, Job
from .log import detail, log

COVER = config.TEMPLATES / "cover" / "cover.html"
FONTS = config.TEMPLATES / "fonts"

# 카드 안 영역의 위치(소제목 h2 부터 그 영역 끝까지)를 돌려준다
_RECTS_JS = """() => {
  const card = document.querySelector('.card').getBoundingClientRect();
  const y0 = window.scrollY;
  const box = (els) => { let t = 1e9, b = -1e9; els.filter(Boolean).forEach(e => { const r = e.getBoundingClientRect();
      t = Math.min(t, r.top); b = Math.max(b, r.bottom); }); return {x: card.left, y: t + y0, width: card.width, height: b - t}; };
  const h2s = [...document.querySelectorAll('.bd > h2')];
  const out = {tables: [], terms: null, quiz: null, exam: null};
  h2s.forEach(h => { const n = h.nextElementSibling; if (!n) return;
    if (n.tagName === 'TABLE') { const c = n.nextElementSibling && n.nextElementSibling.classList.contains('cap') ? n.nextElementSibling : null;
      out.tables.push(box([h, n, c])); }
    else if (n.classList.contains('terms')) out.terms = box([h, n]);
    else if (n.classList.contains('quiz')) out.quiz = box([h, n]);
    else if (n.classList.contains('exam')) out.exam = box([h, n]); });
  return out; }"""


def _pad(r: Dict[str, float], p: int = 14) -> Dict[str, float]:
    return {"x": r["x"], "y": max(0, r["y"] - p), "width": r["width"], "height": r["height"] + 2 * p}


def cover_html(job: Job, c: Card) -> str:
    col, light = render.palette(c.chapter)
    title = c.title
    size = 96 if len(title) <= 10 else 84 if len(title) <= 16 else 72 if len(title) <= 24 else 60
    rep = {"{{FONTS}}": FONTS.as_uri(), "{{BG}}": config.load()["image"]["bg"], "{{C}}": col, "{{L}}": light,
           "{{NO}}": f"{c.n:02d}", "{{BOOK}}": html.escape(job.setting("book")),
           "{{CRUMB}}": html.escape(c.crumb.replace(" › ", " · ")), "{{TITLE}}": render.fmt(title),
           "{{SUB}}": render.fmt(c.subtitle), "{{SERIES}}": html.escape(job.setting("series")), "{{TSIZE}}": str(size)}
    out = COVER.read_text(encoding="utf-8")
    for k, v in rep.items():
        out = out.replace(k, v)
    return out


def shoot(job: Job, c: Card, browser, scale: int, width: int) -> List[str]:
    d = job.assets / c.id
    d.mkdir(parents=True, exist_ok=True)
    made: List[str] = []
    src = job.html_path(c)
    if not src.exists():
        raise SystemExit("학습카드 HTML 이 없어요 — 먼저 「카드 다시 굽기」를 눌러 주세요")
    page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=scale)
    page.goto(src.as_uri())
    # 카드 바깥 여백·그림자 걷기, 정답 펼치기
    page.add_style_tag(content=".card{margin:0 auto!important;box-shadow:none!important;border-radius:0!important}"
                               "body{background:#fff!important}")
    page.evaluate("document.querySelectorAll('details').forEach(d => d.open = true)")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(150)
    rects = page.evaluate(_RECTS_JS)
    for k, r in enumerate(rects["tables"], 1):
        page.screenshot(path=str(d / f"table-{k}.png"), clip=_pad(r), full_page=True)
        made.append(f"table-{k}")
    for name in ("terms", "quiz", "exam"):
        if rects.get(name):
            page.screenshot(path=str(d / f"{name}.png"), clip=_pad(rects[name]), full_page=True)
            made.append(name)
    page.close()
    cp = browser.new_page(viewport={"width": 1536, "height": 1024}, device_scale_factor=1)
    (d / "cover.html").write_text(cover_html(job, c), encoding="utf-8")
    cp.goto((d / "cover.html").as_uri())
    cp.evaluate("document.fonts.ready")
    cp.wait_for_timeout(120)
    cp.screenshot(path=str(d / "cover.png"), clip={"x": 0, "y": 0, "width": 1536, "height": 1024})
    cp.close()
    made.append("cover")
    return made


def run(job: Job, only=None, **_) -> None:
    from playwright.sync_api import sync_playwright

    cfg = config.load()["assets"]
    if not (FONTS / "Pretendard-Bold.woff2").exists():
        detail("Pretendard 글꼴 없음 — python -m aside_blog fonts (지금은 기본 글꼴)")
    cards = job.pick(only)
    if not job.card_html.exists() or not any(job.card_html.glob("*.html")):
        render.run(job)
    log(f"카드에서 표·OX 그림과 표지를 만드는 중이에요 ({len(cards)}장) …")
    n = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for c in cards:
            made = shoot(job, c, browser, int(cfg.get("scale", 2)), int(cfg.get("width", 860)))
            n += 1
            detail(f"  {c.id}: {', '.join(made)}")
            if n % 10 == 0 or n == len(cards):
                log(f"  … {n}/{len(cards)}")
        browser.close()
    log(f"카드 그림 완성! {n}장 — 결과 보기에서 확인해 보세요.")


def slot_file(job: Job, card_id: str, slot: str):
    """블록의 그림 자리 → 실제 파일(없으면 None). cover 는 Codex 그림 → 디자인 표지 순."""
    if slot == "cover":
        return job.cover_path(card_id)
    if re.fullmatch(r"sec\d+", slot):
        p = job.art(card_id, slot)
        return p if p.exists() else None
    p = job.asset(card_id, slot)
    return p if p.exists() else None
