"""검증 게이트 (01_설계.md §5).

  1. 스키마 검증      필수 필드 / 타입 / enum
  2. 도메인 규칙      가격 범위 / 날짜 / 문자열 길이
  3. 이상 탐지        0건인데 이전엔 정상 / 중복 과다 / 급변
  4. 정렬 앵커 검증 ★ 파싱된 날짜·요일 헤더가 요청 범위와 순서까지 일치하는지
  5. 교차 검증        원천 2개 불일치 → 1차 채택 + conflict
  6. T3/T4 전용       해시 변경 / valid_to 누락

4번을 따로 두는 이유: "파싱 성공 + 값 정상 + 정렬 틀림"은 1~3번을 전부 통과한다.
예외도 안 나고 스키마도 맞고 개수도 맞는데 메뉴만 하루씩 밀린다.
값 검증과 별개로 **구조 검증**이 필요하다.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

PRICE_MIN, PRICE_MAX = 500, 20_000
# ★ 40 → 25. 학교 XHR 에서 <pre> 줄바꿈을 놓쳐 4품목이 한 덩어리(약 30자)로
#   들어왔는데 40자 제한을 통과했다. 구분자를 놓친 파서는 긴 이름으로 드러난다.
NAME_LEN_MIN, NAME_LEN_MAX = 1, 25
ANOMALY_DELTA = 0.7
MAX_ITEMS_PER_CELL = 30
# 품목이 1건뿐인데 이름 안에 공백이 여러 번 있으면 구분자를 놓친 것으로 의심한다.
SUSPICIOUS_SPACE_COUNT = 2

SERVICE_STATUS = {"operating", "closed_temporary", "unknown"}
MEAL_TYPES = {"breakfast", "lunch", "dinner"}

WEEKDAY_KO = ["월", "화", "수", "목", "금", "토", "일"]
WEEKDAY_KO_FULL = {
    "일요일": 6, "월요일": 0, "화요일": 1, "수요일": 2,
    "목요일": 3, "금요일": 4, "토요일": 5,
}


class ParseError(RuntimeError):
    """표 자체를 못 찾았거나 구조가 깨졌다. 레코드를 만들지 않는다."""


class AnchorMismatch(ParseError):
    """정렬 앵커 불일치. 조용히 하루 밀리는 오류를 여기서 잡는다."""


@dataclass
class ColumnAnchor:
    """헤더 한 칸이 주장하는 날짜 정보."""
    index: int
    weekday_label: str      # '수요일'
    day_of_month: int       # 3
    computed_date: str      # '2026-06-03'


@dataclass
class ValidationReport:
    ok: list = field(default_factory=list)
    quarantined: list[tuple[object, str]] = field(default_factory=list)

    @property
    def counts(self) -> tuple[int, int]:
        return len(self.ok), len(self.quarantined)


# ═══════════════════════════════════════════════════════════════
# 4. 정렬 앵커 검증
# ═══════════════════════════════════════════════════════════════

def check_alignment(anchors: list[ColumnAnchor]) -> None:
    """헤더가 주장하는 (요일, 일자)와 계산된 날짜가 순서까지 맞는지.

    두 가지를 본다.
      · 요일 이름이 계산된 날짜의 실제 요일과 같은가
      · 괄호 안 일자(NN)가 계산된 날짜의 일(day)과 같은가

    둘 다 원천이 스스로 적어둔 값이므로 **외부 앵커**다.
    파서가 한 칸 밀리면 두 검사가 동시에 깨진다.
    """
    if not anchors:
        raise AnchorMismatch("헤더에서 날짜 앵커를 하나도 찾지 못했다")

    for a in anchors:
        d = dt.date.fromisoformat(a.computed_date)

        expected_wd = WEEKDAY_KO_FULL.get(a.weekday_label)
        if expected_wd is None:
            raise AnchorMismatch(f"[{a.index}] 알 수 없는 요일 표기: {a.weekday_label!r}")
        if d.weekday() != expected_wd:
            raise AnchorMismatch(
                f"[{a.index}] 요일 불일치 — 헤더 {a.weekday_label!r} 인데 "
                f"계산된 {a.computed_date} 는 {WEEKDAY_KO[d.weekday()]}요일이다. "
                f"열이 밀렸을 가능성이 높다"
            )
        if d.day != a.day_of_month:
            raise AnchorMismatch(
                f"[{a.index}] 일자 불일치 — 헤더 ({a.day_of_month:02d}) 인데 "
                f"계산된 날짜는 {a.computed_date}. 열이 밀렸을 가능성이 높다"
            )

    days = [dt.date.fromisoformat(a.computed_date) for a in anchors]
    gaps = {(b - a).days for a, b in zip(days, days[1:])}
    if gaps and gaps != {1}:
        raise AnchorMismatch(f"열 사이 날짜 간격이 1일이 아니다: {sorted(gaps)}")


def check_cell_crosscheck(items: list[str], title_items: list[str]) -> str | None:
    """같은 셀의 두 인코딩(<br> 목록 vs title 속성)을 대조한다.

    원천이 같은 데이터를 두 번 적어둔 덕에 공짜로 얻는 교차 검증이다.
    불일치는 파싱이 뭔가를 놓쳤다는 뜻이므로 격리한다.
    """
    if not title_items:
        return None  # title 이 없는 셀은 대조 대상이 아니다
    a = [re.sub(r"\s+", "", x) for x in items]
    b = [re.sub(r"\s+", "", x) for x in title_items]
    if a != b:
        return f"셀 교차검증 불일치: <br>={a} vs title={b}"
    return None


# ═══════════════════════════════════════════════════════════════
# 1~2. 스키마 · 도메인 규칙
# ═══════════════════════════════════════════════════════════════

def validate_meal(meal) -> str | None:
    """레코드 1건. 통과하면 None, 실패하면 사유 문자열(→ quarantine)."""
    if meal.meal_type not in MEAL_TYPES:
        return f"알 수 없는 meal_type: {meal.meal_type!r}"
    if meal.service_status not in SERVICE_STATUS:
        return f"알 수 없는 service_status: {meal.service_status!r}"
    try:
        dt.date.fromisoformat(meal.date)
    except ValueError:
        return f"날짜 형식 오류: {meal.date!r}"
    if meal.zone is None or meal.corner is None:
        return "zone/corner 는 NULL 일 수 없다 (UNIQUE 무력화)"

    if meal.service_status != "operating" and meal.items:
        return f"{meal.service_status} 인데 품목이 {len(meal.items)}건 있다"
    if meal.service_status == "operating" and not meal.items:
        return "operating 인데 품목이 없다"

    if len(meal.items) > MAX_ITEMS_PER_CELL:
        return f"품목 과다: {len(meal.items)}건"

    names = [i.name for i in meal.items]
    for n in names:
        if not (NAME_LEN_MIN <= len(n) <= NAME_LEN_MAX):
            return f"품목명 길이 이탈: {n!r} ({len(n)}자)"

    # 구분자 놓침 탐지 — 한 덩어리로 들어온 셀은 값도 정상이고 개수도 1건이라
    # 다른 게이트를 전부 통과한다. 실제로 <pre> 줄바꿈을 놓쳐 겪은 유형이다.
    if len(names) == 1 and names[0].count(" ") >= SUSPICIOUS_SPACE_COUNT:
        return (f"품목이 1건인데 내부 공백이 {names[0].count(' ')}회다 — "
                f"구분자를 놓쳤을 가능성: {names[0]!r}")

    dup = {n for n in names if names.count(n) > 2}
    if dup:
        return f"동일 품목 3회 이상 중복: {sorted(dup)}"
    return None


def validate_price_row(price_text: str, price_min: int,
                       price_max: int | None) -> str | None:
    if not price_text.strip():
        return "price_text 가 비어 있다 (원문 표기는 필수)"
    if not (PRICE_MIN <= price_min <= PRICE_MAX):
        return f"가격 범위 이탈: {price_min}"
    if price_max is not None:
        if price_max < price_min:
            return f"price_max({price_max}) < price_min({price_min})"
        if not (PRICE_MIN <= price_max <= PRICE_MAX):
            return f"가격 범위 이탈: {price_max}"
    return None


# ═══════════════════════════════════════════════════════════════
# 3. 이상 탐지
# ═══════════════════════════════════════════════════════════════

def detect_anomaly(parsed_count: int, previous_count: int | None) -> str | None:
    """이전 주기 대비 급변. 셀렉터가 조용히 깨졌을 때의 신호다."""
    if previous_count is None:
        return None
    if parsed_count == 0 and previous_count > 0:
        return f"파싱 0건인데 직전에는 {previous_count}건이었다 (셀렉터 깨짐 의심)"
    if previous_count > 0:
        delta = abs(parsed_count - previous_count) / previous_count
        if delta > ANOMALY_DELTA:
            return (f"직전 대비 {delta:.0%} 변동 "
                    f"({previous_count} → {parsed_count})")
    return None
