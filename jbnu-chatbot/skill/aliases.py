"""별칭 사전 로더 + 발화 기반 슬롯 보완.

★ 이건 '자체 NLU'가 아니다.
  인텐트(블록) 분류는 여전히 오픈빌더가 한다. 우리는 **이미 매칭된 블록 안에서**
  슬롯을 채운다. 오픈빌더는 발화마다 태깅해야 params 가 나오는데,
  학생 자유 발화는 태깅이 안 되므로 params 가 계속 빈다.
  그 빈칸을 사전 조회로 메우는 것뿐이다 — 새 의도를 만들지 않는다.

★ 긴 별칭 우선
  '의대식당'이 '의대'보다 먼저 걸려야 한다. 짧은 걸 먼저 보면
  '의대식당 메뉴'에서 '의대'만 잡고 끝난다.
"""

from __future__ import annotations

import functools
import pathlib
import re

import yaml

CONFIG_PATH = pathlib.Path(__file__).resolve().parents[1] / "config" / "aliases.yaml"


@functools.lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))


def load(path: pathlib.Path | None = None) -> dict:
    return _load(str(path or CONFIG_PATH))


def _pairs(group: dict) -> list[tuple[str, str]]:
    """(별칭, 정규ID) 목록을 **긴 것부터** 정렬해 돌려준다."""
    out: list[tuple[str, str]] = []
    for canonical_id, spec in group.items():
        for a in spec.get("aliases") or []:
            out.append((a, canonical_id))
    return sorted(out, key=lambda x: -len(x[0]))


def facility_pairs(path: pathlib.Path | None = None) -> list[tuple[str, str]]:
    return _pairs(load(path).get("facility") or {})


def meal_pairs(path: pathlib.Path | None = None) -> list[tuple[str, str]]:
    return _pairs(load(path).get("meal_type") or {})


def canonical_name(facility_id: str, path: pathlib.Path | None = None) -> str:
    spec = (load(path).get("facility") or {}).get(facility_id) or {}
    return spec.get("canonical", facility_id)


def all_facility_ids(path: pathlib.Path | None = None) -> list[str]:
    return list((load(path).get("facility") or {}).keys())


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def find_facility(text: str, path: pathlib.Path | None = None) -> str | None:
    """발화에서 식당을 찾는다. 없으면 None (억지로 고르지 않는다)."""
    t = _norm(text)
    if not t:
        return None
    for alias, fid in facility_pairs(path):
        if alias in t:
            return fid
    return None


def find_meal_type(text: str, path: pathlib.Path | None = None) -> str | None:
    t = _norm(text)
    if not t:
        return None
    for alias, meal in meal_pairs(path):
        if alias in t:
            return meal
    return None


def resolve_facility(params: dict, utterance: str,
                     path: pathlib.Path | None = None) -> tuple[str | None, str]:
    """식당 결정. (facility_id, 출처) 를 돌려준다.

    출처를 같이 주는 이유 — 오픈빌더가 뽑은 건지 우리가 발화에서 보완한 건지
    로그로 구분해야 엔티티 등록 효과를 볼 수 있다.
    """
    raw = str(params.get("outlet") or params.get("outlet_name")
              or params.get("facility") or "").strip()
    if raw:
        if raw in all_facility_ids(path):
            return raw, "params:id"
        hit = find_facility(raw, path)
        if hit:
            return hit, "params:alias"
    hit = find_facility(utterance, path)
    if hit:
        return hit, "utterance"
    return None, "none"
