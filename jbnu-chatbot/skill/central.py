"""학과 되묻기가 **막다른 길**이 되는 주제 — 중앙 문서로 보낸다.

★ 왜 (2026-08-17 실측)
  '연계전공 졸업요건' 에 "'국어국문학과 연계전공 졸업요건'처럼 학과를 붙여서"
  가 나갔다. 연계전공은 **학과 소속이 아니다** — 학생이 뭘 붙여도 답에 못 닿는다.
  되묻기가 막다른 길이 된다.

★ 목록은 config 에 둔다 (config/central_topics.yaml)
  안전 연락처·별칭과 같은 자리다. 근거(중앙 페이지 URL)와 확인자를 같이 적는다.
  ★ '학과별로 안 갈리는 주제' 만 넣는다 —
    교직·복수전공·부전공·교양은 실제로 학과별로 갈린다 (사이트 71~109곳).

★ 표를 인용하지는 않는다
  그 중앙 표는 kind='table' 이라 헤더 경계가 없어 지금 규칙으로 못 그린다.
  못 그려도 **원문까지는 보낼 수 있다.** 막다른 길을 없애는 게 먼저다.
  파서가 고쳐지면 이 답이 링크에서 표로 승격되면 된다.
"""

from __future__ import annotations

import functools
import pathlib
import re
from dataclasses import dataclass

CONFIG_PATH = (pathlib.Path(__file__).resolve().parents[1]
               / "config" / "central_topics.yaml")


@dataclass(frozen=True)
class CentralTopic:
    key: str
    label: str
    url: str
    holds: str


@functools.lru_cache(maxsize=1)
def _topics() -> list[tuple[list[str], CentralTopic]]:
    try:
        import yaml
        doc = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 — 설정이 없으면 이 기능만 안 돈다
        return []
    out = []
    for spec in (doc.get("topics") or []):
        url = str(spec.get("url") or "").strip()
        if not url:
            continue          # 링크 없이 '중앙 문서로 보낸다' 는 말이 안 된다
        out.append((
            [str(m) for m in (spec.get("match") or [])],
            CentralTopic(key=str(spec.get("key") or ""),
                         label=str(spec.get("label") or spec.get("key") or ""),
                         url=url,
                         holds=str(spec.get("holds") or "")),
        ))
    return out


def find(utterance: str) -> CentralTopic | None:
    """질문이 중앙 문서로 보낼 주제인가.

    ★ 공백을 지우고 본다 — '연계 전공' 과 '연계전공' 은 같은 말이다.
      (붙여 쓴 발화가 폴백으로 가던 것과 같은 이유다)
    """
    u = re.sub(r"\s+", "", utterance or "")
    if not u:
        return None
    for words, topic in _topics():
        for w in words:
            if re.sub(r"\s+", "", w) in u:
                return topic
    return None
