"""aside-blog 서버 — 127.0.0.1 전용. 넓은 대시보드(제작)와 오른쪽 좁은 패널(게시)이 이 서버를 본다.

aside-shorts server.py 를 옮긴 것. 무거운 일(Codex·Playwright)은 전부 `python -m aside_blog …` 하위 프로세스로
돌리고 로그만 읽는다(한 번에 하나 — 할당량과 브라우저를 두 일이 다투지 않게). 예약 대기열은 30초마다 확인한다.
"""
from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

from .. import accounts, config, schedule
from ..job import Job, all_jobs
from ..log import log

APP = "aside-blog"
STATIC = Path(__file__).parent / "static"
ALLOWED = {"make", "extract", "s0-cards", "render", "s1-post", "s2-imgjson", "s3-images", "s4-assets",
           "post", "fill", "tabs", "plan", "queue", "login", "probe", "doctor", "fonts", "codex-login"}

app = FastAPI(title=APP)


# ── 하위 프로세스 하나 ────────────────────────────────────────────────────────
def label(args: List[str]) -> str:
    """로그 창에 보일 일 이름 — 명령어 대신 쉬운 말."""
    cmd = args[0] if args else ""
    if cmd == "post":
        return "미리 채워 보기" if "--dry-run" in args else ("예약 넣기" if "--at" in args else "발행하기")
    names = {"make": "딸깍 만들기", "extract": "교재에서 글 뽑기", "s0-cards": "학습카드 만들기", "render": "카드 다시 굽기",
             "s1-post": "블로그 글 쓰기", "s2-imgjson": "그림 설명 준비", "s3-images": "그림 그리기",
             "s4-assets": "카드 그림·표지 만들기", "fill": "글쓰기 창에 넣기", "tabs": "글쓰기 창 찾기",
             "queue": "예약 시간 확인", "plan": "예약 걸기", "login": "로그인 창 열기",
             "probe": "글쓰기 화면 구조 확인", "doctor": "점검", "fonts": "글꼴 받기", "codex-login": "Codex 로그인", "new": "새 작업 만들기"}
    return names.get(cmd, cmd)


class Runner:
    def __init__(self) -> None:
        self.proc: Optional[subprocess.Popen] = None
        self.lines: List[str] = []
        self.cmd: List[str] = []
        self.code: Optional[int] = None
        self.lock = threading.Lock()
        self.stopped = False

    @property
    def busy(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self, args: List[str]) -> None:
        with self.lock:
            if self.busy:
                raise HTTPException(409, "지금 다른 일을 하는 중이에요. 끝나면 다시 눌러 주세요.")
            env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            self.cmd = args
            self.code = None
            self.lines.append(f"▶ {label(args)}")
            from ..log import detail
            detail(f"$ aside_blog {' '.join(args)}")
            self.proc = subprocess.Popen(
                [child_python(), "-m", "aside_blog", *args], cwd=str(config.ROOT), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=self._pump, args=(self.proc,), daemon=True).start()

    def _pump(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            self.lines.append(line.rstrip("\n"))
            if len(self.lines) > 3000:
                del self.lines[:1000]
        self.code = proc.wait()
        if self.stopped:
            self.code = -1
            self.stopped = False
        else:
            self.lines.append("✓ 다 됐어요" if self.code == 0 else "✗ 중간에 멈췄어요 — 바로 위 줄을 확인해 주세요")

    def stop(self) -> None:
        if self.busy:
            self.stopped = True
            self.lines.append("■ 멈췄어요. 이미 된 건 남아 있고, 다시 누르면 남은 것만 이어서 해요.")
            if sys.platform.startswith("win"):
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                self.proc.terminate()


RUN = Runner()


def _scheduler() -> None:
    while True:
        time.sleep(30)
        try:
            if not RUN.busy and schedule.due():
                RUN.start(["queue"])
        except Exception as e:      # noqa: BLE001 — 대기열 확인이 서버를 죽이면 안 된다
            from ..log import detail
            detail(f"대기열 확인 실패: {e}")


# ── 보기 ─────────────────────────────────────────────────────────────────────
def _job(name: str) -> Job:
    job = Job(name)
    if not job.exists():
        raise HTTPException(404, f"작업을 찾을 수 없어요: {name}")
    return job


def _status(job: Job, cid: str, st: Dict[str, Any]) -> str:
    if st.get("posted"):
        return "posted"
    if (st.get("scheduled") or {}).get("when"):
        return "scheduled"
    if st.get("error"):
        return "error"
    if job.raw_post(cid) and job.cover_path(cid):
        return "ready"
    if job.raw_post(cid):
        return "post"
    return "card"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "dashboard.html").read_text(encoding="utf-8")


@app.get("/panel", response_class=HTMLResponse)
def panel_page() -> str:
    return (STATIC / "panel.html").read_text(encoding="utf-8")


@app.get("/api/state")
def state() -> Dict[str, Any]:
    loc = config.local()
    nv = config.load()["naver"]
    return {"jobs": [{"name": j.name, "title": j.get("title", j.name)} for j in all_jobs()],
            "accounts": accounts.all_(), "current": loc.get("current"),
            "slots": nv["slots"], "native": bool(nv.get("native_schedule")), "category": nv.get("category"),
            "image_model": loc.get("codex_image_model"), "busy": RUN.busy, "cmd": RUN.cmd, "app": APP}


@app.get("/api/codex")
def codex_status() -> Dict[str, Any]:
    from ..llm import codex_auth
    codex_auth.restore_if_needed()
    st = codex_auth.status()
    img = config.local().get("codex_image_model")
    st["images"] = None if img is None else img != "none"
    return st


@app.get("/api/log")
def get_log(since: int = 0) -> Dict[str, Any]:
    total = len(RUN.lines)
    since = min(max(0, since), total)
    return {"lines": RUN.lines[since:], "next": total, "busy": RUN.busy, "code": RUN.code, "cmd": RUN.cmd,
            "label": label(RUN.cmd)}


class RunBody(BaseModel):
    args: List[str]


@app.post("/api/run")
def run(body: RunBody) -> Dict[str, Any]:
    if not body.args or body.args[0] not in ALLOWED:
        raise HTTPException(400, "허용되지 않은 명령")
    RUN.start(body.args)
    return {"ok": True}


@app.post("/api/stop")
def stop() -> Dict[str, Any]:
    RUN.stop()
    return {"ok": True}


@app.get("/api/jobs/{name}/cards")
def cards(name: str) -> Dict[str, Any]:
    """진행표 — 카드 → 글 → 그림 → 게시."""
    job = _job(name)
    st_all = job.state()
    rows = []
    for c in job.cards:
        st = st_all.get(c.id) or {}
        post = job.raw_post(c.id)
        arts = sorted(p.stem.split("-", 1)[-1] for p in job.images.glob(f"{c.id}-*.png")) if job.images.exists() else []
        cover = job.cover_path(c.id)
        rows.append({
            "id": c.id, "n": c.n, "title": c.title, "subtitle": c.subtitle, "chapter": c.chapter,
            "sec": c.man.get("sec_name", ""), "crumb": c.crumb,
            "post_title": (job.post(c.id) or {}).get("title", "") if post else "",
            "steps": {"post": bool(post), "codex": bool(post and post.get("cover_scene")), "art": arts,
                      "assets": job.asset(c.id, "cover").exists()},
            "cover": job.url(cover) + f"?v={int(cover.stat().st_mtime)}" if cover else "",
            "status": _status(job, c.id, st),
            "scheduled": st.get("scheduled"), "posted": st.get("posted"), "error": st.get("error"),
        })
    return {"title": job.get("title", name), "kind": job.get("kind"), "pdf": job.get("pdf", ""),
            "cards_dir": job.get("cards_dir", ""), "chunks": len(job.manifest), "rows": rows,
            "skip": job.get("skip_chunks") or [], "busy_cmd": RUN.cmd if RUN.busy else []}


@app.get("/api/jobs/{name}/chunks")
def chunks(name: str) -> List[Dict[str, Any]]:
    """교재에서 찾은 목차(조각) — 카드 만들기 전 확인용."""
    job = _job(name)
    have = {c.id.rstrip("abcdefgh") for c in job.cards}
    skip = set(job.get("skip_chunks") or [])
    return [{**m, "has": m["id"] in have, "skip": m["id"] in skip} for m in job.manifest]


@app.get("/api/jobs/{name}/cards/{cid}")
def card_detail(name: str, cid: str) -> Dict[str, Any]:
    from ..naver import resolve_blocks
    from ..s2_imgjson import plan as img_plan
    from ..s3_images import full_prompt
    job = _job(name)
    c = job.card(cid)
    if not c:
        raise HTTPException(404)
    post = job.post(cid)
    blocks = []
    if post:
        for b in resolve_blocks(job, post):
            if b["type"] == "image":
                f = Path(b["file"])
                b = {**b, "url": job.url(f) + f"?v={int(f.stat().st_mtime)}"}
                b.pop("file", None)
            blocks.append(b)
    prompts = []
    if post:
        env = config.read_json(job.images / "이미지프롬프트.json", {}) or {}
        for it in img_plan(job, [cid]):
            f = job.images / it["file"]
            prompts.append({"role": it["role"], "file": it["file"], "prompt": full_prompt(it, env.get("style_hint", "")),
                            "has": f.exists(), "url": job.url(f) + f"?v={int(f.stat().st_mtime)}" if f.exists() else ""})
    html_p = job.html_path(c)
    return {"id": cid, "n": c.n, "card": c.data, "crumb": c.crumb, "post": post, "blocks": blocks,
            "overridden": job.overridden(cid), "prompts": prompts,
            "card_html": job.url(html_p) if html_p.exists() else "",
            "state": job.state().get(cid) or {},
            "next_slot": schedule.next_slots(1, config.local().get("current"))[0].strftime("%Y-%m-%dT%H:%M")}


class Patch(BaseModel):
    patch: Dict[str, Any]


@app.put("/api/jobs/{name}/cards/{cid}")
def save_post(name: str, cid: str, body: Patch) -> Dict[str, Any]:
    job = _job(name)
    if not job.raw_post(cid):
        raise HTTPException(400, "글이 아직 없어요. 먼저 「딸깍 만들기」를 눌러 주세요.")
    allowed = {"title", "tags", "blocks", "category"}
    bad = set(body.patch) - allowed
    if bad:
        raise HTTPException(400, f"고칠 수 없는 칸: {', '.join(bad)}")
    if "blocks" in body.patch:      # 목록은 통째로 바꾼다(deep_merge 가 리스트를 덮어쓴다)
        pass
    job.save_override(cid, body.patch)
    return {"ok": True}


@app.delete("/api/jobs/{name}/cards/{cid}/override")
def reset_post(name: str, cid: str) -> Dict[str, Any]:
    _job(name).reset_override(cid)
    return {"ok": True}


def _safe(base: Path, rest: str) -> Path:
    base = base.resolve()
    p = (base / rest).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(404)
    return p


@app.get("/jobs/{rest:path}")
def job_files(rest: str):
    return FileResponse(_safe(config.JOBS, rest), headers={"Cache-Control": "no-store"})


@app.get("/templates/{rest:path}")
def template_files(rest: str):
    return FileResponse(_safe(config.TEMPLATES, rest))


class Upload(BaseModel):
    data_url: str


@app.post("/api/jobs/{name}/images/{cid}/{role}")
def put_image(name: str, cid: str, role: str, body: Upload) -> Dict[str, Any]:
    """사람이 만든 그림 넣기(ChatGPT 앱 등) — 「그림 넣기」. role: cover | sec1 | sec2."""
    if not re.fullmatch(r"cover|sec\d", role):
        raise HTTPException(400)
    job = _job(name)
    if not job.card(cid):
        raise HTTPException(404)
    head, _, b64 = body.data_url.partition(",")
    if "image/" not in head:
        raise HTTPException(400, "그림 파일이 아니에요.")
    raw = base64.b64decode(b64)
    job.images.mkdir(parents=True, exist_ok=True)
    out = job.art(cid, role)
    if "image/png" in head:
        out.write_bytes(raw)
    else:       # jpg·webp → png (브라우저로 다시 찍는다)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            pg = b.new_page()
            pg.set_content(f'<img id=i src="{body.data_url}" style="display:block">')
            pg.locator("#i").screenshot(path=str(out))
            b.close()
    baked = config.read_json(job.images / "baked.json", {}) or {}
    baked[out.name] = "manual"
    config.write_json(job.images / "baked.json", baked)
    return {"ok": True}


@app.delete("/api/jobs/{name}/images/{cid}/{role}")
def del_image(name: str, cid: str, role: str) -> Dict[str, Any]:
    if not re.fullmatch(r"cover|sec\d", role):
        raise HTTPException(400)
    _job(name).art(cid, role).unlink(missing_ok=True)
    return {"ok": True}


# ── 설정 ─────────────────────────────────────────────────────────────────────
EDITABLE = {
    "post": ["title_max", "tags_max", "table_mode", "terms_mode", "quiz_mode", "section_images", "source_footer"],
    "image": ["per_post", "workers"],
    "naver": ["native_schedule", "pattern", "slots", "weekend_slots", "category", "input_mode", "step_delay"],
    "effort": ["cards", "post"],
}
LOCAL_CFG = config.ROOT / "aside.config.local.json"


@app.get("/api/settings")
def get_settings() -> Dict[str, Any]:
    cfg = config.load()
    return {"values": {sec: {k: cfg[sec].get(k) for k in keys} for sec, keys in EDITABLE.items()},
            "patterns": [{"key": k, "label": v["label"]} for k, v in schedule.PATTERNS.items()]}


class Settings(BaseModel):
    values: Dict[str, Dict[str, Any]]


@app.put("/api/settings")
def put_settings(body: Settings) -> Dict[str, Any]:
    """이 PC 에만 적용(aside.config.local.json)."""
    cur = config.read_json(LOCAL_CFG, {}) or {}
    base = config.deep_merge(config.DEFAULTS, config.read_json(config.ROOT / "aside.config.json", {}) or {})
    for sec, vals in body.values.items():
        if sec not in EDITABLE:
            raise HTTPException(400, f"고칠 수 없는 칸: {sec}")
        for k, v in vals.items():
            if k not in EDITABLE[sec]:
                raise HTTPException(400, f"고칠 수 없는 칸: {sec}.{k}")
            if v == base.get(sec, {}).get(k):
                cur.get(sec, {}).pop(k, None)
            else:
                cur.setdefault(sec, {})[k] = v
    cur = {k: v for k, v in cur.items() if v}
    config.write_json(LOCAL_CFG, cur)
    return {"ok": True}


@app.get("/api/jobs/{name}/meta")
def get_meta(name: str) -> Dict[str, Any]:
    return config.read_json(_job(name).dir / "job.json", {}) or {}


@app.put("/api/jobs/{name}/meta")
def put_meta(name: str, body: Dict[str, Any]) -> Dict[str, Any]:
    job = _job(name)
    cur = config.read_json(job.dir / "job.json", {}) or {}
    for k in ("title", "book", "series", "source", "category"):
        if k in body:
            cur[k] = str(body[k]).strip()
    config.write_json(job.dir / "job.json", cur)
    return {"ok": True}


class Pick(BaseModel):
    kind: str = "folder"      # folder(카드 폴더) | files(교재 PDF)
    path: str = ""


@app.post("/api/pick")
def pick(body: Pick) -> Dict[str, Any]:
    """Windows 기본 선택 창을 띄워 경로를 받는다(브라우저는 실제 경로를 못 준다)."""
    import json as _json
    from ..pick import classify
    if body.kind not in ("folder", "files"):
        raise HTTPException(400)
    if body.path:
        p = Path(body.path.strip().strip('"')).expanduser()
        if p.is_file() and p.suffix.lower() == ".pdf":
            return {"files": [str(p)], "kind": "pdf"}
        if not p.is_dir():
            raise HTTPException(400, f"폴더나 PDF 를 찾을 수 없어요: {p}")
        return classify(p)
    r = subprocess.run([child_python(), "-m", "aside_blog.pick", body.kind], cwd=str(config.ROOT),
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                       capture_output=True, text=True, encoding="utf-8", timeout=600,
                       env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"))
    if r.returncode != 0:
        raise HTTPException(501, "선택 창을 띄우지 못했어요. 경로를 직접 붙여 넣어 주세요.")
    return _json.loads(r.stdout.strip().splitlines()[-1])


class NewJob(BaseModel):
    name: str
    pdf: str = ""
    cards: str = ""
    title: str = ""
    category: str = ""


@app.post("/api/jobs")
def new_job(body: NewJob) -> Dict[str, Any]:
    """새 작업 — 하위 프로세스로 돌린다(PDF 글 뽑기·카드 굽기가 몇 초~몇십 초 걸린다)."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", body.name):
        raise HTTPException(400, "작업 이름은 영문·숫자·_- 만 써 주세요 (폴더 이름이 돼요).")
    if Job(body.name).exists():
        raise HTTPException(400, f"「{body.name}」 이름이 이미 있어요. 다른 이름을 써 주세요.")
    if not body.pdf and not body.cards:
        raise HTTPException(400, "교재 PDF 나 학습카드 폴더 중 하나는 골라 주세요.")
    args = ["new", body.name]
    for k in ("pdf", "cards", "title", "category"):
        if getattr(body, k):
            args += [f"--{k}", getattr(body, k)]
    RUN.start(args)
    return {"ok": True}


ALLOWED.add("new")


@app.delete("/api/jobs/{name}")
def delete_job(name: str) -> Dict[str, Any]:
    """작업 폴더를 통째로 지운다. 원본 교재·카드 폴더는 건드리지 않는다."""
    import shutil
    job = _job(name)
    if RUN.busy:
        raise HTTPException(409, "지금 다른 일을 하는 중이에요. 끝나면 다시 눌러 주세요.")
    pending = [k for k, v in job.state().items() if (v.get("scheduled") or {}).get("mode") == "queue" and not v.get("posted")]
    if pending:
        raise HTTPException(400, f"예약이 {len(pending)}건 걸려 있어요. 예약 탭에서 먼저 취소해 주세요.")
    shutil.rmtree(job.dir)
    return {"ok": True}


# ── 계정 ─────────────────────────────────────────────────────────────────────
class NewAcc(BaseModel):
    name: str
    blog_id: str = ""
    label: str = ""


@app.post("/api/accounts")
def add_account(body: NewAcc) -> Dict[str, Any]:
    try:
        return accounts.add(body.name, body.label, body.blog_id)
    except SystemExit as e:
        raise HTTPException(400, str(e))


@app.post("/api/accounts/{name}/use")
def use_account(name: str) -> Dict[str, Any]:
    accounts.set_current(name)
    return {"ok": True}


@app.post("/api/accounts/{name}/login")
def login(name: str) -> Dict[str, Any]:
    if RUN.busy:
        raise HTTPException(409, "지금 다른 일을 하는 중이에요. 끝나면 다시 눌러 주세요.")
    accounts.get(name)
    RUN.start(["login", "--account", name])
    return {"ok": True}


@app.get("/api/accounts/{name}/status")
def account_status(name: str) -> Dict[str, Any]:
    from .. import naver
    # ★ 글을 넣는 동안엔 같은 Chrome 에 두 번째로 붙지 않는다(aside-shorts 2026-10-05 실측: 작성 창이 사라짐)
    if RUN.busy and (RUN.cmd[:1] or [""])[0] in ("post", "plan", "queue", "probe", "login", "fill", "tabs"):
        return {"chrome": True, "logged_in": None, "note": "작업 중"}
    try:
        return naver.status(accounts.get(name))
    except Exception as e:      # noqa: BLE001
        return {"chrome": False, "logged_in": None, "note": str(e)[:120]}


@app.get("/api/accounts/{name}/tabs")
def account_tabs(name: str) -> List[Dict[str, Any]]:
    """「현재 탭 선택하기」 — 왼쪽 Chrome 에 열린 글쓰기 탭들."""
    from .. import naver
    if RUN.busy:
        raise HTTPException(409, "지금 다른 일을 하는 중이에요. 끝나면 다시 눌러 주세요.")
    try:
        return naver.list_tabs(accounts.get(name))
    except Exception as e:      # noqa: BLE001
        raise HTTPException(400, f"왼쪽 창을 읽지 못했어요: {str(e)[:100]}")


class Fill(BaseModel):
    job: str
    data_id: str
    account: str
    tab: int
    publish: bool = False


@app.post("/api/fill")
def fill(body: Fill) -> Dict[str, Any]:
    args = ["fill", "--job", body.job, "--only", body.data_id, "--account", body.account, "--tab", str(body.tab)]
    if body.publish:
        args.append("--publish")
    RUN.start(args)
    return {"ok": True}


# ── 예약 ─────────────────────────────────────────────────────────────────────
def _title(job: Job, cid: str) -> str:
    c = job.card(cid)
    p = job.post(cid) or {}
    return f"#{c.n:02d} {p.get('title') or c.title}" if c else cid


class Sched(BaseModel):
    job: str
    data_id: str
    when: str
    account: str
    native: bool = True


@app.post("/api/schedule")
def add_schedule(body: Sched) -> Dict[str, Any]:
    job = _job(body.job)
    when = schedule.round10(schedule.parse_when(body.when))
    if when < datetime.now():
        raise HTTPException(400, "이미 지난 시간이에요. 앞으로의 시간을 골라 주세요.")
    if body.native:
        RUN.start(["post", "--job", body.job, "--only", body.data_id, "--account", body.account,
                   "--at", when.strftime(schedule.FMT)])
        return {"ok": True, "mode": "native"}
    schedule.enqueue(job, body.data_id, when, body.account)
    return {"ok": True, "mode": "queue"}


@app.delete("/api/schedule/{name}/{data_id}")
def cancel_schedule(name: str, data_id: str) -> Dict[str, Any]:
    job = _job(name)
    st = job.state().get(data_id) or {}
    if (st.get("scheduled") or {}).get("mode") == "native":
        raise HTTPException(400, "네이버에 이미 예약 발행으로 걸린 글이에요 — 블로그 「글 관리」에서 예약을 바꾸거나 취소해 주세요")
    job.update_state(data_id, {"scheduled": None, "error": None})
    return {"ok": True}


@app.get("/api/schedule")
def list_schedule() -> List[Dict[str, Any]]:
    out = []
    for job in all_jobs():
        for cid, st in job.state().items():
            sc = st.get("scheduled") or {}
            if sc.get("when") or st.get("posted"):
                out.append({"job": job.name, "data_id": cid, "title": _title(job, cid),
                            "when": sc.get("when") or (st.get("posted") or {}).get("at", "")[:16].replace("T", " "),
                            "mode": sc.get("mode"), "account": sc.get("account") or (st.get("posted") or {}).get("account"),
                            "posted": bool(st.get("posted")), "url": (st.get("posted") or {}).get("url"),
                            "error": st.get("error")})
    return sorted(out, key=lambda r: r["when"])


class Plan(BaseModel):
    job: str
    account: str
    apply: bool = False
    ids: List[str] = []
    pattern: str = ""
    start: str = ""


@app.get("/api/patterns")
def patterns() -> Dict[str, Any]:
    cur = config.load()["naver"].get("pattern") or "2-lunch"
    return {"current": cur, "items": [{"key": k, **v} for k, v in schedule.PATTERNS.items()]}


@app.get("/api/jobs/{name}/ready")
def ready(name: str) -> List[Dict[str, Any]]:
    from ..cli import _ready
    job = _job(name)
    out = []
    for cid in _ready(job):
        cover = job.cover_path(cid)
        out.append({"id": cid, "title": _title(job, cid), "cover": job.url(cover) if cover else ""})
    return out


@app.post("/api/plan")
def plan(body: Plan) -> List[Dict[str, Any]]:
    """체크한 글(없으면 전부)을 패턴의 다음 빈 시각에 차례로 — 추천 미리보기. apply 면 실제로 건다."""
    from ..cli import _ready
    job = _job(body.job)
    left = _ready(job)
    if body.ids:
        left = [s for s in left if s in set(body.ids)]
    start = schedule.parse_when(body.start + " 00:00") if body.start else None
    slots = schedule.next_slots(len(left), body.account, start=start, pat=body.pattern or None)
    out = [{"data_id": cid, "title": _title(job, cid), "when": when.strftime(schedule.FMT),
            "dow": "월화수목금토일"[when.weekday()]} for cid, when in zip(left, slots)]
    if body.apply and out:
        args = ["plan", "--job", body.job, "--account", body.account, "--apply", "--only", ",".join(left)]
        if body.pattern:
            args += ["--pattern", body.pattern]
        if body.start:
            args += ["--start", body.start]
        RUN.start(args)
    return out


# ── 띄우기 ───────────────────────────────────────────────────────────────────
def _open_panel(port: int) -> None:
    """오른쪽 좁은 앱창. 뜬 뒤 화면 오른쪽 끝에 정확히 붙인다(chrome.place)."""
    from ..chrome import chrome_path, pick_panel_port, place, port_open, screen, wait_port
    width = int(config.load()["ui"]["width"])
    url = f"http://127.0.0.1:{port}/panel"
    pport = pick_panel_port(port)
    if port_open(pport):
        try:
            place(pport, "right")
        except Exception:
            pass
        return
    sw, sh = screen()
    try:
        subprocess.Popen([chrome_path(), f"--app={url}", f"--user-data-dir={config.PROFILES / '_panel'}",
                          f"--remote-debugging-port={pport}", "--no-first-run", "--no-default-browser-check",
                          f"--window-position={max(0, sw - width)},0", f"--window-size={width},{sh - 40}"])
    except SystemExit:
        webbrowser.open(url)
        return
    if wait_port(pport, 40):
        time.sleep(0.8)
        try:
            place(pport, "right")
        except Exception as e:      # noqa: BLE001
            log(f"패널 배치 실패(무시): {e}")


def _open_dashboard(port: int) -> None:
    from ..chrome import chrome_path, free_port, screen
    url = f"http://127.0.0.1:{port}/"
    sw, sh = screen()
    w, h = min(1500, sw - 80), min(980, sh - 80)
    try:
        dport = free_port(int(config.load()["naver"]["base_port"]) - 80)
        data = config.local()
        data["dash_port"] = dport
        config.save_local(data)
        subprocess.Popen([chrome_path(), f"--app={url}", f"--user-data-dir={config.PROFILES / '_dash'}",
                          f"--remote-debugging-port={dport}", "--no-first-run", "--no-default-browser-check",
                          f"--window-position={max(0, (sw - w) // 2)},{max(0, (sh - h) // 3)}",
                          f"--window-size={w},{h}"])
    except SystemExit:
        webbrowser.open(url)


@app.post("/api/panel")
def open_panel_api() -> Dict[str, Any]:
    port = int(config.local().get("ui_port") or config.load()["ui"]["port"])
    threading.Thread(target=_open_panel, args=(port,), daemon=True).start()
    return {"ok": True}


class Arrange(BaseModel):
    account: str = ""


@app.post("/api/arrange")
def arrange_windows(body: Arrange) -> Dict[str, Any]:
    from ..chrome import arrange
    acc = None
    if body.account:
        try:
            acc = accounts.get(body.account)
        except SystemExit:
            acc = None
    if RUN.busy and (RUN.cmd[:1] or [""])[0] in ("post", "fill", "plan"):
        raise HTTPException(409, "글을 넣는 중에는 창을 옮기지 않아요")
    return {"done": arrange(acc)}


@app.post("/api/quit")
def quit_app() -> Dict[str, Any]:
    RUN.stop()

    def _bye():
        time.sleep(0.6)
        from ..chrome import panel_port, port_open
        for pp in (panel_port(), config.local().get("dash_port")):
            try:
                if pp and port_open(int(pp)):
                    urllib.request.urlopen(urllib.request.Request(
                        f"http://127.0.0.1:{pp}/json/close/" + _panel_target(int(pp)), method="PUT"), timeout=2)
            except Exception:
                pass
        os._exit(0)

    threading.Thread(target=_bye, daemon=True).start()
    return {"ok": True}


def _panel_target(pp: int) -> str:
    import json as _json
    tabs = _json.load(urllib.request.urlopen(f"http://127.0.0.1:{pp}/json/list", timeout=2))
    return next(t["id"] for t in tabs if t.get("type") == "page")


def _quiet_stdio() -> None:
    if sys.stdout is None or sys.stderr is None:
        config.LOGS.mkdir(parents=True, exist_ok=True)
        f = open(config.LOGS / "server.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stdout or f
        sys.stderr = sys.stderr or f


def _is_aside(port: int) -> bool:
    import json as _json
    try:
        return _json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1)).get("app") == APP
    except Exception:
        return False


def child_python() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return sys.executable


def serve(window: bool = True) -> None:
    import uvicorn
    from ..chrome import port_open
    _quiet_stdio()
    want = int(os.environ.get("ASIDE_PORT") or config.load()["ui"]["port"])
    port = want
    for cand in range(want, want + 20):
        if not port_open(cand):
            port = cand
            break
        if _is_aside(cand):
            log(f"이미 켜져 있어요. 대시보드를 다시 열어요. (주소 http://127.0.0.1:{cand}/)")
            if window:
                _open_dashboard(cand)
            return
    else:
        raise SystemExit(f"{want}~{want + 19} 포트가 모두 쓰이고 있어요. 다른 프로그램을 몇 개 닫고 다시 켜 주세요.")
    if port != want:
        log(f"{want} 번은 다른 프로그램이 쓰고 있어서 {port} 번으로 켰어요.")
    data = config.local()
    data["ui_port"] = port
    config.save_local(data)
    threading.Thread(target=_scheduler, daemon=True).start()
    if window:
        threading.Timer(1.2, _open_dashboard, args=(port,)).start()
    log(f"aside-blog 가 켜졌어요. 이 검은 창을 닫으면 예약이 멈춰요 (최소화는 괜찮아요). 대시보드: http://127.0.0.1:{port}/")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", log_config=None)
