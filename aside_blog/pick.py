"""Windows 기본 선택 창 — 「교재 PDF 고르기」·「카드 폴더 고르기」.

브라우저는 보안상 실제 경로를 알려 주지 않으므로, 같은 PC 에서 도는 서버가 대신 창을 띄운다.
tkinter 는 스레드를 가리므로 서버가 이 모듈을 **별도 프로세스**로 부르고 stdout 의 JSON 만 읽는다.
  python -m aside_blog.pick folder   → {"folder": "...", "kind": "cards"|"pdf"|"", "files": [...]}
  python -m aside_blog.pick files    → {"files": [...]}   (PDF)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def classify(d: Path) -> dict:
    """폴더 안이 학습카드 폴더(00_목차.html + NN_*.html)인지, 교재 PDF 폴더인지."""
    if not d or not d.is_dir():
        return {"folder": str(d or ""), "kind": "", "files": []}
    cards = sorted(p for p in d.glob("*.html") if p.name[:2].isdigit() and p.name != "00_목차.html")
    if (d / "00_목차.html").exists() or cards:
        return {"folder": str(d), "kind": "cards", "files": [str(p) for p in cards]}
    pdfs = sorted(d.glob("*.pdf"))
    return {"folder": str(d), "kind": "pdf" if pdfs else "", "files": [str(p) for p in pdfs]}


def main(kind: str) -> dict:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)     # 패널·Chrome 뒤에 숨지 않게
    root.update()
    if kind == "folder":
        d = filedialog.askdirectory(parent=root, title="학습카드 폴더 고르기 (00_목차.html 이 있는 폴더)")
        out = classify(Path(d)) if d else {"folder": "", "kind": "", "files": []}
    else:
        fs = filedialog.askopenfilenames(parent=root, title="교재 PDF 고르기",
                                         filetypes=[("교재 PDF", "*.pdf"), ("모든 파일", "*.*")])
        out = {"files": sorted(fs), "kind": "pdf" if fs else ""}
    root.destroy()
    return out


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1] if len(sys.argv) > 1 else "folder"), ensure_ascii=False))
