"""블록 → 핸들러 라우팅. 단일 진입점 POST /skill 이 쓴다.

배경
  블록마다 스킬 URL 이 다르면(`/skill/food.menu.today`) 블록을 추가할 때마다
  오픈빌더에서 스킬을 새로 등록하고 토큰을 다시 붙여넣어야 한다.
  도메인이 12개까지 늘 예정이라 그때마다 반복이 된다.
  → 스킬은 1개만 등록하고, payload 의 userRequest.block 으로 분기한다.

★ block.name 은 총학이 오픈빌더에서 붙인 이름이다.
  우리 내부 키('food.menu.today')가 아니라 '오늘 학식' 같은 한국어일 수 있고,
  이름을 바꾸면 라우팅이 끊긴다. 그래서 **block.id(불변)를 우선**한다.

★ 모르는 블록은 추측해서 보내지 않는다.
  키워드로 대충 맞히면 새 블록이 조용히 엉뚱한 답을 한다.
  폴백을 내고 이름을 기록한다 — GET /admin/blocks 에서 확인해 한 줄 추가하면 된다.
"""

from __future__ import annotations

import functools
import logging
import pathlib
import re
from collections import OrderedDict

import yaml

log = logging.getLogger("jbnu.routing")

CONFIG_PATH = pathlib.Path(__file__).resolve().parents[1] / "config" / "blocks.yaml"
MAX_UNMAPPED = 30

# 매핑에 없던 블록 이름을 기억해 둔다. 총학이 뭘 추가해야 하는지 알려주기 위해서다.
_unmapped: "OrderedDict[str, dict]" = OrderedDict()


@functools.lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8")) or {}


def load(path: pathlib.Path | None = None) -> dict:
    return _load(str(path or CONFIG_PATH))


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def _name_index(doc: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for handler, names in (doc.get("handlers") or {}).items():
        for n in names or []:
            out[_norm(n)] = handler
    return out


def known_handlers(path: pathlib.Path | None = None) -> list[str]:
    return list((load(path).get("handlers") or {}).keys())


def resolve(payload: dict, *, path_block: str | None = None,
            config_path: pathlib.Path | None = None) -> tuple[str | None, str]:
    """(핸들러, 결정 근거) 를 돌려준다.

    우선순위
      1. 경로에 블록이 박힌 기존 엔드포인트 (/skill/{block_name}) — 하위 호환
      2. userRequest.block.id  (이름을 바꿔도 안 끊긴다)
      3. userRequest.block.name
      4. intent.name           (block 이 비는 경우의 보조)
      5. 없음 → 폴백
    """
    doc = load(config_path)
    handlers = set(doc.get("handlers") or {})
    ids = doc.get("ids") or {}
    by_name = _name_index(doc)

    if path_block:
        if path_block in handlers:
            return path_block, "path"
        hit = by_name.get(_norm(path_block))
        if hit:
            return hit, "path:alias"

    ur = payload.get("userRequest") or {}
    block = ur.get("block") or {}
    bid = str(block.get("id") or "").strip()
    bname = str(block.get("name") or "").strip()
    iname = str((payload.get("intent") or {}).get("name") or "").strip()

    if bid and bid in ids:
        return ids[bid], "block.id"
    if bname:
        if bname in handlers:
            return bname, "block.name"
        hit = by_name.get(_norm(bname))
        if hit:
            return hit, "block.name:alias"
    if iname:
        hit = by_name.get(_norm(iname))
        if hit:
            return hit, "intent.name:alias"

    _remember(bid, bname or iname, ur.get("utterance", ""))
    return None, "unmapped"


def _remember(block_id: str, name: str, utterance: str) -> None:
    """매핑 안 된 블록을 기록한다. 차단만 하지 않고 해제 경로를 준다."""
    key = f"{block_id}|{name}"
    if key in _unmapped:
        _unmapped[key]["hits"] += 1
        return
    if len(_unmapped) >= MAX_UNMAPPED:
        _unmapped.popitem(last=False)
    _unmapped[key] = {"block_id": block_id, "block_name": name,
                      "sample_utterance": utterance[:60], "hits": 1}
    log.warning("[routing] UNMAPPED block id=%r name=%r utterance=%r — "
                "config/blocks.yaml 에 한 줄 추가하면 된다", block_id, name, utterance[:40])


def unmapped_blocks() -> list[dict]:
    return list(_unmapped.values())


def clear_unmapped() -> None:
    _unmapped.clear()
