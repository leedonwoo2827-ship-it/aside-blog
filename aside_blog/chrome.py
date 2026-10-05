"""실제 Chrome(전용 프로필) + CDP — aside-shorts `youtube.py` / aside-threads `threads.py` 의 공통 부분.

왜 Playwright 가 띄운 브라우저가 아닌가: 네이버·구글은 자동화 브라우저 로그인을 막거나 보안 확인을 자주 띄운다.
그래서 **사람이 그 Chrome 에서 한 번 로그인**하고, 이후로는 같은 프로필에 CDP 로 붙어서 조종한다.
그 Chrome 창이 화면 왼쪽에 떠 있고, 오른쪽에 패널이 붙는다 — 글쓰기가 눈앞에서 진행된다.
"""
from __future__ import annotations

import ctypes
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import accounts, config
from .log import detail

HOME = "https://blog.naver.com/"


def chrome_path() -> str:
    """Windows · macOS · Linux 의 Google Chrome. 설정 naver.chrome 이 있으면 그것이 이긴다.

    Chromium 도 받지만 **Google Chrome 을 권한다** — 로그인 차단이 덜하다(lecture-composer 와 같은 이유)."""
    cfg = config.load()["naver"]
    cands = [cfg.get("chrome") or ""]
    if sys.platform.startswith("win"):
        cands += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                  r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                  str(Path.home() / r"AppData\Local\Google\Chrome\Application\chrome.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    else:
        cands += [shutil.which(n) or "" for n in
                  ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")]
    for c in cands:
        if c and Path(c).exists():
            return c
    raise SystemExit("Chrome 을 찾지 못했습니다 — Google Chrome 을 설치하거나 "
                     "aside.config.local.json 의 naver.chrome 에 실행 파일 경로를 적으세요")


_SCREEN: Optional[tuple] = None


def screen() -> tuple:
    """화면 크기(가로, 세로) — 왼쪽 네이버 창과 오른쪽 패널을 나란히 놓는 데 쓴다."""
    global _SCREEN
    if _SCREEN:
        return _SCREEN
    try:
        if sys.platform.startswith("win"):
            u = ctypes.windll.user32
            u.SetProcessDPIAware()
            _SCREEN = (u.GetSystemMetrics(0), u.GetSystemMetrics(1))
        else:
            # macOS·Linux: tkinter 는 메인 스레드를 가리므로 별도 프로세스로 묻는다
            r = subprocess.run([sys.executable.replace("pythonw", "python"), "-c",
                                "import tkinter as t;r=t.Tk();r.withdraw();"
                                "print(r.winfo_screenwidth(), r.winfo_screenheight())"],
                               capture_output=True, text=True, timeout=10)
            w, h = map(int, r.stdout.split())
            _SCREEN = (w, h)
    except Exception:
        _SCREEN = (1440, 900) if sys.platform == "darwin" else (1920, 1080)
    return _SCREEN


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_chrome(acc: Dict[str, Any], url: str = HOME) -> bool:
    """그 계정의 Chrome 을 왼쪽에 띄운다. 이미 떠 있으면 그대로 쓴다. 새로 띄웠으면 True."""
    port = int(acc["port"])
    if port_open(port):
        return False
    sw, sh = screen()
    panel = int(config.load()["ui"]["width"])
    subprocess.Popen([
        chrome_path(), f"--user-data-dir={accounts.profile(acc)}",
        f"--remote-debugging-port={port}", "--no-first-run", "--no-default-browser-check",
        "--window-position=0,0", f"--window-size={max(900, sw - panel)},{sh - 40}", url])
    if not wait_port(port):
        raise SystemExit("Chrome 디버그 포트가 열리지 않습니다 — 같은 프로필 Chrome 이 이미 떠 있으면 닫고 다시")
    try:
        place(port, "left")
    except Exception as e:      # noqa: BLE001 — 배치는 보기 좋으라고 하는 일이다
        detail(f"  (창 배치 실패, 무시: {e})")
    return True


def wait_port(port: int, tries: int = 60) -> bool:
    for _ in range(tries):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1)
            return True
        except Exception:
            time.sleep(0.5)
    return False


PANEL_PORT_OFFSET = -1      # 패널 Chrome 의 디버그 포트 = base_port - 1


def free_port(start: int, avoid=(), span: int = 60) -> int:
    """start 부터 위로 비어 있는 포트 하나. 이런 도구를 여러 개 쓰는 PC 는 포트가 자주 겹친다."""
    for p in range(start, start + span):
        if p not in avoid and not port_open(p):
            return p
    raise SystemExit(f"{start} 근처에 빈 포트가 없어요 — 다른 프로그램을 몇 개 닫고 다시 해 주세요")


def panel_port() -> int:
    """패널 Chrome 의 디버그 포트. 기억해 둔 값 → 없으면 base_port-1. 실제로 고르는 건 pick_panel_port()."""
    saved = config.local().get("panel_port")
    return int(saved) if saved else int(config.load()["naver"]["base_port"]) + PANEL_PORT_OFFSET


def is_our_panel(port: int, ui_port: int) -> bool:
    """그 포트의 Chrome 이 aside 패널인가(다른 도구의 Chrome 일 수도 있다)."""
    import json as _json
    try:
        tabs = _json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1))
        return any(str(t.get("url", "")).startswith(f"http://127.0.0.1:{ui_port}/") for t in tabs)
    except Exception:
        return False


def pick_panel_port(ui_port: int) -> int:
    p = panel_port()
    if not port_open(p) or is_our_panel(p, ui_port):
        return p
    used = {int(a["port"]) for a in accounts.all_()}
    p = free_port(int(config.load()["naver"]["base_port"]) - 40, avoid=used)
    data = config.local()
    data["panel_port"] = p
    config.save_local(data)
    return p


def place(port: int, side: str) -> None:
    """창을 화면 왼쪽(네이버) 또는 오른쪽(패널)에 **정확히** 붙인다.

    ★ 처음에는 실행 인자(--window-position/size)로만 놓았는데, 그 좌표는 Windows 배율(125%·150%)과
      모니터 구성에 따라 Chrome 이 다르게 읽는다 — 패널이 화면 밖으로 밀리고 가운데가 비었다(2026-10-03).
      그래서 창이 뜬 뒤 **Chrome 에게 직접** 쓸 수 있는 화면 영역(screen.avail*)을 묻고,
      같은 단위로 CDP `Browser.setWindowBounds` 를 건다. 배율·OS 와 무관하게 맞는다."""
    from playwright.sync_api import sync_playwright

    width = int(config.load()["ui"]["width"])
    with sync_playwright() as pw:
        br = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        if True:      # with 블록을 나가면 CDP 연결만 끊긴다 — 사람이 보는 창은 그대로 남는다
            ctx = br.contexts[0]
            page = next((p for p in ctx.pages if not p.url.startswith("devtools")), None) or ctx.new_page()
            ax, ay, aw, ah = page.evaluate(
                "[screen.availLeft || 0, screen.availTop || 0, screen.availWidth, screen.availHeight]")
            cdp = ctx.new_cdp_session(page)
            wid = cdp.send("Browser.getWindowForTarget")["windowId"]
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
            if side == "right":
                b = {"left": ax + aw - width, "top": ay, "width": width, "height": ah}
            else:
                b = {"left": ax, "top": ay, "width": max(700, aw - width), "height": ah}
            cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": b})
            cdp.detach()


def arrange(acc: Optional[Dict[str, Any]] = None) -> List[str]:
    """떠 있는 창들을 다시 붙인다 — 패널의 「창 정렬」."""
    done = []
    if port_open(panel_port()):
        place(panel_port(), "right")
        done.append("패널")
    if acc and port_open(int(acc["port"])):
        place(int(acc["port"]), "left")
        done.append(acc["name"])
    return done


class Session:
    """with Session(acc) as s: s.page …  — 끝나도 Chrome 은 닫지 않는다(사람이 계속 본다).

    url_match 가 들어간 탭을 우선 고른다. 없으면 첫 탭(또는 새 탭)."""

    url_match: Sequence[str] = ("blog.naver.com",)
    cookie_urls: Sequence[str] = ("https://www.naver.com", "https://blog.naver.com", "https://nid.naver.com")
    cookie_names: Sequence[str] = ("NID_AUT", "NID_SES")

    def __init__(self, acc: Dict[str, Any], url: str = HOME):
        self.acc = acc
        self.url = url

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        ensure_chrome(self.acc, self.url)
        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.acc['port']}")
        self.ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
        pages = [p for p in self.ctx.pages if any(u in p.url for u in self.url_match)]
        self.page = pages[0] if pages else (self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page())
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        return self

    def __exit__(self, *exc):
        try:
            self._pw.stop()     # CDP 연결만 끊는다 — 창은 남는다
        except Exception:
            pass

    def logged_in(self) -> bool:
        names = {c["name"] for c in self.ctx.cookies(list(self.cookie_urls))}
        return any(n in names for n in self.cookie_names)

    def shot(self, name: str) -> Path:
        d = config.LOGS / "post"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{time.strftime('%m%d-%H%M%S')}-{name}.png"
        try:
            self.page.screenshot(path=str(p))
        except Exception:
            pass
        return p
