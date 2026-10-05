"""이미 만든 학습카드 HTML 폴더(`_context/변액보험_학습카드` 같은 것) → 카드 JSON + 목차. LLM 없음.

render.py 의 거꾸로다: render(parse(html)) 가 원래 HTML 과 같아야 한다(검증 명령 `check-cards`).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup, NavigableString, Tag

FILE_RE = re.compile(r"^(\d+)_(\d+-\d+-\d+[a-z]?)_.*\.html$")
CRUMB_RE = re.compile(r"^제(\d+)장 (.*?) › (\d+)\. (.*)$")
TOC_H2_RE = re.compile(r"^제(\d+)장 (.*?) · (\d+)\. (.*)$")


def md(el: Optional[Tag], skip: Tuple[str, ...] = ()) -> str:
    """요소 안 글자 → <strong> 은 **굵게** 로, 나머지 태그는 벗긴다."""
    if el is None:
        return ""
    parts: List[str] = []
    for ch in el.children:
        if isinstance(ch, NavigableString):
            parts.append(str(ch))
        elif isinstance(ch, Tag):
            if ch.name in skip or (ch.get("class") and set(ch.get("class")) & set(skip)):
                continue
            inner = md(ch, skip)
            parts.append(f"**{inner}**" if ch.name == "strong" else inner)
    return "".join(parts)


def _text(el: Optional[Tag]) -> str:
    return el.get_text() if el is not None else ""


def parse_card(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """카드 하나 → (카드 JSON, {n, book, source, man})."""
    m = FILE_RE.match(path.name)
    if not m:
        raise ValueError(f"카드 파일 이름이 아니에요: {path.name}")
    n, cid = int(m.group(1)), m.group(2)
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    card = soup.select_one("div.card")
    hd, bd = card.select_one(".hd"), card.select_one(".bd")
    no = _text(hd.select_one(".no"))
    book = re.sub(r"\s*#\d+\s*$", "", no).strip()
    crumb = _text(hd.select_one(".crumb"))
    cm = CRUMB_RE.match(crumb)
    man = ({"chapter": int(cm.group(1)), "chapter_name": cm.group(2), "sec_no": int(cm.group(3)),
            "sec_name": cm.group(4)} if cm else {})
    d: Dict[str, Any] = {"id": cid, "title": md(hd.select_one("h1")), "subtitle": md(hd.select_one("p.sub")),
                         "intro": md(bd.select_one("p.intro")), "sections": [], "tables": [], "terms": [],
                         "exam_points": [], "quiz": [], "one_line": "", "tags": []}
    kids = [k for k in bd.children if isinstance(k, Tag)]
    i = 0
    while i < len(kids):
        k = kids[i]
        if k.name == "h2":
            nxt = kids[i + 1] if i + 1 < len(kids) else None
            cls = set(nxt.get("class") or []) if nxt is not None else set()
            head = md(k)
            if nxt is not None and nxt.name == "ul":
                d["sections"].append({"heading": head, "points": [md(li) for li in nxt.find_all("li", recursive=False)]})
                i += 2
                continue
            if nxt is not None and nxt.name == "table":
                rows = nxt.find_all("tr")
                t: Dict[str, Any] = {"title": head, "headers": [md(th) for th in rows[0].find_all("th")] if rows else [],
                                     "rows": [[md(td) for td in r.find_all("td")] for r in rows[1:]]}
                step = 2
                cap = kids[i + 2] if i + 2 < len(kids) else None
                if cap is not None and "cap" in (cap.get("class") or []):
                    t["caption"] = md(cap)
                    step = 3
                d["tables"].append(t)
                i += step
                continue
            if "terms" in cls:
                for t in nxt.select(".term"):
                    d["terms"].append({"term": md(t.find("b")), "desc": md(t, skip=("b",))})
            elif "exam" in cls:
                d["exam_points"] = [md(li) for li in nxt.find_all("li")]
            elif "quiz" in cls:
                for det in nxt.find_all("details"):
                    q = re.sub(r"^Q\.\s", "", md(det.find("summary")))
                    ans = det.select_one(".ans")
                    ox = ans.select_one(".ox")
                    d["quiz"].append({"q": q, "o": "o" in (ox.get("class") or []), "a": md(ans, skip=("ox",))})
            i += 2
            continue
        if "one" in (k.get("class") or []):
            d["one_line"] = md(k, skip=("span",))
        i += 1
    ft = card.select_one(".ft")
    source = "".join(str(x) for x in ft.children if isinstance(x, NavigableString)).strip() if ft else ""
    d["tags"] = [_text(s).lstrip("#") for s in ft.select(".tags span")] if ft else []
    for k in ("tables", "terms", "exam_points", "quiz"):
        if not d[k]:
            d.pop(k)
    return d, {"n": n, "book": book, "source": source, "man": man}


def parse_toc(path: Path) -> Dict[str, Any]:
    """00_목차.html → {book, series, order: [card id …]}."""
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    out = {"book": _text(soup.select_one(".hd h1")), "series": _text(soup.select_one(".hd p.sub")), "order": []}
    for a in soup.select(".bd li a"):
        m = FILE_RE.match(a.get("href", ""))
        if m:
            out["order"].append(m.group(2))
    return out


def parse_folder(folder: Path) -> Dict[str, Any]:
    """폴더 전체 → {cards: [카드 JSON …] (번호순), manifest: [...], book, series, source}."""
    files = sorted((p for p in folder.glob("*.html") if FILE_RE.match(p.name)), key=lambda p: int(p.name.split("_")[0]))
    if not files:
        raise SystemExit(f"학습카드 HTML 이 없어요: {folder} (NN_장-절-항_제목.html 꼴)")
    cards, manifest, seen = [], [], set()
    book = source = ""
    for f in files:
        d, info = parse_card(f)
        cards.append(d)
        book = book or info["book"]
        source = source or info["source"]
        b = re.match(r"^(\d+-\d+-\d+)", d["id"]).group(1)
        if b not in seen and info["man"]:
            seen.add(b)
            manifest.append({"id": b, **info["man"], "sub_name": "", "lines": 0})
    toc = parse_toc(folder / "00_목차.html") if (folder / "00_목차.html").exists() else {}
    return {"cards": cards, "manifest": manifest, "book": toc.get("book") or book,
            "series": toc.get("series", ""), "source": source}
