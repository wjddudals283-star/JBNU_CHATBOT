"""학사일정 파서 — 작업 11.

픽스처는 실제 dataAjax.do 응답이다.
  schedule_2026_2.html   2026-2학기 · 22건
  schedule_2027_1.html   2027-1학기 · 전 달 '일정이 없습니다' (미게시)
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from conftest import SNAPSHOT_ID, SOURCE_URL
from crawler.parsers import jbnu_academic_schedule as sched
from crawler.validate import AnchorMismatch, ParseError
from store import repo

FIX = pathlib.Path(__file__).parent / "fixtures"
Y2026 = (FIX / "schedule_2026_2.html").read_text(encoding="utf-8", errors="replace")
Y2027 = (FIX / "schedule_2027_1.html").read_text(encoding="utf-8", errors="replace")


def _parse26():
    return sched.parse(Y2026, ac_year=2026, ac_semester=2)


# ═══════════════════════════════════════════════════════════════
# 파싱
# ═══════════════════════════════════════════════════════════════

def test_실데이터_22건_파싱():
    r = _parse26()
    assert len(r.calendar_entries) == 22 and not r.quarantined
    assert not r.not_published


def test_단일날짜와_기간을_구분한다():
    by_title = {e.title: e for e in _parse26().calendar_entries}
    single = by_title["개교기념일(휴업일)"]
    assert single.start_date == "2026-10-15" and single.end_date is None

    ranged = by_title["제2학기 수강신청 변경(추가) 기간"]
    assert ranged.start_date == "2026-09-01" and ranged.end_date == "2026-09-07"
    assert ranged.is_range


def test_콤마를_쪼개지_않는다():
    """★ 콤마를 항목 구분자로 보는 것 자체가 추론이다.

    '제2학기 개강, 일반대학원 종합시험' 이 두 건인지 한 건의 긴 이름인지 모른다.
    원천이 그렇게 말한 적이 없다. 여러 해를 백필해 관측으로 판단할 때까지 미룬다.
    """
    titles = [e.title for e in _parse26().calendar_entries]
    assert "제2학기 개강, 일반대학원 종합시험" in titles
    assert "제2학기 개강" not in titles
    assert "일반대학원 종합시험" not in titles


def test_raw_text가_원문을_보존한다():
    """분할 규칙을 나중에 바꿔도 재크롤이 필요 없어야 한다."""
    e = next(x for x in _parse26().calendar_entries if "," in x.title)
    assert e.start_date in e.raw_text and e.title in e.raw_text


def test_미게시는_parse_error가_아니다():
    """★ 0건의 두 가지 뜻.

    <dt class="empty"> 가 있으면 미게시(정상). 없이 0건이면 셀렉터 깨짐.
    섞으면 미공개 학기를 크롤 고장으로 오인한다.
    """
    r = sched.parse(Y2027, ac_year=2027, ac_semester=1)
    assert r.calendar_entries == []
    assert r.not_published is True


def test_표를_못_찾으면_parse_error():
    with pytest.raises(ParseError):
        sched.parse("<html><body><p>개편했습니다</p></body></html>")


def test_empty표시_없이_0건이면_parse_error():
    """셀렉터가 깨진 경우. 미게시와 구분된다."""
    with pytest.raises(ParseError):
        sched.parse('<dl class="academic"></dl>')


def test_짝_없는_dt는_parse_error():
    """dt/dd 개수가 어긋나면 조용히 밀린 채로 짝지어지면 안 된다."""
    broken = '<dl class="academic"><div><dt>2026-09-01</dt></div>' \
             '<div><dt>2026-09-02</dt><dd>개강</dd></div></dl>'
    with pytest.raises(ParseError) as e:
        sched.parse(broken)
    assert "dd" in str(e.value)


def test_CSRF_403은_명확한_메시지():
    with pytest.raises(ParseError) as e:
        sched.parse("<html>죄송합니다. 접근이 거부되었습니다.</html>")
    assert "CSRF" in str(e.value)


def test_이상한_날짜는_격리():
    weird = '<dl class="academic"><div><dt>미정</dt><dd>어떤 일정</dd></div>' \
            '<div><dt>2026-09-01</dt><dd>개강</dd></div></dl>'
    r = sched.parse(weird)
    assert len(r.calendar_entries) == 1
    assert r.quarantined and "날짜 형식" in r.quarantined[0][1]


# ═══════════════════════════════════════════════════════════════
# 구조 검증 — 파라미터가 무시되면 잡는다
# ═══════════════════════════════════════════════════════════════

def test_요청한_학기와_응답이_어긋나면_잡는다():
    """★ 값도 정상이고 개수도 맞는데 엉뚱한 학기가 오는 경우.

    파라미터가 무시되면 항상 같은 학기가 오고, 그러면 조용히 틀린 답을 한다.
    """
    with pytest.raises(AnchorMismatch) as e:
        sched.parse(Y2026, ac_year=2026, ac_semester=1)   # 실제로는 2학기 응답
    assert "어긋난다" in str(e.value)


def test_학기를_안_주면_구조검증을_건너뛴다():
    r = sched.parse(Y2026)
    assert len(r.calendar_entries) == 22


# ═══════════════════════════════════════════════════════════════
# upcoming — 12번(deadline.upcoming) 토대
# ═══════════════════════════════════════════════════════════════

def test_다가오는_일정():
    e = _parse26().calendar_entries
    up = sched.upcoming(e, today="2026-09-01", days=14)
    assert [x.title for x in up] == [
        "제2학기 개강, 일반대학원 종합시험",
        "제2학기 수강신청 변경(추가) 기간",
    ]


def test_진행_중인_기간도_포함된다():
    """9/3 은 수강신청 변경 기간(9/1~9/7) 한가운데다. 이미 시작했어도 알려야 한다."""
    e = _parse26().calendar_entries
    up = sched.upcoming(e, today="2026-09-03", days=3)
    assert any(x.title.startswith("제2학기 수강신청 변경") for x in up)


def test_지난_일정은_빠진다():
    e = _parse26().calendar_entries
    up = sched.upcoming(e, today="2026-11-01", days=7)
    assert all(x.start_date >= "2026-10-26" for x in up)


# ═══════════════════════════════════════════════════════════════
# 저장 — 시계열 규칙
# ═══════════════════════════════════════════════════════════════

def _meta(valid_from: str) -> repo.SourceMeta:
    return repo.SourceMeta(
        source_id=SNAPSHOT_ID, source_url=SOURCE_URL,
        observed_at=f"{valid_from}T07:00:00+09:00", confidence=0.95,
        extraction_method="html_selector", tier="T1", valid_from=valid_from)


def test_저장과_조회(conn):
    for e in _parse26().calendar_entries:
        repo.upsert_calendar(conn, e, _meta("2026-08-11"))
    conn.commit()

    rows = repo.query_calendar(conn, since="2026-09-01", until="2026-09-30")
    titles = [r["title"] for r in rows]
    assert "제2학기 개강, 일반대학원 종합시험" in titles
    assert all(r["start_date"] <= "2026-09-30" for r in rows)


def test_개정본은_별개_레코드고_최신만_조회된다(conn):
    """★ 학사일정은 개정된다. 덮어쓰면 '언제 바뀌었나'를 못 답한다."""
    e = next(x for x in _parse26().calendar_entries
             if x.title == "개교기념일(휴업일)")
    repo.upsert_calendar(conn, e, _meta("2026-08-11"))

    revised = sched.ParsedCalendarEntry(
        ac_year=e.ac_year, ac_semester=e.ac_semester, title=e.title,
        start_date=e.start_date, end_date="2026-10-16", raw_text="개정본")
    repo.upsert_calendar(conn, revised, _meta("2026-09-20"))
    conn.commit()

    n = conn.execute("SELECT COUNT(*) c FROM academic_calendar").fetchone()["c"]
    assert n == 2, "이력이 남아야 한다"

    rows = repo.query_calendar(conn, since="2026-10-15", until="2026-10-15")
    hit = next(r for r in rows if r["title"] == "개교기념일(휴업일)")
    assert hit["end_date"] == "2026-10-16", "조회는 최신 관측만 쓴다"


def test_기간_역전은_DB가_거부한다(conn):
    with pytest.raises(Exception):
        conn.execute(
            """INSERT INTO academic_calendar
                 (id, ac_year, ac_semester, title, start_date, end_date,
                  source_id, source_url, observed_at, valid_from,
                  confidence, extraction_method, tier)
               VALUES ('x',2026,2,'거꾸로','2026-09-10','2026-09-01',
                       ?,?,'2026-08-11T07:00:00+09:00','2026-08-11',
                       0.95,'html_selector','T1')""",
            (SNAPSHOT_ID, SOURCE_URL))
