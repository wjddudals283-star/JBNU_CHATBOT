"""생활관(likehome) 주간식단 파서.

원천: https://likehome.jbnu.ac.kr/home/main/inner.php?sMenu=B7100&date=YYYY-MM-DD
      서버 렌더 HTML 테이블. date 는 그 주 **일요일**로 정규화되어 302 리다이렉트된다.

표 구조 (실측)
    thead tr : th[0]='' , th[1..7] = '일요일 (31)' … '토요일 (06)'
    tbody tr : th[0]='아침'|'점심'|'저녁' , td[0..6] = 일~토
    셀 내부  : <a class="calendar_pop" title="흰밥, 미역국, …">흰밥<br> 미역국<br> …</a>

★ selectolax 의 row.css("td,th") 는 문서 순서를 지키지 않는다(셀렉터별로 묶어 반환).
  행 머리글이 끝으로 밀려 **메뉴가 하루씩 조용히 밀린다.** 반드시 row.iter() 를 쓴다.
  이 파일에서 css("td,th") 를 쓰면 T15 가 실패한다.
"""

from __future__ import annotations

import datetime as dt
import html as html_mod
import pathlib
import re
from dataclasses import dataclass

import yaml
from selectolax.parser import HTMLParser

from crawler import validate
from crawler.validate import AnchorMismatch, ColumnAnchor, ParseError
from store.repo import ParsedItem, ParsedMeal

SELECTORS_PATH = pathlib.Path(__file__).resolve().parents[2] / "config" / "selectors.yaml"

FACILITY_ID = "jbnu:facility/생활관-식당"
SOURCE_KEY = "likehome_week_menu"
EXTRACTION_METHOD = "html_selector"
CONFIDENCE = 0.95

MEAL_LABEL_TO_TYPE = {"아침": "breakfast", "점심": "lunch", "저녁": "dinner",
                      "조식": "breakfast", "중식": "lunch", "석식": "dinner"}

_HEADER_RE = re.compile(r"(?P<wd>[월화수목금토일]요일)\s*\((?P<day>\d{1,2})\)")
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def load_selectors(path: pathlib.Path | None = None) -> dict:
    doc = yaml.safe_load((path or SELECTORS_PATH).read_text(encoding="utf-8"))
    return doc[SOURCE_KEY]


@dataclass
class ParseResult:
    meals: list[ParsedMeal]
    quarantined: list[tuple[ParsedMeal, str]]
    week_start: str
    anchors: list[ColumnAnchor]

    @property
    def counts(self) -> tuple[int, int]:
        return len(self.meals), len(self.quarantined)


# ── 문서 순서를 지키는 셀 순회 ────────────────────────────────────

def row_cells(row):
    """★ row.css("td,th") 금지. 자식 노드를 순회해 문서 순서를 지킨다."""
    return [n for n in row.iter(include_text=False) if n.tag in ("td", "th")]


def _text(node) -> str:
    return re.sub(r"\s+", " ", (node.text() or "")).strip()


def _split_items(link_html: str) -> list[str]:
    parts = _BR_RE.split(link_html)
    out = []
    for p in parts:
        t = html_mod.unescape(_TAG_RE.sub("", p))
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            out.append(t)
    return out


def _split_title(title: str, sep: str) -> list[str]:
    if not title:
        return []
    return [t.strip() for t in html_mod.unescape(title).split(sep) if t.strip()]


# ── 헤더 → 날짜 앵커 ─────────────────────────────────────────────

def _parse_header(header_cells, week_start: dt.date) -> list[ColumnAnchor]:
    anchors: list[ColumnAnchor] = []
    col = 0
    for cell in header_cells:
        m = _HEADER_RE.search(_text(cell))
        if not m:
            continue  # 좌상단 빈 칸
        anchors.append(ColumnAnchor(
            index=col,
            weekday_label=m.group("wd"),
            day_of_month=int(m.group("day")),
            computed_date=(week_start + dt.timedelta(days=col)).isoformat(),
        ))
        col += 1
    return anchors


_RANGE_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\s*~\s*(\d{4})\.(\d{2})\.(\d{2})")
_LEFT_RE = re.compile(r'<a[^>]+href="[^"]*date=(\d{4}-\d{2}-\d{2})[^"]*"[^>]*>\s*<img[^>]+left_btn')
_RIGHT_RE = re.compile(r'<a[^>]+href="[^"]*date=(\d{4}-\d{2}-\d{2})[^"]*"[^>]*>\s*<img[^>]+right_btn')


def _find_week_start(html: str, explicit: str | None) -> dt.date:
    """주 시작일(일요일).

    ★ 셀의 href 를 먼저 뒤지면 안 된다. 문서 앞쪽의 **이전주 네비게이션 링크**가
      먼저 걸려 일주일 전 날짜를 집는다. 그러면 표 전체가 7일 밀린다.
      (실제로 처음에 이렇게 짰다가 앵커 게이트에 걸렸다.)

    독립 신호 3개를 모아 대조한다. 하나라도 어긋나면 파싱을 중단한다.
      · 화면에 표시된 기간  '2026.05.31 ~ 2026.06.06'
      · 이전주 버튼 링크 + 7일
      · 다음주 버튼 링크 - 7일
    """
    if explicit:
        return dt.date.fromisoformat(explicit)

    cands: dict[str, dt.date] = {}
    m = _RANGE_RE.search(html)
    if m:
        cands["표시기간"] = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _LEFT_RE.search(html)
    if m:
        cands["이전주+7"] = dt.date.fromisoformat(m.group(1)) + dt.timedelta(days=7)
    m = _RIGHT_RE.search(html)
    if m:
        cands["다음주-7"] = dt.date.fromisoformat(m.group(1)) - dt.timedelta(days=7)

    if not cands:
        raise ParseError("주 시작일 신호를 하나도 찾지 못했다")
    if len(set(cands.values())) > 1:
        raise ParseError(
            "주 시작일 신호가 서로 어긋난다: "
            + ", ".join(f"{k}={v}" for k, v in sorted(cands.items()))
        )
    start = next(iter(cands.values()))
    if start.weekday() != 6:  # 일요일
        raise ParseError(f"주 시작일이 일요일이 아니다: {start} ({start.weekday()})")
    return start


# ── 본체 ────────────────────────────────────────────────────────

def parse(html: str, *, week_start: str | None = None,
          selectors: dict | None = None) -> ParseResult:
    """주간표 HTML → ParsedMeal 목록.

    빈 칸 처리 (02 §3 개정판)
      메뉴 있음                → operating
      칸이 비어 있음           → unknown      ★ closed_vacation 이라고 단정하지 않는다
      원천이 '식사없음' 명시    → closed_temporary
      '[지방선거]' 등 대괄호    → closed_temporary + note 에 사유 보존
      표 자체를 못 찾음        → ParseError (레코드 생성 안 함)
    """
    sel = selectors or load_selectors()
    tree = HTMLParser(html)

    table = tree.css_first(sel["table"])
    if table is None:
        raise ParseError(f"식단표를 찾지 못했다 (selector={sel['table']!r})")

    header_cells = table.css(sel["header_cells"])
    if not header_cells:
        raise ParseError(f"헤더 행을 찾지 못했다 (selector={sel['header_cells']!r})")

    ws = _find_week_start(html, week_start)
    anchors = _parse_header(header_cells, ws)

    # ── 게이트 4. 여기서 걸리면 레코드를 하나도 만들지 않는다 ──
    validate.check_alignment(anchors)

    date_by_col = {a.index: a.computed_date for a in anchors}
    closed_markers = set(sel["closed_markers"])
    bracket_re = re.compile(sel["closed_bracket_pattern"])

    meals: list[ParsedMeal] = []
    quarantined: list[tuple[ParsedMeal, str]] = []

    for row in table.css(sel["body_rows"]):
        cells = row_cells(row)
        if not cells:
            continue
        label = _text(cells[0])
        meal_type = MEAL_LABEL_TO_TYPE.get(label)
        if meal_type is None:
            continue  # 끼니 행이 아니다

        data_cells = cells[1:]
        if len(data_cells) != len(anchors):
            raise ParseError(
                f"'{label}' 행의 칸 수({len(data_cells)})가 헤더 열 수({len(anchors)})와 다르다"
            )

        for col, cell in enumerate(data_cells):
            date = date_by_col.get(col)
            if date is None:
                continue
            meal, reason = _parse_cell(cell, sel, closed_markers, bracket_re,
                                       date=date, meal_type=meal_type)
            if reason:
                quarantined.append((meal, reason))
            else:
                reason2 = validate.validate_meal(meal)
                if reason2:
                    quarantined.append((meal, reason2))
                else:
                    meals.append(meal)

    return ParseResult(meals=meals, quarantined=quarantined,
                       week_start=ws.isoformat(), anchors=anchors)


def _parse_cell(cell, sel: dict, closed_markers: set[str], bracket_re: re.Pattern,
                *, date: str, meal_type: str) -> tuple[ParsedMeal, str | None]:
    link = cell.css_first(sel["cell_link"])

    def build(status: str, items: list[ParsedItem], note: str | None = None):
        return ParsedMeal(facility_id=FACILITY_ID, date=date, meal_type=meal_type,
                          service_status=status, zone="", corner="",
                          items=items, note=note)

    # 링크가 없거나 내용이 비었다 = 관측의 부재.
    # ★ '방학이라 쉰다'고 단정하지 않는다. 그 판단은 답변 시점에 운영시간과 조인해서 한다.
    if link is None or not _text(link):
        return build("unknown", []), None

    raw_items = _split_items(link.html or "")
    title_items = _split_title(link.attributes.get(sel["title_attr"]) or "",
                               sel["title_separator"])

    # 원천이 명시한 미운영
    joined = "".join(raw_items)
    if joined in closed_markers or (len(raw_items) == 1 and raw_items[0] in closed_markers):
        return build("closed_temporary", [], note=raw_items[0] if raw_items else None), None
    if len(raw_items) == 1 and bracket_re.match(raw_items[0]):
        # [지방선거] [대체공휴일] — 사유를 note 에 보존한다
        return build("closed_temporary", [], note=raw_items[0]), None

    # 같은 셀의 두 인코딩 대조 (<br> 목록 vs title 속성)
    mismatch = validate.check_cell_crosscheck(raw_items, title_items)
    items = [ParsedItem(name=n, display_order=i) for i, n in enumerate(raw_items)]
    meal = build("operating", items)
    return meal, mismatch
