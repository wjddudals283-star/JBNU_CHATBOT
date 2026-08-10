"""답변 분기 판정 — 질의 계층.

저장하는 건 관측. 결합은 여기서 매번 다시 한다.
파싱 시점에 추론한 결론이 status='verified' 로 굳으면
"이 closed 는 눈으로 본 건가 추측한 건가"를 아무도 답할 수 없게 된다.

판정표 (02_핸드오프.md §4)

| meal_service     | serves | 답변 |
|------------------|--------|------|
| operating + items| —      | A    메뉴
| closed_temporary | —      | B    "오늘은 쉬어요"
| unknown          | False  | B    "○○은 조식은 운영하지 않아요"
| unknown          | True   | C-1  "아직 올라오지 않았어요"
| unknown          | None   | C-2  "확인하지 못했어요"

여기에 신선도 게이트가 앞선다 — stale 이면 값이 있어도 C-2 다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from store import repo


class Branch(str, Enum):
    A = "A"          # 사실 있음 + 신선함
    B = "B"          # 명확한 미운영 (관측 또는 운영시간 근거)
    C1 = "C-1"       # 운영하는 건 아는데 메뉴가 아직 없음
    C2 = "C-2"       # 확인 불가 — 모른다

    @property
    def is_refusal(self) -> bool:
        return self in (Branch.C1, Branch.C2)


@dataclass
class MealAnswer:
    branch: Branch
    reason: str
    rows: list[dict[str, Any]]
    observed_at: str | None = None
    serves: bool | None = None
    stale: bool = False
    # 판단의 근거가 된 운영시간 관측. 답변에 그대로 보여준다.
    # ★ 단서(caveat)를 쓰는 대신 근거를 노출한다 — 학생은 단서를 검증할 수 없지만
    #   시간표는 검증할 수 있다. 시간표가 이상하면 학생이 먼저 알아챈다.
    hours: list[dict[str, Any]] = field(default_factory=list)

    @property
    def operating_rows(self) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["service_status"] == "operating" and r["items"]]


def resolve_meal(conn, *, facility_id: str, date: str, meal_type: str,
                 now: dt.datetime, term: str | None = None) -> MealAnswer:
    """식단 질문 하나에 대한 분기 판정.

    이 함수는 사실을 만들지 않는다. 이미 저장된 관측 둘(식단, 운영시간)을
    읽어 어떤 문장을 낼지만 정한다. 다음 호출 때 다시 판정한다.
    """
    facts = repo.query_meal(conn, facility_id=facility_id, date=date,
                            meal_type=meal_type, now=now)
    # B 분기에서 **근거로 보여줄** 운영시간. 판단이 아니라 표시에 쓴다.
    hours = repo.query_operating_hours(conn, facility_id=facility_id, on_date=date)

    def answer(b: Branch, reason: str, rows=(), **kw) -> MealAnswer:
        return MealAnswer(b, reason, rows=list(rows), hours=hours, **kw)

    # 신선도 게이트가 먼저다. 오래된 값은 값이 있어도 쓰지 않는다.
    if facts.found and facts.stale:
        return answer(Branch.C2, "stale", stale=True,
                      observed_at=_latest(facts.rows))

    serves = repo.serves_meal(conn, facility_id=facility_id, date=date,
                              meal_type=meal_type, term=term)

    if not facts.found:
        # 레코드 자체가 없다 = 크롤이 아직 닿지 않았거나 격리됐다.
        if serves is False:
            return answer(Branch.B, "not_offered", serves=serves)
        if serves is True:
            return answer(Branch.C1, "not_published", serves=serves)
        return answer(Branch.C2, "no_record", serves=serves)

    observed = _latest(facts.rows)

    if any(r["service_status"] == "operating" and r["items"] for r in facts.rows):
        return answer(Branch.A, "operating", facts.rows,
                      observed_at=observed, serves=serves)

    # 원천이 "운영없음"을 명시한 경우 — 관측된 미운영이다.
    if facts.rows and all(r["service_status"] == "closed_temporary" for r in facts.rows):
        return answer(Branch.B, "closed_observed", facts.rows,
                      observed_at=observed, serves=serves)

    # 남은 건 unknown (칸이 비어 있었다). 여기서 운영시간과 결합한다.
    if serves is False:
        return answer(Branch.B, "not_offered", facts.rows,
                      observed_at=observed, serves=serves)
    if serves is True:
        return answer(Branch.C1, "not_published", facts.rows,
                      observed_at=observed, serves=serves)
    return answer(Branch.C2, "unknown", facts.rows,
                  observed_at=observed, serves=serves)


def _latest(rows: list[dict[str, Any]]) -> str | None:
    stamps = [r["observed_at"] for r in rows if r.get("observed_at")]
    return max(stamps) if stamps else None
