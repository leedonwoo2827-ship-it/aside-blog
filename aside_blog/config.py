"""설정 — `aside.config.json` 위에 `aside.config.local.json`(PC별, git 제외)을 덮는다.

job 설정은 `jobs/<job>/job.json` 위에 `job.local.json` 을 덮는다(lecture-composer 와 같은 규칙).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "jobs"
PROFILES = ROOT / "profiles"
CONTEXT = ROOT / "_context"
LOGS = ROOT / "logs"
TEMPLATES = ROOT / "templates"
LOCAL = ROOT / "local.json"

DEFAULTS: Dict[str, Any] = {
    "llm": {"total_timeout_sec": 900, "stall_timeout_sec": 180, "retries": 2},
    "models": {"copy": "auto", "image": "auto"},       # auto = 계정에서 되는 모델을 찾아 local.json 에 기억
    "effort": {"copy": "medium", "cards": "medium", "post": "medium"},
    # 교재 PDF → 학습카드 (textbook-study-cards 스킬과 같은 꼴)
    "cards": {
        "batch": 3, "split_lines": 150, "max_chunk_lines": 400,
        "book": "변액보험 학습카드",
        "series": "2026 변액보험의 이해와 판매 · 한 주제씩 공부하며 정리",
        "source": "생명보험협회 『2026 변액보험의 이해와 판매』(변액보험판매자격시험 교재)를 공부하며 "
                  "개인 학습용으로 요약·재구성한 노트입니다.",
    },
    # 학습카드 → 네이버 블로그 글
    "post": {
        "batch": 5, "title_max": 60, "tags_max": 30,
        "table_mode": "image",       # image | text | both — 표를 그림으로(카드 그대로) 또는 글로
        "terms_mode": "text",        # text | image — 핵심 용어
        "quiz_mode": "image",        # image | text | both — OX 정답 (네이버는 접기 칸이 없다)
        "section_images": True,      # Codex 섹션 그림이 있으면 소제목 아래에 넣기
        "source_footer": True,
    },
    "image": {
        "bg": "#F6F1E8", "accent_a": "#1F4E79", "accent_b": "#9DC3E6",
        "cover_size": "1536x1024", "section_size": "1024x1024",
        "per_post": 3, "workers": 3, "retries": 2,
        "negative": "",
    },
    "assets": {"width": 860, "scale": 2},
    "naver": {
        "chrome": "", "native_schedule": True, "pattern": "2-lunch",
        "slots": ["12:30", "19:00"], "weekend_slots": ["11:00", "20:00"],
        "category": "변액보험판매관리사 133제", "open_type": "public",
        "input_mode": "paste",       # paste(서식째 붙여넣기) | type(한 줄씩 타자)
        "base_port": 9381, "image_wait_sec": 90, "step_delay": 0.4,
    },
    "ui": {"port": 5295, "width": 460},
}


def deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in (b or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise SystemExit(f"JSON 이 깨졌습니다: {path} — {e}")


def write_json(path: Path, data: Any) -> None:
    """원자적 쓰기 — 서버와 하위 프로세스가 같은 파일을 만진다."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def load() -> Dict[str, Any]:
    cfg = deep_merge(DEFAULTS, read_json(ROOT / "aside.config.json", {}) or {})
    return deep_merge(cfg, read_json(ROOT / "aside.config.local.json", {}) or {})


def local() -> Dict[str, Any]:
    return read_json(LOCAL, {}) or {}


def save_local(data: Dict[str, Any]) -> None:
    write_json(LOCAL, data)
