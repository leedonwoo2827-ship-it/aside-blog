"""네이버 블로그 글쓰기 — 실제 Chrome(전용 프로필) + CDP. aside-shorts `youtube.py` 와 같은 방식.

사람이 왼쪽 Chrome 에서 네이버에 **한 번** 로그인하면, 이후로는 그 창에 붙어서
  글쓰기 열기 → (작성 중 글 팝업 닫기) → 제목 → 본문(글·그림 차례대로) → 발행 창(카테고리·전체공개·태그·현재/예약) → 발행
을 눈앞에서 한다. 레퍼런스 영상(딸깍 SNS 5:11~7:35)의 「선택된 글쓰기창에 넣기」 → 「발행」 흐름과 같다.

스마트에디터 ONE 은 blog.naver.com 페이지 안의 #mainFrame(iframe) 에 뜬다. 화면이 바뀌면 **SEL·TEXT 표만 고친다.**
`run.bat probe` 가 에디터·사진 창·발행 창 구조와 스크린샷을 logs/probe/ 에 떨군다 — 아무것도 발행하지 않는다.

본문 넣기 두 방식(naver.input_mode):
  paste  글 덩어리(소제목·문단·글머리)를 서식 있는 HTML 로 한 번에 붙여넣는다(사람이 웹에서 복사해 붙이는 것과 같다).
  type   한 줄씩 타자. 굵게는 Ctrl+B. 붙여넣기가 안 먹으면 그 덩어리만 자동으로 이 방식으로 다시 한다.
그림은 툴바 「사진」 → 파일 선택 창에 파일을 넣는다(안 되면 그림 붙여넣기).
"""
from __future__ import annotations

import base64
import html as _html
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import accounts, config
from .chrome import Session, ensure_chrome, place, port_open
from .log import detail, log

HOME = "https://blog.naver.com/{blog}"
WRITE = "https://blog.naver.com/{blog}?Redirect=Write&"
LOGIN = "https://nid.naver.com/nidlogin.login?url=https%3A%2F%2Fblog.naver.com%2F{blog}"

# ── 셀렉터 — 화면이 바뀌면 여기만 고친다 ───────────────────────────────────
# ★ 아직 실측 전(2026-10-05). 로그인 후 `run.bat probe` 결과(logs/probe/)를 보고 맞춘다.
#   발행 창의 클래스 이름에는 해시가 붙는다(publish_btn__m9KHH 꼴) → [class*='…'] + 글자(TEXT) 로 같이 찾는다.
SEL: Dict[str, Any] = {
    "frame": "mainFrame",                                   # iframe name
    "editor": ".se-content, .se-wrap, #SE-root",
    "draft_cancel": ".se-popup-button-cancel",              # 「작성 중인 글이 있습니다」 → 취소(새 글)
    "help_close": ".se-help-panel-close-button, button[class*='close'][class*='help']",
    "title": ".se-documentTitle .se-text-paragraph, .se-title-text .se-text-paragraph",      # 실측 ✓
    # ★ 2026-10-05 실측: 본문 상자는 article.se-components-wrap (예전 이름 .se-main-container 는 없음)
    "body_para": ".se-components-wrap .se-component.se-text .se-text-paragraph",
    "component": ".se-components-wrap .se-component:not(.se-documentTitle)",
    "image_comp": ".se-components-wrap .se-component.se-image",
    "photo_btn": "button.se-image-toolbar-button",                                          # 실측 ✓ 「사진」
    "photo_file": "input[type=file][accept*='image'], input#hidden-file",
    "quote_btn": "button.se-insert-quotation-default-toolbar-button",                      # 실측 ✓
    "publish_open": "button[class*='publish_btn']",                                          # 실측 ✓ publish_btn__v_kS9
    "layer": "[class*='layer_publish'], [class*='publish_layer'], [class*='option_publish']",
    "category_btn": "[class*='selectbox_button'], button[aria-label*='카테고리'], [class*='category'] button",
    "category_item": "[class*='option_list'] label, [class*='option_list'] li, [class*='category'] [role='option'], [class*='item'] label",
    # ★ 라디오는 label 이 클릭을 가로챈다(2026-10-05 실측) → label 을 먼저
    "open_public": "label[for='open_public'], label:has-text('전체공개')",
    "tag_input": "#tag-input, input[placeholder*='태그']",
    "time_now": "label[for='radio_time1']",                                                 # 실측 ✓
    "time_reserve": "label[for='radio_time2']",
    "date_input": "[class*='input_date'], input[class*='date']",
    "dp_title": ".ui-datepicker-title, [class*='datepicker'] [class*='title']",
    "dp_next": ".ui-datepicker-next, [class*='datepicker'] [class*='next']",
    "hour_sel": "select[class*='hour']",
    "minute_sel": "select[class*='minute']",
    "confirm": "button[class*='confirm_btn'], [class*='layer_btn'] button[class*='publish']",
    "post_url": re.compile(r"(?:blog\.naver\.com/[^/?#]+/(\d{9,})|logNo=(\d{9,}))"),
}
TEXT: Dict[str, Any] = {
    "publish": re.compile(r"^\s*발행\s*$"),
    "draft_cancel": re.compile(r"^(취소|새로 쓰기|새로쓰기)$"),
    "public": re.compile(r"^전체공개$"),
    "now": re.compile(r"^현재$"),
    "reserve": re.compile(r"^예약$"),
    "photo": re.compile(r"^사진$"),
    "quote": re.compile(r"^인용구$"),
}


class PostError(RuntimeError):
    pass


class ScheduleUnsupported(PostError):
    """발행 창의 예약 칸을 다루지 못함 — 호출부가 내장 대기열로 넘긴다."""


class NaverSession(Session):
    url_match = ("blog.naver.com",)


def blog_of(acc: Dict[str, Any]) -> str:
    return accounts.blog_id(acc)


# ── 에디터 찾기 ──────────────────────────────────────────────────────────────
def editor_frame(page, timeout: int = 30_000):
    """에디터가 든 프레임 — #mainFrame 이 있으면 그것, 없으면 페이지 자체(신형 주소)."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        for fr in [page.frame(name=SEL["frame"]), page.main_frame]:
            try:
                if fr is not None and fr.locator(SEL["editor"]).count():
                    return fr
            except Exception:
                pass
        time.sleep(0.5)
    raise PostError("글쓰기 화면이 열리지 않았어요. 왼쪽 창에서 네이버에 로그인되어 있는지 확인해 주세요.")


def _first_visible(fr, css: str, text=None, role: str = "button"):
    cands = [fr.locator(css)]
    if text is not None:
        cands += [fr.get_by_role(role, name=text), fr.get_by_text(text)]
    for loc in cands:
        try:
            n = loc.count()
        except Exception:
            continue
        for i in range(min(n, 8)):
            el = loc.nth(i)
            try:
                if el.is_visible():
                    return el
            except Exception:
                pass
    return None


def _click(fr, css: str, text=None, *, role: str = "button", timeout: int = 15_000) -> None:
    """css → (role, 이름) → 글자 순서로 찾아 누른다 (youtube._click 과 같은 규칙)."""
    deadline = time.time() + timeout / 1000
    while time.time() < deadline:
        el = _first_visible(fr, css, text, role)
        if el is not None:
            try:
                el.scroll_into_view_if_needed(timeout=3_000)
                el.click(timeout=5_000)
                return
            except Exception as e:      # noqa: BLE001
                detail(f"  클릭 재시도 {css}: {e}")
        time.sleep(0.4)
    raise PostError(f"화면에서 버튼을 찾지 못했어요 ({text.pattern if text is not None else css})")


def _try_click(fr, css: str, text=None, timeout: int = 2_500) -> bool:
    try:
        _click(fr, css, text, timeout=timeout)
        return True
    except PostError:
        return False


def _guard_dialogs(page) -> None:
    if getattr(page, "_aside_dialog", False):
        return

    def _on(d):
        try:
            detail(f"  확인 창: {d.type} {d.message[:80]}")
            # 「이 페이지를 나가시겠습니까」(beforeunload)는 나간다 — 쓰던 글은 네이버가 임시저장해 둔다.
            # 그 밖의 확인 창은 취소(글을 지키는 쪽)
            d.accept() if d.type == "beforeunload" else d.dismiss()
        except Exception:
            pass
    page.on("dialog", _on)
    page._aside_dialog = True


def dismiss_popups(fr) -> None:
    if _try_click(fr, SEL["draft_cancel"], TEXT["draft_cancel"], timeout=3_000):
        log("  작성 중이던 글 알림은 닫았어요 (새 글로 써요)")
    _try_click(fr, SEL["help_close"], None, timeout=1_500)


# ── 본문 → HTML (붙여넣기용) ─────────────────────────────────────────────────
def _md(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", _html.escape(text or ""))


def _plain(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")


def blocks_html(blocks: List[Dict[str, Any]]) -> str:
    out = []
    for b in blocks:
        t = b["type"]
        if t == "heading":
            out.append(f'<p><span style="font-size:19px"><b>{_md(b["text"])}</b></span></p>')
        elif t == "paragraph":
            out.append(f"<p>{_md(b['text'])}</p>")
        elif t == "small":
            out.append(f'<p><span style="font-size:13px;color:#8a909c">{_md(b["text"])}</span></p>')
        elif t == "list":
            if b.get("plain"):
                out += [f"<p>{_md(x)}</p>" for x in b.get("items") or []]
            else:
                out.append("<ul>" + "".join(f"<li>{_md(x)}</li>" for x in b.get("items") or []) + "</ul>")
        elif t == "quote":
            out.append(f"<blockquote><p><b>{_md(b['text'])}</b></p></blockquote>")
        out.append("<p><br></p>" if t in ("list", "quote") else "")
    return "".join(out)


def blocks_text(blocks: List[Dict[str, Any]]) -> str:
    lines = []
    for b in blocks:
        if b["type"] == "list":
            lines += [(_plain(x) if b.get("plain") else "• " + _plain(x)) for x in b.get("items") or []]
        else:
            lines.append(_plain(b.get("text", "")))
    return "\n".join(lines)


# ── 커서·넣기 ────────────────────────────────────────────────────────────────
def _count(fr, css: str) -> int:
    try:
        return fr.locator(css).count()
    except Exception:
        return 0


def caret_end(fr, page) -> None:
    """본문 맨 끝 글 칸에 커서. 마지막이 그림이면 그 아래 새 줄을 만든다."""
    comps = fr.locator(SEL["component"])
    n = comps.count()
    last = comps.nth(n - 1) if n else None
    if last is not None and "se-text" not in (last.get_attribute("class") or ""):
        last.click()
        page.keyboard.press("ArrowDown")
        page.keyboard.press("End")
        page.keyboard.press("Enter")
        return
    paras = fr.locator(SEL["body_para"])
    if paras.count():
        paras.last.click()
        page.keyboard.press("End")


def _text_len(fr) -> int:
    """제목을 뺀 본문 글자 수 — 붙여넣기가 먹었는지 확인할 때."""
    try:
        return int(fr.evaluate("""() => [...document.querySelectorAll('.se-components-wrap .se-component.se-text')]
              .reduce((n, e) => n + e.innerText.trim().length, 0)"""))
    except Exception:
        return 0


def paste_html(fr, page, html_s: str, text_s: str) -> bool:
    """서식 있는 붙여넣기. ① 진짜 클립보드 + Ctrl+V ② 붙여넣기 이벤트. 글이 늘었는지로 확인."""
    before = _text_len(fr)
    try:
        page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://blog.naver.com")
        fr.evaluate("""([h, t]) => navigator.clipboard.write([new ClipboardItem({
              'text/html': new Blob([h], {type: 'text/html'}), 'text/plain': new Blob([t], {type: 'text/plain'})})])""",
                    [html_s, text_s])
        page.keyboard.press("Control+V")
        time.sleep(1.0)
        if _text_len(fr) > before + 5:
            return True
    except Exception as e:      # noqa: BLE001
        detail(f"  클립보드 붙여넣기 안 됨: {e}")
    try:
        fr.evaluate("""([h, t]) => { const dt = new DataTransfer(); dt.setData('text/html', h); dt.setData('text/plain', t);
              const el = document.activeElement || document.body;
              el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true})); }""",
                    [html_s, text_s])
        time.sleep(1.0)
        if _text_len(fr) > before + 5:
            return True
    except Exception as e:      # noqa: BLE001
        detail(f"  붙여넣기 이벤트 안 됨: {e}")
    return False


def type_blocks(page, blocks: List[Dict[str, Any]]) -> None:
    kb = page.keyboard

    def rich(text: str) -> None:
        for k, seg in enumerate(re.split(r"\*\*", text or "")):
            if k % 2:
                kb.press("Control+B")
            if seg:
                kb.insert_text(seg)
            if k % 2:
                kb.press("Control+B")
        kb.press("Enter")

    for b in blocks:
        t = b["type"]
        if t == "heading":
            rich(f"**{_plain(b['text'])}**")
        elif t in ("paragraph", "small"):
            rich(b["text"])
        elif t == "quote":
            rich(f"**“{_plain(b['text'])}”**")
        elif t == "list":
            for x in b.get("items") or []:
                rich(x if b.get("plain") else "• " + x)
        time.sleep(0.15)


def put_text(fr, page, blocks: List[Dict[str, Any]]) -> None:
    if not blocks:
        return
    caret_end(fr, page)
    mode = config.load()["naver"].get("input_mode", "paste")
    if mode == "paste" and paste_html(fr, page, blocks_html(blocks), blocks_text(blocks)):
        return
    if mode == "paste":
        detail("  붙여넣기가 안 먹어서 타자로 넣어요")
    type_blocks(page, blocks)


def put_image(fr, page, path: Path) -> bool:
    before = _count(fr, SEL["image_comp"])
    wait = int(config.load()["naver"].get("image_wait_sec", 90))
    caret_end(fr, page)
    try:
        with page.expect_file_chooser(timeout=10_000) as fc:
            _click(fr, SEL["photo_btn"], TEXT["photo"], timeout=8_000)
        fc.value.set_files(str(path))
    except Exception as e:      # noqa: BLE001
        detail(f"  사진 버튼으로 못 넣음 → 숨은 칸/붙여넣기: {e}")
        try:
            fr.locator(SEL["photo_file"]).first.set_input_files(str(path), timeout=5_000)
        except Exception:
            b64 = base64.b64encode(path.read_bytes()).decode()
            fr.evaluate("""([b64, name]) => { const bin = atob(b64); const a = new Uint8Array(bin.length);
                  for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i);
                  const dt = new DataTransfer(); dt.items.add(new File([a], name, {type: 'image/png'}));
                  (document.activeElement || document.body).dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true})); }""",
                        [b64, path.name])
    deadline = time.time() + wait
    while time.time() < deadline:
        if _count(fr, SEL["image_comp"]) > before:
            time.sleep(1.0)
            return True
        time.sleep(0.8)
    return False


# ── 글 전체 넣기 ─────────────────────────────────────────────────────────────
def resolve_blocks(job, post: Dict[str, Any]) -> List[Dict[str, Any]]:
    """그림 블록에 실제 파일을 붙인다. 없는 그림은 빼고(대표이미지는 디자인 표지로 언제나 있음)."""
    from .s4_assets import slot_file
    out = []
    for b in post.get("blocks") or []:
        if b["type"] == "image":
            f = slot_file(job, post["data_id"], b["slot"])
            if f is None:
                if not b.get("optional"):
                    detail(f"  그림 없음(건너뜀): {post['data_id']} {b['slot']}")
                continue
            b = {**b, "file": str(f)}
        out.append(b)
    return out


def fill(s: NaverSession, fr, post: Dict[str, Any], blocks: List[Dict[str, Any]]) -> None:
    page = s.page
    delay = float(config.load()["naver"].get("step_delay", 0.4))
    log("  제목을 넣어요")
    _click(fr, SEL["title"], None, timeout=15_000)
    page.keyboard.press("Control+A")
    page.keyboard.insert_text(post["title"])
    time.sleep(delay)
    # 본문 첫 칸으로
    paras = fr.locator(SEL["body_para"])
    if paras.count():
        paras.first.click()
    run: List[Dict[str, Any]] = []
    n_img = sum(1 for b in blocks if b["type"] == "image")
    k = 0
    for b in blocks + [{"type": "_end"}]:
        if b["type"] in ("image", "_end"):
            put_text(fr, page, run)
            run = []
            time.sleep(delay)
        if b["type"] == "image":
            k += 1
            log(f"  그림 {k}/{n_img} 넣는 중 …")
            if not put_image(fr, page, Path(b["file"])):
                s.shot("image-fail")
                raise PostError("그림을 넣지 못했어요. 왼쪽 창을 확인해 주세요 (그림이 너무 크거나 사진 창이 바뀌었을 수 있어요).")
            time.sleep(delay)
        elif b["type"] != "_end":
            run.append(b)
    s.shot("filled")


# ── 발행 창 ──────────────────────────────────────────────────────────────────
def _pick_category(fr, name: str) -> None:
    if not name:
        return
    _click(fr, SEL["category_btn"], None, timeout=8_000)
    time.sleep(0.5)
    exact = re.compile(rf"^\s*{re.escape(name)}\s*$")
    el = _first_visible(fr, SEL["category_item"] + ":has-text(\"" + name.replace('"', '') + "\")", exact, role="option")
    if el is None:
        el = _first_visible(fr, "label, li, span", exact)
    if el is None:
        raise PostError(f"카테고리 「{name}」을 찾지 못했어요. 블로그 관리에서 카테고리 이름을 확인해 주세요.")
    el.click()
    time.sleep(0.4)


def _set_tags(fr, page, tags: List[str]) -> None:
    box = _first_visible(fr, SEL["tag_input"])
    if box is None:
        detail("  태그 칸을 못 찾음 — 태그 없이 진행")
        return
    for t in tags[:30]:
        box.click()
        page.keyboard.insert_text(t)
        page.keyboard.press("Enter")
        time.sleep(0.12)


def _set_reserve(fr, when: datetime) -> None:
    try:
        _click(fr, SEL["time_reserve"], TEXT["reserve"], role="radio", timeout=6_000)
        time.sleep(0.6)
        # 날짜: 달력 열고 달 맞추고 날 누르기
        _click(fr, SEL["date_input"], None, timeout=5_000)
        for _ in range(14):
            t = (_first_visible(fr, SEL["dp_title"]) or fr.locator("body")).inner_text()
            ym = re.findall(r"\d+", t)
            if len(ym) >= 2 and int(ym[0]) == when.year and int(ym[1]) == when.month:
                break
            _click(fr, SEL["dp_next"], re.compile(r"다음|Next"), timeout=3_000)
            time.sleep(0.3)
        day = _first_visible(fr, f"td a:text-is('{when.day}'), td button:text-is('{when.day}')")
        if day is None:
            raise PostError(f"달력에서 {when.day}일을 못 찾음")
        day.click()
        # 시·분 (분은 10분 단위)
        for css, val in ((SEL["hour_sel"], when.hour), (SEL["minute_sel"], when.minute)):
            sel = _first_visible(fr, css)
            if sel is None:
                raise PostError("시각 칸을 못 찾음")
            for v in (f"{val:02d}", str(val)):
                try:
                    sel.select_option(value=v, timeout=2_000)
                    break
                except Exception:
                    try:
                        sel.select_option(label=v, timeout=2_000)
                        break
                    except Exception:
                        continue
            else:
                raise PostError(f"시각 {val} 을 고르지 못함")
    except PostError as e:
        raise ScheduleUnsupported(str(e)) from e


def publish_layer(s: NaverSession, fr, post: Dict[str, Any], category: str, when: Optional[datetime],
                  dry_run: bool) -> Dict[str, Any]:
    page = s.page
    log("  발행 창을 열어요")
    _click(fr, SEL["publish_open"], TEXT["publish"], timeout=15_000)
    time.sleep(1.0)
    if category:
        log(f"  카테고리: {category}")
        _pick_category(fr, category)
    _try_click(fr, SEL["open_public"], TEXT["public"], timeout=3_000)
    if post.get("tags"):
        log(f"  태그 {len(post['tags'][:30])}개")
        _set_tags(fr, page, post["tags"])
    if when:
        log(f"  예약: {when:%m월 %d일 %H:%M}")
        _set_reserve(fr, when)
    else:
        _try_click(fr, SEL["time_now"], TEXT["now"], timeout=2_000)
    s.shot("publish-layer")
    if dry_run:
        log("  미리 채워 보기라서 여기서 멈춰요 — 왼쪽 창에서 확인하고, 괜찮으면 직접 「발행」을 눌러도 돼요.")
        return {"dry_run": True}
    before = page.url
    # ★ 발행을 누른 뒤에는 다시 시도하지 않는다(두 번 올라갈 수 있다)
    btn = _first_visible(fr, SEL["confirm"]) or _first_visible(fr, SEL["layer"] + " button", TEXT["publish"])
    if btn is None:
        raise PostError("발행 창의 「발행」 버튼을 찾지 못했어요.")
    btn.click()
    url = ""
    for _ in range(30):
        time.sleep(1)
        try:
            urls = [page.url] + [f.url for f in page.frames]
        except Exception:
            urls = []
        hit = next((u for u in urls if SEL["post_url"].search(u) and u != before), "")
        if hit:
            url = hit
            break
    s.shot("published")
    if not url and not when:
        url = rss_lookup(blog_of(s.acc), post["title"])
    m = SEL["post_url"].search(url or "")
    return {"url": url, "log_no": (m.group(1) or m.group(2)) if m else "", "at": datetime.now().isoformat(timespec="seconds"),
            "scheduled_for": when.strftime("%Y-%m-%d %H:%M") if when else None, "title": post["title"]}


def rss_lookup(blog: str, title: str, tries: int = 6) -> str:
    """발행한 글 주소 — 화면에서 못 읽으면 블로그 공개 RSS 에서 같은 제목을 찾는다(2026-10-05 실측으로 확인)."""
    import urllib.request
    want = re.sub(r"\s+", "", title)
    for k in range(tries):
        try:
            x = urllib.request.urlopen(f"https://rss.blog.naver.com/{blog}.xml", timeout=10).read().decode("utf-8", "replace")
            for t, link in re.findall(r"<item>.*?<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>.*?<link>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", x, re.S):
                if re.sub(r"\s+", "", _html.unescape(t)) == want:
                    return link.split("?")[0]
        except Exception as e:      # noqa: BLE001
            detail(f"  RSS 확인 실패: {e}")
        time.sleep(5 + 5 * k)
    return ""


# ── 바깥에서 부르는 것 ───────────────────────────────────────────────────────
def publish(acc: Dict[str, Any], job, post: Dict[str, Any], *, when: Optional[datetime] = None,
            dry_run: bool = False) -> Dict[str, Any]:
    blog = blog_of(acc)
    blocks = resolve_blocks(job, post)
    with NaverSession(acc, HOME.format(blog=blog)) as s:
        page = s.page
        _guard_dialogs(page)
        if not s.logged_in():
            raise PostError("네이버 로그인이 필요해요. 「로그인 창 열기」로 왼쪽 창에서 로그인해 주세요.")
        log("  글쓰기 화면을 열어요")
        page.goto(WRITE.format(blog=blog), wait_until="domcontentloaded")
        if "nid.naver.com" in page.url:
            raise PostError("네이버 로그인이 풀렸어요. 「로그인 창 열기」로 다시 로그인해 주세요.")
        fr = editor_frame(page)
        time.sleep(1.5)
        dismiss_popups(fr)
        s.shot("editor")
        fill(s, fr, post, blocks)
        return publish_layer(s, fr, post, post.get("category") or job.category, when, dry_run)


def list_tabs(acc: Dict[str, Any]) -> List[Dict[str, Any]]:
    """지금 열려 있는 네이버 글쓰기 탭들 — 「현재 탭 선택하기」."""
    if not port_open(int(acc["port"])):
        return []
    out = []
    with NaverSession(acc, HOME.format(blog=blog_of(acc))) as s:
        for i, p in enumerate(s.ctx.pages):
            u = p.url
            if "blog.naver.com" in u and re.search(r"Redirect=Write|postwrite|PostWriteForm", u, re.I):
                try:
                    title = p.title()
                except Exception:
                    title = ""
                out.append({"index": i, "url": u, "title": title})
    return out


def fill_tab(acc: Dict[str, Any], job, post: Dict[str, Any], index: int, *, publish_now: bool = False,
             when: Optional[datetime] = None) -> Dict[str, Any]:
    """사람이 열어 둔 글쓰기 탭에 넣는다 (영상의 「선택된 글쓰기창에 넣기」). 발행은 고를 때만."""
    blocks = resolve_blocks(job, post)
    with NaverSession(acc, HOME.format(blog=blog_of(acc))) as s:
        pages = s.ctx.pages
        if index >= len(pages):
            raise PostError("고른 탭이 닫혔어요. 「현재 탭 선택하기」를 다시 눌러 주세요.")
        s.page = pages[index]
        s.page.bring_to_front()
        _guard_dialogs(s.page)
        fr = editor_frame(s.page)
        dismiss_popups(fr)
        fill(s, fr, post, blocks)
        if publish_now or when:
            return publish_layer(s, fr, post, post.get("category") or job.category, when, dry_run=False)
        log("  글쓰기 창에 다 넣었어요. 왼쪽 창에서 확인하고 「발행」을 눌러 주세요.")
        return {"filled": True}


def open_login(acc: Dict[str, Any]) -> None:
    blog = blog_of(acc)
    fresh = ensure_chrome(acc, LOGIN.format(blog=blog))
    if not fresh:
        with NaverSession(acc, HOME.format(blog=blog)) as s:
            if not s.logged_in():
                s.page.goto(LOGIN.format(blog=blog))
            else:
                s.page.goto(HOME.format(blog=blog))
        try:
            place(int(acc["port"]), "left")
        except Exception:
            pass
    log(f"왼쪽 Chrome 창에서 네이버에 로그인해 주세요 (블로그: blog.naver.com/{blog}). "
        "「로그인 상태 유지」를 켜 두면 다음부터는 안 물어봐요. 창은 닫지 않아도 돼요.")


def status(acc: Dict[str, Any]) -> Dict[str, Any]:
    if not port_open(int(acc["port"])):
        return {"chrome": False, "logged_in": None}
    with NaverSession(acc, HOME.format(blog=blog_of(acc))) as s:
        return {"chrome": True, "logged_in": s.logged_in()}


def probe(acc: Dict[str, Any]) -> Path:
    """글쓰기 화면·사진 창·발행 창 구조를 떠 둔다 — 셀렉터를 맞출 때. **발행하지 않는다.**"""
    out = config.LOGS / "probe" / time.strftime("%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    blog = blog_of(acc)
    with NaverSession(acc, HOME.format(blog=blog)) as s:
        page = s.page
        _guard_dialogs(page)
        log(f"  로그인: {s.logged_in()}")
        page.goto(WRITE.format(blog=blog), wait_until="domcontentloaded")
        time.sleep(5)
        page.screenshot(path=str(out / "0-raw.png"))
        try:
            fr = editor_frame(page, 20_000)
        except PostError as e:
            log(f"  {e}")
            (out / "page.html").write_text(page.content(), encoding="utf-8")
            return out
        (out / "frame.html").write_text(fr.content(), encoding="utf-8")
        dismiss_popups(fr)
        page.screenshot(path=str(out / "1-editor.png"))
        try:
            (out / "editor.aria.yml").write_text(fr.locator("body").aria_snapshot(), encoding="utf-8")
        except Exception as e:      # noqa: BLE001
            log(f"  aria 스냅샷 실패: {e}")
        found = {k: _count(fr, v) for k, v in SEL.items() if isinstance(v, str) and k != "frame"}
        detail(f"probe 셀렉터 개수(에디터): {found}")
        try:
            _click(fr, SEL["publish_open"], TEXT["publish"], timeout=8_000)
            time.sleep(1.5)
            page.screenshot(path=str(out / "3-publish-layer.png"))
            (out / "frame-layer.html").write_text(fr.content(), encoding="utf-8")
            (out / "layer.aria.yml").write_text(fr.locator("body").aria_snapshot(), encoding="utf-8")
            found2 = {k: _count(fr, v) for k, v in SEL.items() if isinstance(v, str) and k != "frame"}
            detail(f"probe 셀렉터 개수(발행 창): {found2}")
            page.keyboard.press("Escape")
        except Exception as e:      # noqa: BLE001
            log(f"  발행 창은 못 열었어요: {e}")
        log("  셀렉터 확인: " + ", ".join(f"{k} {'✓' if v else '✗'}" for k, v in found.items()))
    log(f"  → {out}")
    return out
