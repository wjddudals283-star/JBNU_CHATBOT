"""학교 식단안내 XHR 파서 — dataAjax.do?type=day.

이 원천 하나에서 셋이 나온다.
  · 운영시간 (T2)  — 다른 원천에 없다. B분기 판정의 근거
  · 메뉴 단가표 (T2) — 가격의 유일한 공식 출처
  · 주간 식단 (T1) — 생협 API 와 교차검증할 2차 원천

응답 구조 (실측, 테이블 10개)
    [0] 진수원 운영시간   [1] 진수원 가격   [2] 진수원 주간식단
    [3] 의대식당 운영시간 [4] 의대식당 가격 [5] 의대식당 주간식단
    [6] 후생관 운영시간   [7] 후생관 가격(위임) [8] 후생관 주간식단
    [9] 후생관 메뉴 단가표 (34행, rowspan/colspan 사용)

★ rowspan/colspan 정규화가 필수다. 학교 식단표는 "이번 주 내내 같은 메뉴"인 코너를
  colspan=5 로 묶는다. 정규화 없이 읽으면 월요일에만 메뉴가 있고 화~금이 비어 보인다.
★ row.css("td,th") 금지 — 문서 순서를 안 지킨다. row_cells() 를 쓴다.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

from crawler import validate
from crawler.validate import AnchorMismatch, ColumnAnchor, ParseError
from store.repo import ParsedItem, ParsedMeal

SOURCE_KEY = "jbnu_cafeteria_day"
EXTRACTION_METHOD = "html_selector"
CONFIDENCE = 0.95

# 식당 표기 → facility_id
FACILITY_BY_NAME = {
    "진수원": "jbnu:facility/진수원",
    "의대식당": "jbnu:facility/의대식당",
    "후생관": "jbnu:facility/후생관-푸드코트",
}

MEAL_LABEL_TO_TYPE = {"조식": "breakfast", "점심": "lunch", "석식": "dinner",
                      "아침": "breakfast", "중식": "lunch", "저녁": "dinner"}

CLOSED_MARKERS = {"운영없음", "미운영", "식사없음"}

_DATE_IN_HEADER = re.compile(r"(?P<wd>[월화수목금토일])요일\s*(?P<date>\d{4}-\d{2}-\d{2})")
_TIME_RANGE = re.compile(r"(?P<from>\d{1,2}:\d{2})\s*~\s*(?P<to>\d{1,2}:\d{2})")
_WON = re.compile(r"([\d,]+)\s*원")
_AUDIENCE = re.compile(r"(구성원|외부인)\s*[:：]\s*([\d,]+)\s*원")

WEEKDAY_TO_INDEX = {"일": 0, "월": 1, "화": 2, "수": 3, "목": 4, "금": 5, "토": 6}


# ── 문서 순서를 지키는 셀 순회 + rowspan/colspan 정규화 ──────────────

def row_cells(row):
    """★ row.css("td,th") 금지. 자식 노드를 순회해 문서 순서를 지킨다."""
    return [n for n in row.iter(include_text=False) if n.tag in ("td", "th")]


def _txt(node) -> str:
    return re.sub(r"\s+", " ", (node.text() or "")).strip()


def normalize_grid(table) -> list[list[dict]]:
    """rowspan/colspan 을 펼쳐 직사각 격자로 만든다.

    각 칸은 {'text','tag','origin'} — origin 은 (row, col) 원본 위치라
    펼쳐진 칸인지(origin != 자기 위치) 구분할 수 있다.
    """
    rows = table.css("tr")
    grid: list[list[dict | None]] = [[] for _ in rows]
    occupied: dict[tuple[int, int], dict] = {}

    for r, row in enumerate(rows):
        c = 0
        for cell in row_cells(row):
            while (r, c) in occupied:
                c += 1
            rs = int(cell.attributes.get("rowspan") or 1)
            cs = int(cell.attributes.get("colspan") or 1)
            payload = {"text": _txt(cell), "tag": cell.tag, "origin": (r, c),
                       "rowspan": rs, "colspan": cs,
                       # ★ raw 는 줄바꿈을 보존한다. 메뉴 항목이 <pre> 안에서
                       #   줄바꿈으로 구분되므로 _txt() 로 뭉개면 한 덩어리가 된다.
                       "raw": (cell.text() or ""),
                       # 셀마다 data-date 가 붙어 있다. 열 위치와 별개인 독립 신호다.
                       "date_attr": cell.attributes.get("data-date")}
            for dr in range(rs):
                for dc in range(cs):
                    occupied[(r + dr, c + dc)] = payload
            c += cs

    width = max((k[1] for k in occupied), default=-1) + 1
    out: list[list[dict]] = []
    for r in range(len(rows)):
        out.append([occupied.get((r, c), {"text": "", "tag": "td",
                                          "origin": (r, c), "rowspan": 1, "colspan": 1})
                    for c in range(width)])
    return out


# ── 결과 ────────────────────────────────────────────────────────

@dataclass
class ParsedHours:
    facility_id: str
    term: str
    weekday: int
    meal_type: str
    is_closed: bool
    open_time: str | None = None
    close_time: str | None = None
    note: str | None = None


@dataclass
class ParsedPrice:
    facility_id: str
    name: str
    price_text: str
    price_min: int
    price_max: int | None
    audience: str = "전체"
    category: str | None = None
    corner: str | None = None
    note: str | None = None


@dataclass
class ParseResult:
    meals: list[ParsedMeal] = field(default_factory=list)
    quarantined: list[tuple[ParsedMeal, str]] = field(default_factory=list)
    hours: list[ParsedHours] = field(default_factory=list)
    prices: list[ParsedPrice] = field(default_factory=list)
    anchors: list[ColumnAnchor] = field(default_factory=list)
    week_start: str = ""
    complete_hours_facilities: set[str] = field(default_factory=set)

    @property
    def counts(self) -> tuple[int, int]:
        return len(self.meals), len(self.quarantined)


# ── 운영시간 ────────────────────────────────────────────────────

def parse_hours_table(table, facility_id: str) -> list[ParsedHours]:
    """운영시간표 1개.

    실측 형태:  ['점심', '평일 11:30 ~ 14:00', '주말·공휴일 미운영']

    ★ '주말·공휴일 미운영'은 원천이 **명시한** 부정이다. 행으로 남긴다.
      행의 부재로 미운영을 추론하지 않기 위해서다.
    """
    out: list[ParsedHours] = []
    for row in table.css("tr"):
        cells = [_txt(c) for c in row_cells(row)]
        if not cells:
            continue
        meal = MEAL_LABEL_TO_TYPE.get(cells[0])
        if meal is None:
            continue
        body = " ".join(cells[1:])

        m = _TIME_RANGE.search(body)
        if m:
            for wd in (1, 2, 3, 4, 5):   # 평일 = 월~금
                out.append(ParsedHours(
                    facility_id=facility_id, term="unspecified", weekday=wd,
                    meal_type=meal, is_closed=False,
                    open_time=m.group("from"), close_time=m.group("to"),
                    note=cells[1] if len(cells) > 1 else None))

        if "미운영" in body:
            closed_wd = []
            if "주말" in body:
                closed_wd += [0, 6]          # 일, 토
            if "공휴일" in body:
                closed_wd += [7]
            for wd in closed_wd:
                out.append(ParsedHours(
                    facility_id=facility_id, term="unspecified", weekday=wd,
                    meal_type=meal, is_closed=True,
                    note="주말·공휴일 미운영 (원천 명시)"))
    return out


# ── 단가표 ──────────────────────────────────────────────────────

def _parse_price_text(text: str) -> tuple[str, int, int | None] | None:
    """'6,000원 - 6,500원' / '6,000원 부터' / '4,300원' → (원문, min, max).

    ★ 범위를 하한 단일값으로 접지 않는다. price_text 를 그대로 보존한다.
    """
    nums = [int(x.replace(",", "")) for x in _WON.findall(text)]
    if not nums:
        return None
    raw = text.strip()
    if len(nums) >= 2:
        return raw, min(nums), max(nums)
    if "부터" in raw:
        return raw, nums[0], None       # 상한 미상
    return raw, nums[0], nums[0]


def parse_price_table(table, facility_id: str) -> list[ParsedPrice]:
    """후생관 메뉴 단가표. 분류/코너/메뉴/단가/비고, rowspan 다수."""
    grid = normalize_grid(table)
    if not grid:
        return []
    header = [c["text"] for c in grid[0]]
    if "단가" not in header:
        return []
    idx = {name: header.index(name) for name in header if name}
    out: list[ParsedPrice] = []
    for row in grid[1:]:
        cells = [c["text"] for c in row]
        if len(cells) < 4:
            continue
        price_cell = cells[idx.get("단가", 3)]
        parsed = _parse_price_text(price_cell)
        if parsed is None:
            continue
        raw, pmin, pmax = parsed
        name = cells[idx.get("메뉴", 2)]
        if not name:
            continue
        out.append(ParsedPrice(
            facility_id=facility_id, name=name, price_text=raw,
            price_min=pmin, price_max=pmax,
            category=cells[idx.get("분류", 0)] or None,
            corner=cells[idx.get("코너", 1)] or None,
            note=cells[idx.get("비고", 4)] if len(cells) > idx.get("비고", 4) else None,
        ))
    return out


def parse_simple_price_table(table, facility_id: str) -> list[ParsedPrice]:
    """진수원·의대식당 가격표: '백반 / 구성원 : 7,000원 / 외부인 : 8,500원'."""
    text = " ".join(_txt(c) for row in table.css("tr") for c in row_cells(row))
    hits = _AUDIENCE.findall(text)
    if not hits:
        return []
    name = _txt(table.css("tr")[0]) or "백반"
    out = []
    for audience, amount in hits:
        val = int(amount.replace(",", ""))
        out.append(ParsedPrice(facility_id=facility_id, name=name,
                               price_text=f"{amount}원", price_min=val, price_max=val,
                               audience=audience))
    return out


# ── 주간 식단 ───────────────────────────────────────────────────

def is_complete_coverage(hours: list[ParsedHours]) -> bool:
    """이 시설에 폐쇄세계 가정(hours_coverage='complete')을 켤 수 있는가.

    판정 기준 — 셋을 다 만족해야 한다.
      1) 시간표 파싱에 성공해 행이 1건 이상 나왔다
      2) **요일 차원이 빠짐없다** — 게시된 각 끼니에 대해 일~토(0~6) 7개 요일이
         모두 '여는 행' 또는 '명시적 미운영 행'으로 채워져 있다
      3) 그 표가 이 시설이 게시한 운영시간 전부다 (표를 통째로 파싱했다)

    2번이 핵심이다. 이게 만족되면 요일 차원에서는 폐쇄세계 가정이 아예 필요 없다 —
    '주말 미운영'이 관측된 행으로 있으니까. 그래서 'complete' 가 실제로 여는 것은
    **끼니 차원**뿐이다. 예: 진수원 시간표에 조식 행이 없다 → 조식을 안 한다.

    파싱 성공만으로 complete 를 세우면 안 된다. 일부만 긁힌 표를 complete 로 두면
    "행이 없다"가 곧 "안 한다"가 되어, 운영 중인 끼니를 미운영으로 답한다.
    """
    if not hours:
        return False
    by_meal: dict[str, set[int]] = {}
    for h in hours:
        by_meal.setdefault(h.meal_type, set()).add(h.weekday)
    return all(days >= set(range(7)) for days in by_meal.values())


def _split_menu(raw: str) -> list[str]:
    """메뉴 셀 → 품목 목록.

    1순위 구분자는 줄바꿈이다(<pre> 안). 한 줄에 '/' 로 나열한 셀도 있다
    (예: '통등심돈까스/치즈돈까스/치킨까스').

    ★ 공백으로는 나누지 않는다. '감자크림함박(배식)' 처럼 품목명 안에
      공백이 들어가는 경우가 있어 오분할 위험이 크다.
    """
    out: list[str] = []
    for line in raw.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        for part in line.split("/"):
            part = part.strip()
            if part:
                out.append(part)
    return out


def parse_menu_table(table, facility_id: str) -> tuple[list[ParsedMeal], list[ColumnAnchor]]:
    grid = normalize_grid(table)
    if not grid:
        return [], []

    # 헤더에서 날짜 앵커 추출. 헤더에 실제 날짜가 박혀 있다.
    anchors: list[ColumnAnchor] = []
    col_date: dict[int, str] = {}
    for c, cell in enumerate(grid[0]):
        m = _DATE_IN_HEADER.search(cell["text"].replace(" ", ""))
        if not m:
            m = _DATE_IN_HEADER.search(cell["text"])
        if m:
            d = m.group("date")
            anchors.append(ColumnAnchor(
                index=len(anchors),
                weekday_label=m.group("wd") + "요일",
                day_of_month=dt.date.fromisoformat(d).day,
                computed_date=d,
            ))
            col_date[c] = d
    if not anchors:
        raise ParseError(f"{facility_id}: 식단표 헤더에서 날짜를 찾지 못했다")

    validate.check_alignment(anchors)

    meals: list[ParsedMeal] = []
    label_cols = min(col_date) if col_date else 1
    for row in grid[1:]:
        labels = [c["text"] for c in row[:label_cols]]
        meal_type = None
        for lab in labels:
            if lab in MEAL_LABEL_TO_TYPE:
                meal_type = MEAL_LABEL_TO_TYPE[lab]
                break
        if meal_type is None:
            continue
        zone = labels[1] if len(labels) > 1 and labels[1] not in MEAL_LABEL_TO_TYPE else ""
        corner = labels[-1] if labels[-1] not in MEAL_LABEL_TO_TYPE else ""
        if zone == corner:
            zone = ""

        for c, date in col_date.items():
            cell = row[c]

            # ★ 셀의 data-date 와 헤더에서 계산한 열 날짜를 대조한다.
            #   두 개의 독립 인코딩이므로 정렬이 밀리면 여기서 걸린다.
            #   단, colspan 으로 펼쳐진 칸은 제외한다 — "이번 주 내내 같은 메뉴"인
            #   코너는 td 하나(colspan=5)로 묶이고 data-date 는 첫날 값만 갖는다.
            cell_date = cell.get("date_attr")
            if cell_date and cell["colspan"] == 1 and cell_date != date:
                raise AnchorMismatch(
                    f"{facility_id}: 셀 data-date={cell_date} 인데 "
                    f"헤더 기준 열 날짜는 {date} 다. 열이 밀렸다"
                )

            text = cell["text"].strip()
            if not text:
                meals.append(ParsedMeal(facility_id=facility_id, date=date,
                                        meal_type=meal_type, service_status="unknown",
                                        zone=zone, corner=corner, items=[]))
                continue
            if text in CLOSED_MARKERS:
                meals.append(ParsedMeal(facility_id=facility_id, date=date,
                                        meal_type=meal_type,
                                        service_status="closed_temporary",
                                        zone=zone, corner=corner, items=[], note=text))
                continue

            # 항목 구분자는 <pre> 안의 줄바꿈이다. '/' 로 나열된 셀도 있다.
            names = _split_menu(cell.get("raw") or text)
            meals.append(ParsedMeal(
                facility_id=facility_id, date=date, meal_type=meal_type,
                service_status="operating", zone=zone, corner=corner,
                items=[ParsedItem(name=n, display_order=i) for i, n in enumerate(names)]))
    return meals, anchors


# ── 본체 ────────────────────────────────────────────────────────

def _group_tables_by_restaurant(tree, title_class: str) -> list[tuple[str, list]]:
    """문서 순서로 훑어 (식당명, 표들) 목록을 만든다.

    제목이 표의 조상이 아니라 앞선 형제이므로 트리 구조로는 묶을 수 없다.
    traverse() 로 실제 문서 순서를 따라간다.
    """
    groups: list[tuple[str, list]] = []
    current: str | None = None
    for node in tree.root.traverse(include_text=False):
        cls = node.attributes.get("class") or ""
        if node.tag == "p" and title_class in cls.split():
            current = _txt(node)
            groups.append((current, []))
        elif node.tag == "table" and groups:
            groups[-1][1].append(node)
    return [(n, t) for n, t in groups if t]


def parse(html: str, *, selectors: dict | None = None) -> ParseResult:
    tree = HTMLParser(html)
    if "접근이 거부" in html or "접근권한이 없" in html:
        raise ParseError("403 — CSRF 토큰 없이 호출했다 (cafeteria.do GET → _csrf → 같은 세션 POST)")

    sel = selectors or {}
    title_class = sel.get("restaurant_title_class", "title")

    # ★ 제목(p.title)은 테이블의 조상이 아니라 **형제**다. 그래서 문서 순서로
    #   훑으면서 "현재 식당"을 갱신하고, 만나는 표를 거기에 귀속시킨다.
    #   css("p.title, table") 로 한 번에 뽑으면 안 된다 — 셀렉터별로 묶여
    #   순서가 깨진다(likehome 에서 하루 밀렸던 것과 같은 함정).
    groups = _group_tables_by_restaurant(tree, title_class)
    if not groups:
        raise ParseError("식당 제목과 표를 짝지을 수 없다")

    result = ParseResult()
    for name, tables in groups:
        if name not in FACILITY_BY_NAME:
            if "단가표" in name:
                for t in tables:
                    result.prices.extend(
                        parse_price_table(t, FACILITY_BY_NAME["후생관"]))
            continue

        fid = FACILITY_BY_NAME[name]
        for t in tables:
            caption = _txt(t.css_first("caption")) if t.css_first("caption") else ""
            head = " ".join(_txt(c) for c in row_cells(t.css("tr")[0])) if t.css("tr") else ""

            if "단가" in head:
                result.prices.extend(parse_price_table(t, fid))
            elif "요일" in head or _DATE_IN_HEADER.search(head):
                meals, anchors = parse_menu_table(t, fid)
                result.meals.extend(meals)
                if not result.anchors:
                    result.anchors = anchors
                    result.week_start = anchors[0].computed_date
            elif _TIME_RANGE.search(_txt(t)) or "미운영" in _txt(t):
                hrs = parse_hours_table(t, fid)
                if hrs:
                    result.hours.extend(hrs)
                    # ★ 파싱에 성공했다고 complete 가 아니다. 요일 커버리지를 실제로 검사한다.
                    if is_complete_coverage(hrs):
                        result.complete_hours_facilities.add(fid)
            elif "원" in _txt(t):
                result.prices.extend(parse_simple_price_table(t, fid))

    # 검증
    kept: list[ParsedMeal] = []
    for m in result.meals:
        reason = validate.validate_meal(m)
        if reason:
            result.quarantined.append((m, reason))
        else:
            kept.append(m)
    result.meals = kept
    return result
