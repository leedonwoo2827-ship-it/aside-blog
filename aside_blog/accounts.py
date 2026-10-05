"""네이버 계정 = Chrome 전용 프로필 하나 + CDP 포트 하나 + 블로그 아이디.

local.json (PC별, git 제외):
  {"current": "dekman", "accounts": [{"name": "dekman", "port": 9381, "label": "dekman 블로그", "blog_id": "dekman"}]}
프로필 폴더 profiles/<name>/ 에 로그인 쿠키가 산다 — **절대 커밋하지 않는다.**
같은 프로필 Chrome 은 동시에 하나만 뜰 수 있어서 계정마다 포트를 따로 준다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config


def all_() -> List[Dict[str, Any]]:
    return list(config.local().get("accounts") or [])


def get(name: Optional[str]) -> Dict[str, Any]:
    accs = all_()
    if not accs:
        raise SystemExit("네이버 계정이 아직 없어요 — 「올리기」 탭에서 계정을 추가해 주세요")
    name = name or config.local().get("current") or accs[0]["name"]
    for a in accs:
        if a["name"] == name:
            return a
    raise SystemExit(f"계정이 없습니다: {name} (있는 것: {', '.join(a['name'] for a in accs)})")


def profile(acc: Dict[str, Any]) -> Path:
    p = config.PROFILES / acc["name"]
    p.mkdir(parents=True, exist_ok=True)
    return p


def add(name: str, label: str = "", blog_id: str = "") -> Dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", name):
        raise SystemExit("계정 이름은 영문·숫자·_- 만 써 주세요 (폴더 이름이 돼요). 화면에 보일 이름은 따로 적을 수 있어요")
    blog_id = (blog_id or name).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,40}", blog_id):
        raise SystemExit("블로그 아이디는 blog.naver.com/ 뒤의 영문 아이디예요 (예: dekman)")
    data = config.local()
    accs = list(data.get("accounts") or [])
    if any(a["name"] == name for a in accs):
        raise SystemExit(f"이미 있습니다: {name}")
    base = int(config.load()["naver"]["base_port"])
    used = {int(a["port"]) for a in accs}
    from .chrome import port_open
    used.add(int(data.get("panel_port") or base - 1))
    port = next(p for p in range(base, base + 200) if p not in used and not port_open(p))
    acc = {"name": name, "port": port, "label": label or f"{blog_id} 블로그", "blog_id": blog_id}
    accs.append(acc)
    data["accounts"] = accs
    data.setdefault("current", name)
    config.save_local(data)
    profile(acc)
    return acc


def remove(name: str) -> None:
    data = config.local()
    data["accounts"] = [a for a in data.get("accounts") or [] if a["name"] != name]
    if data.get("current") == name:
        data["current"] = data["accounts"][0]["name"] if data["accounts"] else None
    config.save_local(data)


def set_current(name: str) -> None:
    get(name)
    data = config.local()
    data["current"] = name
    config.save_local(data)


def blog_id(acc: Dict[str, Any]) -> str:
    return acc.get("blog_id") or acc["name"]
