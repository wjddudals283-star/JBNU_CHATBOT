"""학사일정 파서 — schedule/dataAjax.do.

    POST https://www.jbnu.ac.kr/web/academic/schedule/dataAjax.do
      data:    type=yearly, acYear=2026, acSemester=1|2
      headers: X-CSRF-Token 필수 (없으면 403). 식단 XHR 과 같은 절차
      응답:    HTML 조각. **테이블이 아니라 <dl><dt>날짜</dt><dd>내용</dd>**

    <dt>2026-09-01</dt>              <dd>제2학기 개강, 일반대학원 종합시험</dd>
    <dt>2026-09-01 ~ 2026-09-07</dt> <dd>제2학기 수강신청 변경(추가) 기간</dd>

★ acYear/acSemester 로 과거·미래 조회가 된다.
  학교 식단 XHR 은 이번 주 고정이라 백필이 안 됐는데, 이건 된다.
  사고가 나도 복구가 되고, 여러 해를 쌓으면 관측으로 판단할 거리가 생긴다.

★ 콤마를 쪼개지 않는다.
  '제2학기 개강, 일반대학원 종합시험' 이 두 건인지 한 건의 긴 이름인지 **모른다.**
  콤마를 항목 구분자로 보는 것 자체가 추론이다(§5). 원천이 그렇게 말한 적 없다.
  raw_text 로 원문을 남겨두면, 여러 해를 백필해 같은 항목이 단독으로 나타나는지
  **관측**한 뒤에 나중에 판단할 수 있다. 재크롤도 필요 없다.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

from crawler.validate import AnchorMismatch, ParseError

SOURCE_KEY = "jbnu_academic_schedule"
EXTRACTION_METHOD = "html_selector"
CONFIDENCE = 0.95

_ISO = r"\d{4}-\d{2}-\d{2}"
_RANGE_RE = re.compile(rf"^({_ISO})\s*~\s*({_ISO})$")
_SINGLE_RE = re.compile(rf"^({_ISO})$")

# 학기별로 일정이 몰리는 달. 응답이 요청한 학기와 맞는지 보는 구조 검증에 쓴다.
SEMESTER_MONTHS = {1: {1, 2, 3, 4, 5, 6, 7, 8}, 2: {7, 8, 9, 10, 11, 12, 1, 2}}


@dataclass
class ParsedCalendarEntry:
    ac_year: int
    ac_semester: int
    title: str
    start_date: str
    end_date: str | None
    raw_text: str

    @property
    def is_range(self) -> bool:
        return self.end_date is not None


@dataclass
class ParseResult:
    calendar_entries: list[ParsedCalendarEntry] = field(default_factory=list)
    quarantined: list[tuple[object, str]] = field(default_factory=list)
    # ingest 가 기대하는 공통 필드 (식단 파서와 모양을 맞춘다)
    meals: list = field(default_factory=list)
    anchors: list = field(default_factory=list)
    week_start: str = ""
    ac_year: int | None = None
    ac_semester: int | None = None
    # 원천이 '일정이 없습니다'를 명시한 경우. 미게시이지 파싱 실패가 아니다.
    not_published: bool = False

    @property
    def counts(self) -> tuple[int, int]:
        return len(self.calendar_entries), len(self.quarantined)


def _txt(node) -> str:
    return re.sub(r"\s+", " ", (node.text() or "")).strip()


def _pairs(dl) -> list[tuple[str, str]]:
    """<dt>/<dd> 를 문서 순서로 짝짓는다.

    ★ css("dt"), css("dd") 를 따로 뽑아 zip 하면 안 된다 — 개수가 어긋났을 때
      조용히 밀린 채로 짝지어진다. likehome 에서 하루 밀렸던 것과 같은 함정이다.
    """
    out: list[tuple[str, str]] = []
    pending: str | None = None
    saw_empty = False
    # ★ iter() 는 직계 자식만 준다. 실제 마크업은 <dl><div><dt>…</dt><dd>…</dd></div>
    #   구조라 한 겹 안에 있다. 깊이 순회로 문서 순서를 따라간다.
    for node in dl.traverse(include_text=False):
        if node.tag == "dt":
            # ★ 일정이 없는 달은 <dt class="empty">일정이 없습니다.</dt> 만 온다.
            #   이건 파싱 실패가 아니라 **'그 달엔 일정이 없다'는 관측**이다.
            #   텍스트가 아니라 class 로 판별한다 — 문구는 바뀔 수 있다.
            if "empty" in (node.attributes.get("class") or "").split():
                saw_empty = True
                continue
            if pending is not None:
                raise ParseError(f"<dt> 다음에 <dd> 가 없다: {pending!r}")
            pending = _txt(node)
        elif node.tag == "dd":
            if pending is None:
                raise ParseError(f"<dd> 앞에 <dt> 가 없다: {_txt(node)!r}")
            out.append((pending, _txt(node)))
            pending = None
    if pending is not None:
        raise ParseError(f"짝 없는 <dt>: {pending!r}")
    return out, saw_empty


def _parse_dates(text: str) -> tuple[str, str | None]:
    t = text.strip()
    m = _RANGE_RE.match(t)
    if m:
        start, end = m.group(1), m.group(2)
        if end < start:
            raise ParseError(f"기간의 끝이 시작보다 이르다: {t}")
        return start, end
    m = _SINGLE_RE.match(t)
    if m:
        return m.group(1), None
    raise ParseError(f"날짜 형식을 모르겠다: {t!r}")


def parse(html: str, *, ac_year: int | None = None,
          ac_semester: int | None = None) -> ParseResult:
    if "접근이 거부" in html or "접근권한이 없" in html:
        raise ParseError("403 — CSRF 토큰 없이 호출했다 "
                         "(schedule.do GET → _csrf → 같은 세션 POST)")

    tree = HTMLParser(html)
    dls = tree.css("dl.academic") or tree.css("dl")
    if not dls:
        raise ParseError("학사일정 목록(<dl>)을 찾지 못했다")

    result = ParseResult(ac_year=ac_year, ac_semester=ac_semester)
    seen: set[tuple[str, str]] = set()

    any_empty_marker = False
    for dl in dls:
        pairs, saw_empty = _pairs(dl)
        any_empty_marker = any_empty_marker or saw_empty
        for date_text, title in pairs:
            if not title:
                continue
            try:
                start, end = _parse_dates(date_text)
            except ParseError as e:
                result.quarantined.append((date_text, str(e)))
                continue

            year = ac_year or int(start[:4])
            sem = ac_semester or (1 if 3 <= int(start[5:7]) <= 8 else 2)

            key = (start, title)
            if key in seen:
                continue          # 같은 응답 안의 중복은 조용히 접는다
            seen.add(key)

            result.calendar_entries.append(ParsedCalendarEntry(
                ac_year=year, ac_semester=sem, title=title,
                start_date=start, end_date=end,
                # ★ 원문 그대로. 콤마 분할 판단을 나중으로 미룰 수 있게 남긴다.
                raw_text=f"{date_text}\t{title}",
            ))

    if not result.calendar_entries:
        # ★ 0건에는 두 가지 뜻이 있다.
        #   · 모든 달이 <dt class="empty"> → **미게시.** 미래 학기가 그렇다. 정상이다
        #   · 그런 표시도 없이 0건      → 셀렉터가 깨진 것. 파싱 실패
        #   둘을 섞으면 미공개 학기를 크롤 고장으로 오인하거나, 그 반대가 된다.
        if any_empty_marker:
            result.not_published = True
            return result
        raise ParseError("일정을 한 건도 파싱하지 못했다 (셀렉터 깨짐 의심)")

    _check_semester_alignment(result)
    return result


def _check_semester_alignment(result: ParseResult) -> None:
    """요청한 학기와 응답의 달이 맞는지 — 구조 검증(게이트 4).

    ★ 값도 정상이고 개수도 맞는데 **엉뚱한 학기 응답**을 받는 경우가 있다.
      파라미터가 무시되면 항상 같은 학기가 오고, 그러면 조용히 틀린 답을 한다.
    """
    if result.ac_semester is None:
        return
    allowed = SEMESTER_MONTHS[result.ac_semester]
    months = [int(e.start_date[5:7]) for e in result.calendar_entries]
    off = [m for m in months if m not in allowed]
    # 학기 경계라 일부는 넘어갈 수 있다. 절반 이상이 어긋나면 응답 자체가 다른 것이다.
    if len(off) > len(months) / 2:
        raise AnchorMismatch(
            f"{result.ac_year}년 {result.ac_semester}학기를 요청했는데 "
            f"일정 {len(months)}건 중 {len(off)}건의 달이 어긋난다 "
            f"(월 분포 {sorted(set(months))}). 파라미터가 무시됐을 가능성"
        )


def upcoming(entries: list[ParsedCalendarEntry], *, today: str,
             days: int = 14) -> list[ParsedCalendarEntry]:
    """오늘 기준 다가오는 일정 — 12번(deadline.upcoming) 의 토대."""
    d0 = dt.date.fromisoformat(today)
    d1 = d0 + dt.timedelta(days=days)
    out = []
    for e in entries:
        s = dt.date.fromisoformat(e.start_date)
        end = dt.date.fromisoformat(e.end_date) if e.end_date else s
        # 진행 중이거나 곧 시작하는 것
        if end >= d0 and s <= d1:
            out.append(e)
    return sorted(out, key=lambda e: (e.start_date, e.title))
