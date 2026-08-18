"""학사일정 항목 검색 — "수강신청 언제야" 같은 특정 조회.

★ 왜 블록을 나누지 않고 서버에서 가르나
  학생은 블록 경계를 모른다. 한 블록에 '마감 뭐 있어'와 '수강신청 언제야'가
  섞여 들어온다. params 가 비면 utterance 를 보는 것과 같은 패턴이다.

★ 유사도 매칭이 아니다
  utterance_aliases(학생이 쓰는 말)와 title_keywords(원천 제목에 든 말)를
  사람이 적어 둔다. 자동 확장하지 않는다 — 그러면 엉뚱한 항목이 걸린다.

★ '못 찾음'과 '자료 없음'을 가른다
  지금까지 '없는 걸 있다고' 하는 오류만 막아왔는데, '있는 걸 없다고' 하는 것도
  학생에겐 똑같이 못 믿을 챗봇이다. C분기를 내기 전에 정말 조회했는지 확인한다.
"""

from __future__ import annotations

import datetime as dt
import functools
import pathlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import yaml

CONFIG_PATH = pathlib.Path(__file__).resolve().parents[1] / "config" / "calendar_topics.yaml"


class Outcome(str, Enum):
    FOUND = "found"                 # 조회했고 찾았다
    NOT_FOUND = "not_found"         # ★ 조회했는데 그 항목이 없다
    NO_DATA = "no_data"             # ★ 조회할 자료 자체가 없다 (크롤 실패/미수집)
    NO_TOPIC = "no_topic"           # 무엇을 묻는지 못 알아냈다 → 목록으로


@dataclass
class Topic:
    key: str
    label: str
    title_keywords: list[str]
    see_also: str | None = None


@dataclass
class SearchResult:
    outcome: Outcome
    topic: Topic | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)
    searched_total: int = 0          # 실제로 훑은 일정 건수. 조회 여부의 증거다


@functools.lru_cache(maxsize=4)
def _load(path: str) -> dict:
    return yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8")) or {}


def load(path: pathlib.Path | None = None) -> dict:
    return _load(str(path or CONFIG_PATH))


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", (s or ""))


def topic_pairs(path: pathlib.Path | None = None) -> list[tuple[str, Topic]]:
    """(별칭, 주제) 를 **긴 별칭부터** 정렬해 돌려준다.

    '수강신청변경'이 '수강신청'보다 먼저 걸려야 한다.
    """
    out: list[tuple[str, Topic]] = []
    for key, spec in (load(path).get("topics") or {}).items():
        t = Topic(key=key, label=spec.get("label", key),
                  title_keywords=spec.get("title_keywords") or [],
                  see_also=spec.get("see_also"))
        for a in spec.get("utterance_aliases") or []:
            out.append((_norm(a), t))
    return sorted(out, key=lambda x: -len(x[0]))


def find_topic(utterance: str, path: pathlib.Path | None = None) -> Topic | None:
    """★ 별칭이 **낱말 안쪽**에 걸리면 안 된다 (2026-08-18)

    '시험' 을 별칭에 넣어야 '시험 언제' 가 학사일정으로 간다 —
    학생이 제일 많이 묻는 축인데 지금은 검색 되묻기로 샌다.
    그런데 그냥 넣으면 '종합시험'(공지 88건)·'외국어시험'(29건)·
    '임용시험'·'자격시험' 이 전부 걸리고, title_keywords 가
    [중간시험, 기말시험] 이라 **중간시험 완료로 답한다.** 확신 오답이다.

    같은 병을 이미 두 번 봤다 (학자금 대출 · 인**공지**능).
    tools/alias_traps.py 가 쓰는 잣대를 여기에도 붙인다 —
    앞 글자가 한글이면 그건 다른 낱말의 일부다.

        시험 언제      → 걸린다 (앞이 문장 처음)
        종합시험 언제   → 안 걸린다 ('합' 뒤)
        동아리 등록 기간 → 안 걸린다 ('리' 뒤)   ← '등록' 덫도 같이 막힌다

    ★ 뒤는 안 본다. '시험기간' · '수강신청기간' 처럼 뒤에 말이 붙는 건
      우리말에서 자연스럽고, 실제로 그렇게 묻는다.
    """
    u = _norm(utterance)
    if not u:
        return None
    for alias, topic in topic_pairs(path):
        i = u.find(alias)
        while i != -1:
            if i == 0 or not _HANGUL.match(u[i - 1]):
                return topic
            i = u.find(alias, i + 1)
    return None


_HANGUL = re.compile(r"[가-힣]")


def search(entries: list[dict[str, Any]], utterance: str, *,
           path: pathlib.Path | None = None) -> SearchResult:
    """일정 목록에서 주제에 맞는 항목을 찾는다.

    entries 는 **넓게** 조회해 온 것이어야 한다. 14일치만 보고 '없다'고 하면
    있는 걸 없다고 하는 오류가 된다.
    """
    topic = find_topic(utterance, path)
    if topic is None:
        return SearchResult(Outcome.NO_TOPIC, searched_total=len(entries))
    if not entries:
        # ★ 자료 자체가 없다. '그 항목이 없다'와 다르다.
        return SearchResult(Outcome.NO_DATA, topic=topic, searched_total=0)

    hits = [e for e in entries
            if any(_norm(k) in _norm(e["title"]) for k in topic.title_keywords)]
    if not hits:
        return SearchResult(Outcome.NOT_FOUND, topic=topic,
                            searched_total=len(entries))
    return SearchResult(Outcome.FOUND, topic=topic, entries=hits,
                        searched_total=len(entries))


def rank(entries: list[dict[str, Any]], today: str) -> list[dict[str, Any]]:
    """다가오는 것 먼저, 그 다음 최근 지난 것.

    지난 것도 버리지 않는다 — '수강신청 언제였지'도 유효한 질문이고,
    지난 일정을 감추면 학생이 놓친 걸 확인할 방법이 없다.
    """
    d0 = dt.date.fromisoformat(today)

    def key(e):
        start = dt.date.fromisoformat(e["start_date"])
        end = dt.date.fromisoformat(e["end_date"]) if e.get("end_date") else start
        if end >= d0:
            return (0, (start - d0).days)      # 진행 중·예정: 가까운 순
        return (1, (d0 - start).days)          # 지난 것: 최근 순
    return sorted(entries, key=key)
