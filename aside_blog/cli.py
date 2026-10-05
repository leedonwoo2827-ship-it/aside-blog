"""python -m aside_blog <명령> …   (run.bat <명령> … 과 같다)

교재·카드
  new <job> [--pdf 교재.pdf] [--cards 카드폴더] [--title …] [--category …]
            --pdf 만: 교재에서 카드를 새로 만든다(extract → s0-cards).
            --cards 만: 이미 만든 학습카드 폴더를 그대로 불러온다(LLM 없음).
            둘 다: 교재를 원본으로 붙이고, 카드 폴더를 그 교재의 결과물로 **강제로** 넣는다(할당량 0).
  extract   --job J                       교재 PDF → 조각·목차 (s0 전에 목차 확인)
  s0-cards  --job J [--only] [--force]    조각 → 학습카드 JSON (Codex) · 카드 폴더면 다시 읽기
  render    --job J                       카드 JSON → 학습카드 HTML + 00_목차 (LLM 없음)
블로그 글
  make      --job J [--only id,…] [--force] [--limit N] [--skip-images]   s1→s2→s3→s4 한 번에
  s1-post | s2-imgjson | s3-images | s4-assets   (단계별, 옵션 같음)  s1-post --offline = Codex 없이 카드 그대로
올리기
  account add <이름> [--blog-id dekman] [--label …] | account list | account use <이름> | account rm <이름>
  login   [--account A]                 왼쪽 Chrome 에서 네이버 로그인
  post    --job J --only id [--account A] [--at "2026-10-06 19:00"] [--queue] [--dry-run] [--force]
  tabs    [--account A]                 열려 있는 글쓰기 탭 목록 (영상식 반자동)
  fill    --job J --only id --tab N [--publish] [--at …]   그 탭에 글 넣기
  plan    --job J [--account A] [--only …] [--pattern 2-lunch] [--start 2026-10-06] [--apply]
  queue                                 시각이 된 대기열 게시(서버가 30초마다 부른다)
  probe   [--account A]                 글쓰기·발행 창 구조 떠 두기(셀렉터 맞출 때, 발행 안 함)
기타
  ui [--no-window] · fonts · doctor · codex-login
"""
from __future__ import annotations

import argparse
import shutil
import sys
import urllib.request
from pathlib import Path
from typing import List, Optional

from . import accounts, config, schedule
from .job import Job, all_jobs, base_id, need
from .log import detail, log, usage

STAGES = ["s1-post", "s2-imgjson", "s3-images", "s4-assets"]
STEP_NAMES = {"s1-post": "1/4 글 쓰기", "s2-imgjson": "2/4 그림 설명 준비",
              "s3-images": "3/4 그림 그리기", "s4-assets": "4/4 카드 그림·표지 만들기",
              "s0-cards": "학습카드 만들기", "render": "학습카드 HTML 굽기", "extract": "교재에서 글 뽑기"}
FONT_URL = "https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/static/woff2/Pretendard-{w}.woff2"


def _only(v: Optional[str]) -> Optional[List[str]]:
    return [x.strip() for x in v.split(",") if x.strip()] if v else None


def run_stage(name: str, job: Job, **kw) -> None:
    from . import s0_cards, s1_post, s2_imgjson, s3_images, s4_assets
    from .cards import extract, render
    mod = {"s1-post": s1_post, "s2-imgjson": s2_imgjson, "s3-images": s3_images, "s4-assets": s4_assets,
           "s0-cards": s0_cards, "render": render, "extract": extract}[name]
    log(f"── {STEP_NAMES.get(name, name)}")
    detail(f"stage {name} [{job.name}] {kw}")
    if name == "s1-post" and kw.pop("offline", False):
        s1_post.offline(job, **kw)
        return
    kw.pop("offline", None)
    mod.run(job, **kw)


# ── 새 작업 ──────────────────────────────────────────────────────────────────
def cmd_new(a) -> None:
    import hashlib
    from . import s0_cards
    from .cards import extract, render
    if not a.pdf and not a.cards:
        raise SystemExit("교재 PDF(--pdf) 나 학습카드 폴더(--cards) 중 하나는 주세요")
    pdf = Path(a.pdf).resolve() if a.pdf else None
    folder = Path(a.cards).resolve() if a.cards else None
    if pdf and not pdf.is_file():
        raise SystemExit(f"교재 PDF 가 없어요: {pdf}")
    if folder and not folder.is_dir():
        raise SystemExit(f"카드 폴더가 없어요: {folder}")
    job = Job(a.name)
    if job.exists() and not a.force:
        raise SystemExit(f"「{a.name}」 이름이 이미 있어요. 다른 이름을 써 주세요.")
    job.dir.mkdir(parents=True, exist_ok=True)
    meta = config.read_json(job.dir / "job.json", {}) or {}
    meta.update({k: v for k, v in {"title": a.title, "book": a.book, "series": a.series, "source": a.source,
                                   "category": a.category}.items() if v})
    meta["kind"] = "pdf" if pdf else "cards"
    if pdf:
        meta["pdf"] = str(pdf)
    if folder:
        meta["cards_dir"] = str(folder)
    meta.setdefault("title", a.title or a.name)
    config.write_json(job.dir / "job.json", meta)
    job = Job(a.name)
    pdf_man = extract.run(job, pdf) if pdf else []
    if folder:
        s0_cards.import_folder(job, folder)
        if pdf_man:
            # 교재 조각과 카드를 잇는다 — 카드가 있는 조각은 「이미 만듦」으로 표시(할당량 0)
            card_man = {m["id"]: m for m in job.manifest}
            merged = [{**m, **{k: card_man[m["id"]][k] for k in ("chapter_name", "sec_name") if m["id"] in card_man}}
                      for m in pdf_man]
            config.write_json(job.source / "manifest.json", merged)
            hashes = {m["id"]: hashlib.sha1((job.source / "chunks" / f"{m['id']}.txt").read_text(encoding="utf-8")
                                            .encode("utf-8")).hexdigest()[:12] for m in pdf_man}
            linked = 0
            for f in job.card_json.glob("*.json"):
                d = config.read_json(f)
                h = hashes.get(base_id(d["id"]))
                if h:
                    d["_chunk_hash"] = h
                    config.write_json(f, d)
                    linked += 1
            left = [m["id"] for m in pdf_man if m["id"] not in card_man]
            if left:        # 카드 폴더가 일부러 뺀 조각(절 머리말 등) — 새로 만들면 카드 번호가 밀린다
                meta = config.read_json(job.dir / "job.json", {})
                meta["skip_chunks"] = left
                config.write_json(job.dir / "job.json", meta)
            log(f"교재와 카드를 이었어요: 카드 {linked}장이 교재 조각에 연결됐어요."
                + (f" 카드 폴더에 없는 짧은 조각 {len(left)}개({', '.join(left)})는 건너뛰도록 표시했어요." if left else ""))
        job = Job(a.name)
        render.run(job)
    else:
        log("다음: 목차가 맞으면 「학습카드 만들기」를 눌러 주세요 (Codex 할당량을 써요).")
    log(f"✓ 작업 「{a.name}」을 만들었어요.")


def cmd_fonts(_a=None) -> None:
    d = config.TEMPLATES / "fonts"
    d.mkdir(parents=True, exist_ok=True)
    for w in ("Regular", "Medium", "Bold", "ExtraBold"):
        p = d / f"Pretendard-{w}.woff2"
        if p.exists():
            continue
        log(f"  글꼴 받는 중 ({w})")
        urllib.request.urlretrieve(FONT_URL.format(w=w), p)
    log("글꼴 준비 완료")


def cmd_doctor(_a=None) -> int:
    bad = 0
    from .llm import codex_transport as t
    from .llm.codex_provider import CodexProvider
    if not t.AUTH_PATH.exists():
        log("✗ Codex 로그인이 필요해요 — 「Codex 로그인」 버튼을 눌러 주세요")
        bad += 1
    else:
        try:
            from .llm import models
            m = models.resolve()
            ok, msg = CodexProvider(model=m, effort="low").ping()
            log("✓ Codex 연결 정상" + (f" (남은 사용량 {usage().split()[-1]})" if usage() else "") if ok
                else "✗ Codex 연결이 안 돼요 — 「Codex 로그인」을 다시 해 주세요")
            detail(f"doctor codex: {m} · {msg}  {t.limits_line()}")
            bad += 0 if ok else 1
        except Exception as e:      # noqa: BLE001
            log("✗ Codex 연결이 안 돼요 — 「Codex 로그인」을 다시 해 주세요")
            detail(f"doctor codex 실패: {e}")
            bad += 1
    try:
        from .chrome import chrome_path
        detail(f"Chrome: {chrome_path()}")
        log("✓ Chrome 확인")
    except SystemExit as e:
        log(f"✗ {e}")
        bad += 1
    log("✓ 교재 글 뽑기 준비됨" if shutil.which("pdftotext") else "✓ 교재 글 뽑기 준비됨 (기본 방식)")
    fonts = config.TEMPLATES / "fonts" / "Pretendard-Bold.woff2"
    log("✓ 글꼴 확인" if fonts.exists() else "△ 글꼴이 없어요 — setup 을 다시 실행해 주세요")
    accs = accounts.all_()
    log(f"✓ 네이버 계정 {len(accs)}개" if accs else "△ 네이버 계정이 아직 없어요 — 「올리기」 탭에서 추가해 주세요")
    return bad


def cmd_codex_login(_a=None) -> int:
    """`codex login` 을 대신 돌린다 (aside-threads 그대로). 로그인 주소 줄은 패널이 버튼으로 바꾼다."""
    import re
    import subprocess
    from .llm import codex_auth
    exe = codex_auth.codex_path()
    if not exe:
        log("✗ Codex 가 설치되어 있지 않아요. setup 을 다시 실행하거나 담당자에게 문의해 주세요.")
        return 1
    had = codex_auth.backup()
    log("브라우저에 ChatGPT 로그인 화면이 열려요. 회사 ChatGPT 계정으로 로그인하고 「계속」을 눌러 주세요.")
    log("  (5분 안에 해 주세요. 브라우저가 안 열리면 아래에 나오는 「로그인 페이지 열기」를 누르세요)")
    proc = subprocess.Popen([exe, "login"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
    shown = False
    try:
        for line in proc.stdout:
            line = line.strip()
            detail(f"codex login: {line}")
            m = re.search(r"https://auth\.openai\.com/\S+", line)
            if m and not shown:
                log(f"LOGIN_URL {m.group(0)}")
                shown = True
        code = proc.wait(timeout=300)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        log("✗ 시간이 지나 로그인을 멈췄어요. " + ("원래 쓰던 로그인은 그대로 두었어요." if codex_auth.restore_if_needed()
                                          else "「Codex 로그인」을 다시 눌러 주세요."))
        return 1
    st = codex_auth.status()
    if code == 0 and st["authenticated"]:
        data = config.local()
        data.pop("codex_model", None)
        data.pop("codex_image_model", None)
        config.save_local(data)
        codex_auth.drop_backup()
        log(f"✓ Codex 로그인 완료 ({st['email'] or 'ChatGPT 계정'})")
        return 0
    log("✗ 로그인이 끝나지 않았어요. " + ("원래 쓰던 로그인은 그대로 두었어요." if had and codex_auth.restore_if_needed()
                                    else "「Codex 로그인」을 다시 눌러 주세요."))
    return 1


def cmd_account(a) -> None:
    if a.action == "add":
        acc = accounts.add(a.name, a.label or "", a.blog_id or "")
        log(f"계정 추가: {acc['name']} (blog.naver.com/{acc['blog_id']}) — 이제 「로그인 창 열기」")
    elif a.action == "rm":
        accounts.remove(a.name)
        log(f"계정 목록에서 뺐어요: {a.name} (profiles/{a.name}/ 폴더는 남겨 둠)")
    elif a.action == "use":
        accounts.set_current(a.name)
        log(f"기본 계정: {a.name}")
    else:
        cur = config.local().get("current")
        for acc in accounts.all_():
            log(f"{'*' if acc['name'] == cur else ' '} {acc['name']:16} {acc.get('label', '')}  blog {acc.get('blog_id')}  port {acc['port']}")


def _ready(job: Job) -> List[str]:
    """예약·게시할 수 있는 글 — 글 있음 · 안 올림 · 예약 안 됨. 카드 순서."""
    st = job.state()
    return [c.id for c in job.cards if job.raw_post(c.id)
            and not (st.get(c.id) or {}).get("posted") and not (st.get(c.id) or {}).get("scheduled")]


def cmd_post(a) -> None:
    from . import naver
    job = need(a.job)
    acc = accounts.get(a.account)
    cfg = config.load()["naver"]
    targets = job.pick(_only(a.only)) if a.only else []
    if not targets:
        raise SystemExit("올릴 글 하나를 골라 주세요")
    for c in targets:
        st = job.state().get(c.id) or {}
        if st.get("posted") and not a.force and not a.dry_run:
            log(f"· #{c.n:02d} 「{c.title}」은 이미 올렸어요.")
            continue
        post = job.post(c.id)
        if not post:
            raise SystemExit(f"#{c.n:02d} 「{c.title}」: 글이 아직 없어요 — 먼저 「딸깍 만들기」")
        if not job.cover_path(c.id):
            raise SystemExit(f"#{c.n:02d} 「{c.title}」: 대표이미지가 없어요 — 먼저 「카드 그림·표지 만들기」")
        when = schedule.round10(schedule.parse_when(a.at)) if a.at else None
        if when and (a.queue or not cfg.get("native_schedule")):
            schedule.enqueue(job, c.id, when, acc["name"], "queue")
            log(f"⏰ 「{c.title}」을 {when:%m월 %d일 %H:%M} 에 올리도록 예약했어요. (이 프로그램이 켜져 있어야 올라가요)")
            continue
        log(f"▶ #{c.n:02d} 「{post['title']}」 " + ("미리 채워 보기" if a.dry_run else "예약 넣기" if when else "올리기"))
        detail(f"post {c.id} account={acc['name']} when={when} dry={a.dry_run}")
        try:
            res = naver.publish(acc, job, post, when=when, dry_run=a.dry_run)
        except naver.ScheduleUnsupported as e:
            detail(f"native schedule 실패: {e}")
            schedule.enqueue(job, c.id, when, acc["name"], "queue")
            log("△ 네이버 예약 칸을 다루지 못해서, 이 프로그램의 예약으로 걸어 두었어요. "
                "(작성 중인 글은 왼쪽 창에 남아 있어요 — 닫아도 돼요)")
            continue
        except naver.PostError as e:
            job.update_state(c.id, {"error": str(e)[:300]})
            detail(f"post 실패 {c.id}: {e}  (스크린샷: logs/post/)")
            raise SystemExit(f"✗ 올리지 못했어요: {e}")
        if res.get("dry_run"):
            continue
        rec = {"account": acc["name"], **res}
        if when:
            job.update_state(c.id, {"scheduled": {"when": when.strftime(schedule.FMT), "account": acc["name"],
                                                  "mode": "native"}, "native": rec, "error": None})
            log(f"✓ 네이버에 예약 발행을 걸었어요 ({when:%m월 %d일 %H:%M})")
        else:
            job.update_state(c.id, {"posted": rec, "scheduled": None, "error": None})
            log("✓ 발행했어요! 왼쪽 창에서 확인해 보세요." + (f" {res['url']}" if res.get("url") else ""))


def cmd_fill(a) -> None:
    from . import naver
    job = need(a.job)
    acc = accounts.get(a.account)
    c = job.pick(_only(a.only))[0]
    post = job.post(c.id)
    if not post:
        raise SystemExit("글이 아직 없어요 — 먼저 「딸깍 만들기」")
    when = schedule.round10(schedule.parse_when(a.at)) if a.at else None
    log(f"▶ 고른 글쓰기 창에 넣어요 — #{c.n:02d} 「{post['title']}」")
    try:
        res = naver.fill_tab(acc, job, post, int(a.tab), publish_now=a.publish, when=when)
    except naver.PostError as e:
        raise SystemExit(f"✗ 넣지 못했어요: {e}")
    if res.get("url") or res.get("log_no") or res.get("scheduled_for"):
        key = "scheduled" if when else "posted"
        job.update_state(c.id, {key: ({"when": when.strftime(schedule.FMT), "account": acc["name"], "mode": "native"}
                                      if when else {"account": acc["name"], **res}), "error": None})


def cmd_tabs(a) -> None:
    from . import naver
    tabs = naver.list_tabs(accounts.get(a.account))
    if not tabs:
        log("열려 있는 네이버 글쓰기 창이 없어요. 왼쪽 창에서 「글쓰기」를 눌러 주세요.")
    for t in tabs:
        log(f"TAB {t['index']} {t['title'] or t['url']}")


def cmd_plan(a) -> None:
    job = need(a.job)
    acc = accounts.get(a.account)
    left = _ready(job)
    if a.only:
        want = set(_only(a.only))
        left = [x for x in left if x in want]
    start = schedule.parse_when(a.start + " 00:00") if a.start else None
    slots = schedule.next_slots(len(left), acc["name"], start=start, pat=a.pattern or None)
    native = config.load()["naver"].get("native_schedule")
    for cid, when in zip(left, slots):
        c = job.card(cid)
        log(f"  {when:%m-%d}({'월화수목금토일'[when.weekday()]}) {when:%H:%M}  #{c.n:02d} {c.title}")
    if not a.apply:
        log(f"미리보기예요 — 적용하려면 「예약 걸기」 ({len(left)}건, 계정 {acc['name']})")
        return
    ok = 0
    for cid, when in zip(left, slots):
        if native:
            ns = argparse.Namespace(job=job.name, only=cid, account=acc["name"], at=when.strftime(schedule.FMT),
                                    queue=False, dry_run=False, force=False)
            try:
                cmd_post(ns)
                ok += 1
            except SystemExit as e:     # 한 편이 실패해도 다음 편으로
                log(f"  ✗ {cid}: {e}")
        else:
            schedule.enqueue(job, cid, when, acc["name"])
            ok += 1
    log(f"예약 {ok}건을 걸었어요 ({'네이버 예약 발행' if native else '이 프로그램의 예약'}).")


def cmd_queue(_a=None) -> None:
    items = schedule.due()
    if not items:
        return
    it = items[0]       # 한 번에 하나 — 몰아올리지 않는다
    log("⏰ 예약 시간이 되어 올려요")
    detail(f"queue due: {it['job']}/{it['data_id']} ({it['account']})")
    Job(it["job"]).update_state(it["data_id"], {"scheduled": None})
    ns = argparse.Namespace(job=it["job"], only=it["data_id"], account=it["account"], at=None,
                            queue=False, dry_run=False, force=False)
    cmd_post(ns)


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="aside_blog", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("new")
    p.add_argument("name")
    p.add_argument("--pdf")
    p.add_argument("--cards")
    for k in ("title", "book", "series", "source", "category"):
        p.add_argument(f"--{k}")
    p.add_argument("--force", action="store_true", help="같은 이름 작업을 덮어쓰기")

    for name in ["make", *STAGES, "s0-cards", "render", "extract"]:
        p = sub.add_parser(name)
        p.add_argument("--job")
        p.add_argument("--only")
        p.add_argument("--force", action="store_true")
        p.add_argument("--limit", type=int, default=0, help="s3: 이번에 구울 최대 장수")
        p.add_argument("--skip-images", action="store_true", help="make: Codex 그림 건너뛰기")
        p.add_argument("--offline", action="store_true", help="s1/make: Codex 없이 카드 그대로 글 조립")

    p = sub.add_parser("account")
    p.add_argument("action", choices=["add", "list", "use", "rm"])
    p.add_argument("name", nargs="?")
    p.add_argument("--label")
    p.add_argument("--blog-id")

    for name in ("login", "probe", "tabs"):
        p = sub.add_parser(name)
        p.add_argument("--account")

    p = sub.add_parser("post")
    p.add_argument("--job")
    p.add_argument("--only")
    p.add_argument("--account")
    p.add_argument("--at")
    p.add_argument("--queue", action="store_true", help="네이버 예약 대신 이 프로그램이 시각에 올리기")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")

    p = sub.add_parser("fill")
    p.add_argument("--job")
    p.add_argument("--only", required=True)
    p.add_argument("--account")
    p.add_argument("--tab", required=True)
    p.add_argument("--publish", action="store_true")
    p.add_argument("--at")

    p = sub.add_parser("plan")
    p.add_argument("--job")
    p.add_argument("--account")
    p.add_argument("--only")
    p.add_argument("--pattern")
    p.add_argument("--start")
    p.add_argument("--apply", action="store_true")

    sub.add_parser("queue")
    p = sub.add_parser("ui")
    p.add_argument("--no-window", action="store_true")
    sub.add_parser("fonts")
    sub.add_parser("doctor")
    sub.add_parser("codex-login")

    a = ap.parse_args(argv)
    try:
        return _dispatch(a, ap)
    except SystemExit:
        raise
    except Exception as e:      # noqa: BLE001 — 팀원 화면에는 쉬운 말, 자세한 건 기록 파일로
        import traceback
        detail(traceback.format_exc())
        raise SystemExit(friendly(e))


def friendly(e: Exception) -> str:
    """기술 오류 → 쉬운 말 한 줄. 원문은 logs/detail.log 에."""
    from .llm.codex_transport import CodexAuthError
    from .llm.errors import NotAuthenticated, QuotaExceeded
    if isinstance(e, (NotAuthenticated, CodexAuthError)):
        return "✗ Codex 로그인이 필요해요. 「Codex 로그인」을 누른 뒤 같은 버튼을 다시 눌러 주세요."
    if isinstance(e, QuotaExceeded):
        return "✗ 오늘 쓸 수 있는 양을 다 썼어요. 나중에 같은 버튼을 누르면 남은 것만 이어서 해요."
    msg = str(e).lower()
    if any(w in msg for w in ("timed out", "timeout", "connection", "network", "urlopen")):
        return "✗ 인터넷 연결이 불안정해요. 잠시 뒤 같은 버튼을 다시 눌러 주세요."
    return "✗ 문제가 생겨서 멈췄어요. 같은 버튼을 한 번 더 눌러 보시고, 또 그러면 담당자에게 logs/detail.log 를 보내 주세요."


def _dispatch(a, ap) -> int:
    if a.cmd == "new":
        cmd_new(a)
    elif a.cmd in STAGES or a.cmd in ("make", "s0-cards", "render", "extract"):
        job = need(a.job)
        kw = dict(only=_only(a.only), force=a.force, limit=a.limit, offline=a.offline)
        if a.cmd in ("s0-cards", "render", "extract"):
            run_stage(a.cmd, job, **({} if a.cmd != "s0-cards" else {"only": kw["only"], "force": a.force}))
            return 0
        for st in (STAGES if a.cmd == "make" else [a.cmd]):
            if a.cmd == "make" and st == "s3-images" and (a.skip_images or a.offline):
                continue
            run_stage(st, Job(job.name), **kw)
    elif a.cmd == "account":
        if a.action in ("add", "use", "rm") and not a.name:
            raise SystemExit("계정 이름을 주세요")
        cmd_account(a)
    elif a.cmd == "login":
        from . import naver
        naver.open_login(accounts.get(a.account))
    elif a.cmd == "probe":
        from . import naver
        naver.probe(accounts.get(a.account))
    elif a.cmd == "tabs":
        cmd_tabs(a)
    elif a.cmd == "post":
        cmd_post(a)
    elif a.cmd == "fill":
        cmd_fill(a)
    elif a.cmd == "plan":
        cmd_plan(a)
    elif a.cmd == "queue":
        cmd_queue()
    elif a.cmd == "ui":
        from .web import server
        server.serve(window=not a.no_window)
    elif a.cmd == "fonts":
        cmd_fonts()
    elif a.cmd == "codex-login":
        return cmd_codex_login()
    elif a.cmd == "doctor":
        return 1 if cmd_doctor() else 0
    else:
        ap.print_help()
    return 0
