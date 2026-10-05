"""교재 PDF → 글(all.txt) → 소단원 조각(chunks/<장>-<절>-<소단원>.txt) + 목차(manifest.json). LLM 없음.

textbook-study-cards 스킬 §1 그대로:
  절      ^\\s{0,3}(\\d)\\s{3,}(\\S.*)$            줄 길이 < 30
  소단원  ^\\s{0,3}([가나다…하])\\.\\s(\\S.{0,30})$  줄 길이 < 35, '다.' 로 끝나는 줄 제외(본문 문장 오인식)
  절 번호가 1 로 돌아가면 장 번호 +1
글 뽑기는 pdftotext -layout 이 있으면 그것(스킬과 같음), 없으면 pypdf 의 layout 모드.
교재마다 제목 모양이 다르면 config cards.sec_re / sub_re 로 바꾼다. 결과 목록은 로그로 보여 준다 —
**카드 만들기(할당량을 쓴다) 전에 목차와 맞는지 눈으로 확인**한다.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List

from .. import config
from ..job import Job
from ..log import detail, log

SEC_RE = r"^\s{0,3}(\d)\s+([^\s\d.()].*)$"
CHAP_RE = re.compile(r"^\s*(?:제\s*(\d+)\s*장|(\d+)\s*제\s*장)\s+([^\s\d].*?)\s*$")
SUB_RE = r"^\s{0,3}([가나다라마바사아자차카타파하])\.\s(\S.{0,30})$"


def pdf_text(pdf: Path) -> str:
    exe = shutil.which("pdftotext")
    if exe:
        r = subprocess.run([exe, "-layout", "-enc", "UTF-8", str(pdf), "-"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0 and r.stdout.strip():
            detail(f"pdftotext 로 글 뽑음: {exe}")
            return r.stdout.decode("utf-8", "replace")
        detail(f"pdftotext 실패 → pypdf: {r.stderr[:200]!r}")
    from pypdf import PdfReader
    pages = []
    for pg in PdfReader(str(pdf)).pages:
        try:
            pages.append(pg.extract_text(extraction_mode="layout") or "")
        except TypeError:       # 오래된 pypdf
            pages.append(pg.extract_text() or "")
    detail("pypdf(layout) 로 글 뽑음")
    return "\f".join(pages)


TOC_RE = re.compile(r"(\d)\.\s+(\S.*?)\s+(\d{1,3})\s*$")


def _norm(x: str) -> str:
    return re.sub(r"\s+", "", x)


def read_toc(lines: List[str], head: int = 400) -> List[Dict[str, Any]]:
    """교재 앞쪽 목차의 「N. 절 이름 …… 쪽」 줄 → [{chapter, sec_no, sec_name}]. 절 번호가 1 이면 다음 장."""
    out: List[Dict[str, Any]] = []
    chapter = 0
    for line in lines[:head]:
        m = TOC_RE.search(line)
        if not m:
            continue
        n = int(m.group(1))
        if n == 1:
            chapter += 1
        if chapter and (not out or n != out[-1]["sec_no"] or chapter != out[-1]["chapter"]):
            out.append({"chapter": chapter, "sec_no": n, "sec_name": m.group(2).strip()})
    return out if len(out) >= 3 else []


def split(text: str, chapter_names: Dict[int, str] | None = None) -> List[Dict[str, Any]]:
    """글 → [{id, chapter, chapter_name, sec_no, sec_name, sub_name, text, lines}].

    교재 앞쪽에 목차가 있으면 **목차의 절 순서대로만** 절 제목을 인정한다(그림 속 「1 …」 줄을 절로 오인하지 않게).
    목차가 없으면 스킬 정규식만으로 나눈다."""
    cfg = config.load()["cards"]
    sec_re = re.compile(cfg.get("sec_re") or SEC_RE)
    sub_re = re.compile(cfg.get("sub_re") or SUB_RE)
    lines = text.replace("\f", "\n").splitlines()
    toc = read_toc(lines)
    ti = 0
    chapter, sec, sec_name, subs = 0, 0, "", 0
    cur: Dict[str, Any] | None = None
    out: List[Dict[str, Any]] = []

    def open_chunk(sub_no: int, sub_name: str) -> Dict[str, Any]:
        return {"id": f"{chapter}-{sec}-{sub_no:02d}", "chapter": chapter,
                "chapter_name": (chapter_names or {}).get(chapter, f"{chapter}장"),
                "sec_no": sec, "sec_name": sec_name, "sub_name": sub_name, "body": []}

    names: Dict[int, str] = {}
    for line in lines:
        cm = CHAP_RE.match(line)
        if cm and len(line.strip()) < 40:
            names.setdefault(int(cm.group(1) or cm.group(2)), cm.group(3).strip())
            continue
        m = sec_re.match(line)
        if m and len(line.strip()) < 30:
            n, name = int(m.group(1)), m.group(2).strip()
            ok = True
            if toc:
                ok = ti < len(toc) and toc[ti]["sec_no"] == n and _norm(toc[ti]["sec_name"]).startswith(_norm(name)[:6])
            if ok:
                if toc:
                    chapter, name = toc[ti]["chapter"], toc[ti]["sec_name"]
                    ti += 1
                elif n == 1 or chapter == 0:
                    chapter += 1
                sec, sec_name, subs = n, name, 0
                cur = open_chunk(0, "")
                out.append(cur)
                continue
        m = sub_re.match(line)
        if m and len(line.strip()) < 35 and not line.rstrip().endswith("다.") and chapter:
            subs += 1
            if cur is not None and cur["sub_name"] == "" and not "".join(cur["body"]).strip():
                out.remove(cur)         # 절 제목 바로 밑에 소단원이 오면 빈 -00 조각은 버린다
            cur = open_chunk(subs, m.group(2).strip())
            out.append(cur)
            continue
        if cur is not None:
            cur["body"].append(line)
    for c in out:
        if not (chapter_names or {}).get(c["chapter"]) and names.get(c["chapter"]):
            c["chapter_name"] = names[c["chapter"]]
        c["text"] = "\n".join(c.pop("body")).strip("\n")
        c["lines"] = c["text"].count("\n") + 1 if c["text"] else 0
    return [c for c in out if c["lines"] > 0]


def run(job: Job, pdf: Path | None = None, **_) -> List[Dict[str, Any]]:
    pdf = Path(pdf or job.get("pdf") or "")
    if not pdf.is_file():
        raise SystemExit(f"교재 PDF 를 찾을 수 없어요: {pdf}")
    log(f"교재에서 글을 뽑는 중이에요 — {pdf.name}")
    text = pdf_text(pdf)
    src = job.source
    (src / "chunks").mkdir(parents=True, exist_ok=True)
    (src / "all.txt").write_text(text, encoding="utf-8")
    chunks = split(text, job.get("chapter_names") and {int(k): v for k, v in job.get("chapter_names").items()})
    if not chunks:
        raise SystemExit("교재에서 장·절 제목을 찾지 못했어요. 이 교재는 제목 모양이 달라서 설정을 맞춰야 해요 — 담당자에게 문의해 주세요")
    for old in (src / "chunks").glob("*.txt"):
        old.unlink()
    for c in chunks:
        (src / "chunks" / f"{c['id']}.txt").write_text(c["text"], encoding="utf-8")
    man = [{k: c[k] for k in ("id", "chapter", "chapter_name", "sec_no", "sec_name", "sub_name", "lines")} for c in chunks]
    config.write_json(src / "manifest.json", man)
    log(f"조각 {len(chunks)}개로 나눴어요. 교재 목차와 맞는지 확인해 주세요:")
    cur = None
    for m in man:
        if (m["chapter"], m["sec_no"]) != cur:
            cur = (m["chapter"], m["sec_no"])
            log(f"  제{m['chapter']}장 · {m['sec_no']}. {m['sec_name']}")
        log(f"     {m['id']}  {m['sub_name'] or '(절 본문)'}  — {m['lines']}줄")
    return man
