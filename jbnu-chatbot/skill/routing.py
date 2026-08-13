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
    """블록 이름 비교용 정규화.

    ★ 점·하이픈·밑줄·공백을 전부 지운다.
      오픈빌더에 'info.search' 로 만들었더니 카카오가 'infosearch' 로 저장했다.
      이름을 바꾼 것은 총학이 아니라 **플랫폼 자신**이었다 —
      우리가 통제할 수 없는 변형이므로, 비교하는 쪽에서 흡수한다.
      안 그러면 블록을 추가할 때마다 같은 문제가 난다.
    """
    return re.sub(r"[\s._\-]+", "", (s or "")).lower()


def _name_index(doc: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for handler, names in (doc.get("handlers") or {}).items():
        # 핸들러 키 자체도 이름으로 쓴다. 별칭에 안 적어도 매칭되게 —
        # 별칭 목록에 빠뜨리는 것이 가장 흔한 실수다.
        out[_norm(handler)] = handler
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

    _remember(bid, bname or iname, ur.get("utterance", ""), shape=_shape(payload))
    return None, "unmapped"


def _shape(payload: dict) -> str:
    """블록 정보가 **어떤 모양으로** 비어 있는지.

    ★ 왜 필요한가
      '없다' 를 뭉쳐 세면 고칠 수 있는지 없는지 알 수가 없다 —
      크롤의 empty 갈래를 쪼갤 때와 같은 문제다.
      우리 코드는 `block.get("id") or ""` 로 셋을 다 "" 로 접어버려서
      로그만 봐서는 카카오가 뭘 보냈는지 알 수 없었다.
    """
    ur = payload.get("userRequest") or {}
    if "block" not in ur:
        return "no-block-key"
    b = ur.get("block")
    if b is None:
        return "block-null"
    if not isinstance(b, dict):
        return f"block-{type(b).__name__}"
    parts = []
    for k in ("id", "name"):
        if k not in b:
            parts.append(f"{k}:absent")
        elif b[k] is None:
            parts.append(f"{k}:null")
        elif not str(b[k]).strip():
            parts.append(f"{k}:empty")
        else:
            parts.append(f"{k}:set")
    return " ".join(parts)


def is_welcome(payload: dict, *, path_block: str | None = None,
               config_path: pathlib.Path | None = None) -> bool:
    """첫 인사인가.

    ★ 폴백과 헷갈리면 안 된다 — 가르는 것은 **발화가 비었는지**다
      폴백도 블록 정보가 비어서 온다. 웰컴은 거기에 **할 말이 없다**는 게 더해진다.
      검색할 것이 없으니 검색으로 보낼 이유도 없다.

    ★ 이름은 후보로만 둔다
      카카오가 웰컴 블록에 무슨 이름을 붙이는지 우리가 정하지 않는다.
      맞으면 바로 되고, 틀려도 빈 발화 규칙이 받아 준다.
      실제 이름은 로그에 남는다 — 폴백 때와 같은 방식이다.
    """
    ur = payload.get("userRequest") or {}
    if not str(ur.get("utterance") or "").strip():
        return True
    names = {_norm(n) for n in
             ((load(config_path).get("handlers") or {}).get("welcome") or [])}
    block = ur.get("block") or {}
    for cand in (path_block, block.get("name"),
                 (payload.get("intent") or {}).get("name")):
        if cand and _norm(str(cand)) in names:
            return True
    return False


def by_utterance(utterance: str,
                 config_path: pathlib.Path | None = None) -> tuple[str | None, str]:
    """폴백으로 들어온 말의 갈래를 별칭으로 정한다. **폴백 경로에서만 쓴다.**

    ★ 원칙을 어기는 게 아니라 적용 범위가 다르다
      '모르는 블록은 추측해서 보내지 않는다' 는 총학이 만든 블록 얘기다.
      거기서 추측하면 상담 블록이 식단을 뱉는다.
      폴백은 이미 분류에 실패한 말이고, 대안은 **틀린 답이 아니라 못 찾음**이다.
      '오늘 학식' 에 "'오늘' 관련 안내는 못 찾았어요" 를 내보내는 것보다 낫다.

    ★ 가장 긴 별칭이 이긴다
      '학식 메뉴' 와 '학식' 이 둘 다 걸리면 긴 쪽이 더 많이 말해준다.
    """
    u = _norm(utterance)
    if not u:
        return None, "empty"
    best: tuple[int, str, str] | None = None
    for handler, names in (load(config_path).get("handlers") or {}).items():
        for n in list(names or []) + [handler]:
            k = _norm(n)
            # 한 글자짜리는 아무 데나 걸린다. 두 글자부터 본다.
            if len(k) >= 2 and k in u and (best is None or len(k) > best[0]):
                best = (len(k), handler, n)
    if best is None:
        return None, "no-alias"
    return best[1], f"alias:{best[2]}"


def is_fallback(payload: dict, *, path_block: str | None = None,
                config_path: pathlib.Path | None = None) -> bool:
    """카카오가 어느 블록도 못 고른 말인가.

    폴백은 '모르는 블록' 과 다르다.
      모르는 블록  = 총학이 만들었는데 매핑을 안 한 것 → 함부로 검색에 태우면
                    엉뚱한 도메인 블록이 검색 결과를 뱉는다. 보수적으로 간다.
      폴백 블록    = 애초에 분류에 실패한 말 → **검색이 정확히 할 일이다.**
    """
    ur = payload.get("userRequest") or {}
    block = ur.get("block") or {}

    # ★ 가장 확실한 신호는 이름이 아니라 **블록 정보가 비어 있는 것**이다 (실측)
    #   폴백으로 온 요청에는 block 이 아예 안 실려 온다.
    #     02:11:11  [skill] block='-' via=unmapped utterance='복학 신청'
    #   이름 후보를 맞히는 전략은 총학이 이름을 바꾸면 깨지지만,
    #   이건 플랫폼 동작이라 더 안정적이다.
    #
    #   그리고 '이름이 있는데 우리가 모르는 블록' 과 안전하게 갈린다 —
    #   그쪽은 총학이 만든 블록이라 함부로 검색에 태우면 안 된다.
    if not str(block.get("id") or "").strip() and        not str(block.get("name") or "").strip() and not path_block:
        return True

    names = {_norm(n) for n in (load(config_path).get("fallback_blocks") or [])}
    if not names:
        return False
    for cand in (path_block, block.get("name"),
                 (payload.get("intent") or {}).get("name")):
        if cand and _norm(str(cand)) in names:
            return True
    return False


def _remember(block_id: str, name: str, utterance: str, *,
              shape: str = "") -> None:
    """매핑 안 된 블록을 기록한다. 차단만 하지 않고 해제 경로를 준다."""
    key = f"{block_id}|{name}|{shape}"
    if key in _unmapped:
        _unmapped[key]["hits"] += 1
        return
    if len(_unmapped) >= MAX_UNMAPPED:
        _unmapped.popitem(last=False)
    _unmapped[key] = {"block_id": block_id, "block_name": name, "shape": shape,
                      "sample_utterance": utterance[:60], "hits": 1}
    log.warning("[routing] UNMAPPED block id=%r name=%r shape=%s utterance=%r — "
                "config/blocks.yaml 에 한 줄 추가하면 된다",
                block_id, name, shape, utterance[:40])


def unmapped_blocks() -> list[dict]:
    return list(_unmapped.values())


def clear_unmapped() -> None:
    _unmapped.clear()
