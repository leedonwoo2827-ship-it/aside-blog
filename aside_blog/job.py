"""작업(job) 폴더 하나 = 교재 한 권 = 블로그 연재 하나. 학습카드 1장 = 블로그 글 1개.

jobs/<job>/
  job.json              책 이름·연재 문구·출처·카테고리 (git 에 올려도 되는 것)
  job.local.json        PC별 덮어쓰기
  source/               교재 PDF 에서 뽑은 글(all.txt)·조각(chunks/)·목차(manifest.json)
  cards/json/<id>.json  학습카드 내용 (s0 이 쓰거나 기존 카드 HTML 에서 읽어 옴)
  cards/html/           학습카드 HTML + 00_목차.html — textbook-study-cards 스킬과 같은 꼴
  posts/<id>.json       s1 이 쓴 블로그 글 (캐시 = 이어하기)
  overrides/<id>.json   사람이 고친 글 — **언제나 이긴다**
  images/               이미지프롬프트.json + Codex 가 그린 그림(<id>-cover.png, <id>-sec1.png …)
  assets/<id>/          카드에서 찍은 그림(표·OX 정답) + 디자인 표지 — Codex 그림이 없어도 글이 완성된다
  state.json            게시·예약 기록
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config

_STATE_LOCK = threading.Lock()
SLUG = re.compile(r"[^0-9A-Za-z가-힣]+")


def base_id(card_id: str) -> str:
    """'3-2-05a' → '3-2-05' (긴 소단원을 나눈 카드는 뒤에 a,b,c 가 붙는다)."""
    m = re.match(r"^(\d+-\d+-\d+)", card_id)
    return m.group(1) if m else card_id


@dataclass
class Card:
    data: Dict[str, Any]
    n: int = 0
    man: Dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.data["id"]

    # aside-threads 코드(s3_images 등)가 data_id 로 부른다
    @property
    def data_id(self) -> str:
        return self.id

    @property
    def title(self) -> str:
        return self.data.get("title", "")

    @property
    def subtitle(self) -> str:
        return self.data.get("subtitle", "")

    @property
    def chapter(self) -> int:
        return int(self.man.get("chapter") or 1)

    @property
    def crumb(self) -> str:
        m = self.man
        if not m:
            return ""
        return f"제{m['chapter']}장 {m['chapter_name']} › {m['sec_no']}. {m['sec_name']}"

    @property
    def filename(self) -> str:
        return f"{self.n:02d}_{self.id}_{SLUG.sub('', self.title)[:24]}.html"


@dataclass
class Job:
    name: str

    @property
    def dir(self) -> Path:
        return config.JOBS / self.name

    def sub(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    @property
    def source(self) -> Path:
        return self.dir / "source"

    @property
    def card_json(self) -> Path:
        return self.dir / "cards" / "json"

    @property
    def card_html(self) -> Path:
        return self.dir / "cards" / "html"

    @property
    def posts(self) -> Path:
        return self.dir / "posts"

    @property
    def overrides(self) -> Path:
        return self.dir / "overrides"

    @property
    def images(self) -> Path:
        return self.dir / "images"

    @property
    def assets(self) -> Path:
        return self.dir / "assets"

    def exists(self) -> bool:
        return (self.dir / "job.json").exists()

    @cached_property
    def meta(self) -> Dict[str, Any]:
        base = config.read_json(self.dir / "job.json", {}) or {}
        return config.deep_merge(base, config.read_json(self.dir / "job.local.json", {}) or {})

    def get(self, key: str, default: Any = None) -> Any:
        cur: Any = self.meta
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def setting(self, key: str) -> Any:
        """job.json 의 같은 이름 칸 → 없으면 전체 설정 cards.<key> (book·series·source)."""
        return self.get(key) or config.load()["cards"].get(key, "")

    @property
    def category(self) -> str:
        return self.get("category") or config.load()["naver"]["category"]

    # ── 목차·카드 ────────────────────────────────────────────────────────────
    @property
    def manifest(self) -> List[Dict[str, Any]]:
        return config.read_json(self.source / "manifest.json", []) or []

    @cached_property
    def cards(self) -> List[Card]:
        man = self.manifest
        by = {m["id"]: m for m in man}
        order = {m["id"]: i for i, m in enumerate(man)}
        out = []
        for f in sorted(self.card_json.glob("*.json")) if self.card_json.exists() else []:
            d = config.read_json(f)
            if isinstance(d, dict) and d.get("id"):
                out.append(d)
        out.sort(key=lambda d: (order.get(base_id(d["id"]), 10 ** 6), d["id"]))
        return [Card(d, i, by.get(base_id(d["id"]), {})) for i, d in enumerate(out, 1)]

    def card(self, card_id: str) -> Optional[Card]:
        return next((c for c in self.cards if c.id == card_id), None)

    def pick(self, only: Optional[List[str]] = None) -> List[Card]:
        if not only:
            return list(self.cards)
        want = set(only)
        got = [c for c in self.cards if c.id in want or str(c.n) in want or f"{c.n:02d}" in want]
        if not got:
            raise SystemExit(f"고른 카드가 없어요: {', '.join(only)}")
        return got

    def html_path(self, c: Card) -> Path:
        return self.card_html / c.filename

    # ── 글 ───────────────────────────────────────────────────────────────────
    def raw_post(self, card_id: str) -> Optional[Dict[str, Any]]:
        return config.read_json(self.posts / f"{card_id}.json")

    def post(self, card_id: str) -> Optional[Dict[str, Any]]:
        """s1 결과 위에 사람의 손편집을 덮은 것 — 다른 모든 곳은 이것만 읽는다."""
        base = self.raw_post(card_id)
        if base is None:
            return None
        return config.deep_merge(base, config.read_json(self.overrides / f"{card_id}.json", {}) or {})

    def overridden(self, card_id: str) -> bool:
        return (self.overrides / f"{card_id}.json").exists()

    def save_override(self, card_id: str, patch: Dict[str, Any]) -> None:
        cur = config.read_json(self.overrides / f"{card_id}.json", {}) or {}
        config.write_json(self.overrides / f"{card_id}.json", config.deep_merge(cur, patch))

    def reset_override(self, card_id: str) -> None:
        (self.overrides / f"{card_id}.json").unlink(missing_ok=True)

    # ── 그림 ─────────────────────────────────────────────────────────────────
    def art(self, card_id: str, role: str) -> Path:
        """Codex(또는 사람이 넣은) 그림 — role: cover | sec1 | sec2 …"""
        return self.images / f"{card_id}-{role}.png"

    def asset(self, card_id: str, name: str) -> Path:
        """카드에서 찍은 그림 — name: cover | table-1 | quiz | terms …"""
        return self.assets / card_id / f"{name}.png"

    def cover_path(self, card_id: str) -> Optional[Path]:
        for p in (self.art(card_id, "cover"), self.asset(card_id, "cover")):
            if p.exists():
                return p
        return None

    def url(self, path: Path) -> str:
        return "/jobs/" + path.resolve().relative_to(config.JOBS.resolve()).as_posix()

    # ── 게시 상태 ────────────────────────────────────────────────────────────
    def state(self) -> Dict[str, Any]:
        return config.read_json(self.dir / "state.json", {}) or {}

    def update_state(self, card_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """다시 읽고 고쳐 쓴다 — 서버(예약)와 하위 프로세스(게시)가 같이 쓴다."""
        with _STATE_LOCK:
            st = self.state()
            cur = dict(st.get(card_id) or {})
            for k, v in patch.items():
                if v is None:
                    cur.pop(k, None)
                else:
                    cur[k] = v
            st[card_id] = cur
            config.write_json(self.dir / "state.json", st)
            return cur


def all_jobs() -> List[Job]:
    if not config.JOBS.exists():
        return []
    return [Job(p.name) for p in sorted(config.JOBS.iterdir())
            if p.is_dir() and (p / "job.json").exists() and p.name != "example"]


def need(name: Optional[str]) -> Job:
    if not name:
        jobs = all_jobs()
        if len(jobs) == 1:
            return jobs[0]
        raise SystemExit("작업을 골라 주세요. 있는 작업: " + (", ".join(j.name for j in jobs) or "없음"))
    job = Job(name)
    if not job.exists():
        raise SystemExit(f"작업이 없어요: {name} — 「① 교재·카드」 탭에서 먼저 만들어 주세요")
    return job
