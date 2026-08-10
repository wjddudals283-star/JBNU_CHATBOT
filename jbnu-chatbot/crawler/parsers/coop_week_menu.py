"""생협 주간식단 파서 — get_cafeteria_menu.php (JSON). ★1차 원천.

    POST https://coopjbnu.kr/function/get_cafeteria_menu.php
      data: date=YYYYMMDD   (now 를 **생략**하면 그 주 월~금 15건이 한 번에 온다)
      인증·Referer·쿠키 불필요

응답 스키마 (실측, 키 8개가 전부)
    {"status":"success",
     "list":[{"restNm":"후생관","date":"2026-08-10","day":0,
              "subData":[{"cate1":"점심","cate2":"한식","cate3":"찌개*돌솥",
                          "diet":"돈목살짜글이*계란후라이\\n\\n"}]}]}

이 원천을 1차로 두는 이유
  · date 파라미터로 과거·미래 조회가 된다 → 크롤이 하루 밀려도 백필이 가능하다
  · 학교 XHR 은 date 가 없어 이번 주만 긁힌다 → 놓친 주를 영영 못 채운다

없는 것
  · 가격 (menu_price 는 학교 XHR 단가표에서만 온다)
  · 운영시간
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field

from crawler import validate
from crawler.validate import AnchorMismatch, ColumnAnchor, ParseError
from store.repo import ParsedItem, ParsedMeal

SOURCE_KEY = "coop_week_menu"
EXTRACTION_METHOD = "json_api"
CONFIDENCE = 0.95

FACILITY_BY_REST = {
    "후생관": "jbnu:facility/후생관-푸드코트",
    "진수원": "jbnu:facility/진수원",
    "의대식당": "jbnu:facility/의대식당",
    "의대": "jbnu:facility/의대식당",
}

MEAL_LABEL_TO_TYPE = {"조식": "breakfast", "점심": "lunch", "석식": "dinner",
                      "아침": "breakfast", "중식": "lunch", "저녁": "dinner"}

# 원천이 명시하는 미운영. 이게 menu_item.name 이 되면
# 챗봇이 "오늘 메뉴: 운영없음"이라고 답한다.
CLOSED_MARKERS = {"운영없음", "미운영", "식사없음"}

# day 필드: 0=월 … 4=금 (실측). 파이썬 weekday() 와 같다.
DAY_FIELD_TO_WEEKDAY = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4}


@dataclass
class ParseResult:
    meals: list[ParsedMeal] = field(default_factory=list)
    quarantined: list[tuple[ParsedMeal, str]] = field(default_factory=list)
    anchors: list[ColumnAnchor] = field(default_factory=list)
    week_start: str = ""
    empty_list: bool = False

    @property
    def counts(self) -> tuple[int, int]:
        return len(self.meals), len(self.quarantined)


def _split_diet(diet: str) -> list[str]:
    """diet 문자열 → 품목 목록.

    구분자는 줄바꿈이고, 한 줄에 '/' 로 나열한 셀도 있다
    (예: '비빔밥/돈육불고기비빔밥/주꾸미비빔밥').
    ★ 공백으로는 나누지 않는다 — 품목명 안에 공백이 들어간다.
    """
    out: list[str] = []
    for line in (diet or "").splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        for part in line.split("/"):
            part = part.strip()
            if part:
                out.append(part)
    return out


def parse(payload: str | dict, *, expect_week_of: str | None = None) -> ParseResult:
    """JSON 응답 → ParsedMeal 목록.

    빈 칸 처리
      diet 에 품목 있음        → operating
      diet 가 '운영없음'       → closed_temporary  (원천이 명시한 미운영)
      diet 가 빈 문자열        → unknown          ★ 방학이라고 단정하지 않는다
      list 가 []               → 레코드 0건. 미게시다. parse_error 가 아니다
      status != 'success'      → ParseError
    """
    data = json.loads(payload) if isinstance(payload, str) else payload
    if not isinstance(data, dict):
        raise ParseError(f"최상위가 객체가 아니다: {type(data).__name__}")
    if data.get("status") != "success":
        raise ParseError(f"status={data.get('status')!r} — 성공 응답이 아니다")
    if "list" not in data:
        raise ParseError("응답에 list 키가 없다 (스키마 변경 의심)")

    rows = data.get("list") or []
    result = ParseResult()
    if not rows:
        # ★ 미게시다. 토·일이거나 아직 안 올라온 주.
        #   '운영 안 함'이 아니므로 레코드를 만들지 않는다.
        result.empty_list = True
        return result

    seen_dates: set[str] = set()
    for item in rows:
        rest = (item.get("restNm") or "").strip()
        fid = FACILITY_BY_REST.get(rest)
        if fid is None:
            raise ParseError(f"모르는 식당 표기: {rest!r} (별칭 사전 갱신 필요)")

        date = (item.get("date") or "").strip()
        try:
            d = dt.date.fromisoformat(date)
        except ValueError:
            raise ParseError(f"날짜 형식 오류: {date!r}") from None
        seen_dates.add(date)

        # ★ day 필드와 date 를 대조한다. 같은 정보가 두 번 들어 있으므로 공짜 검증이다.
        day = item.get("day")
        if day is not None and day in DAY_FIELD_TO_WEEKDAY:
            if d.weekday() != DAY_FIELD_TO_WEEKDAY[day]:
                raise AnchorMismatch(
                    f"{rest} {date}: day={day} 인데 실제 요일 인덱스는 {d.weekday()} 다"
                )

        for sub in item.get("subData") or []:
            meal_type = MEAL_LABEL_TO_TYPE.get((sub.get("cate1") or "").strip())
            if meal_type is None:
                continue
            zone = (sub.get("cate2") or "").strip()
            corner = (sub.get("cate3") or "").strip()
            diet = sub.get("diet") or ""
            names = _split_diet(diet)

            if not names:
                meal = ParsedMeal(facility_id=fid, date=date, meal_type=meal_type,
                                  service_status="unknown", zone=zone, corner=corner,
                                  items=[], raw_text=diet)
            elif all(n in CLOSED_MARKERS for n in names):
                meal = ParsedMeal(facility_id=fid, date=date, meal_type=meal_type,
                                  service_status="closed_temporary", zone=zone,
                                  corner=corner, items=[], note=names[0], raw_text=diet)
            else:
                meal = ParsedMeal(
                    facility_id=fid, date=date, meal_type=meal_type,
                    service_status="operating", zone=zone, corner=corner,
                    raw_text=diet,
                    items=[ParsedItem(name=n, display_order=i)
                           for i, n in enumerate(names)])

            reason = validate.validate_meal(meal)
            (result.quarantined.append((meal, reason)) if reason
             else result.meals.append(meal))

    ordered = sorted(seen_dates)
    result.week_start = ordered[0]
    result.anchors = [
        ColumnAnchor(index=i, weekday_label=_WD_KO[dt.date.fromisoformat(x).weekday()],
                     day_of_month=dt.date.fromisoformat(x).day, computed_date=x)
        for i, x in enumerate(ordered)
    ]
    validate.check_alignment(result.anchors)

    if expect_week_of:
        want = dt.date.fromisoformat(expect_week_of)
        got = dt.date.fromisoformat(result.week_start)
        if abs((got - want).days) > 7:
            raise AnchorMismatch(
                f"요청한 주({expect_week_of})와 응답 주({result.week_start})가 7일 넘게 벌어졌다")
    return result


_WD_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
