"""likehome 파서 수용 기준 — T1 / T2 / T2b / T3 / T5 / T15.

픽스처는 실제로 받아온 원문이다 (docs/probe/ 에서 복사).
  likehome_20260531_week.html      학기중 정상 주 (2026-05-31 일 ~ 06-06 토)
                                   ★ 2026-06-03(수) 에 [지방선거] — 외부 검증 가능한 앵커
  likehome_20260809_vacation.html  방학 주 (2026-08-09 일 ~ 08-15 토). 표는 정상, 칸만 빔
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re

import pytest
from selectolax.parser import HTMLParser

from conftest import SNAPSHOT_ID, SOURCE_URL
from crawler import fetch as fetch_mod
from crawler import ingest as ingest_mod
from crawler.parsers import likehome_week_menu as likehome
from crawler.validate import AnchorMismatch, ParseError
from skill import branch
from store import repo

FIX = pathlib.Path(__file__).parent / "fixtures"
WEEK = (FIX / "likehome_20260531_week.html").read_text(encoding="utf-8", errors="replace")
VACATION = (FIX / "likehome_20260809_vacation.html").read_text(encoding="utf-8", errors="replace")

LIKEHOME_ID = likehome.FACILITY_ID
NOW = dt.datetime.fromisoformat("2026-06-01T12:00:00+09:00")


@pytest.fixture()
def dorm_conn(conn):
    conn.execute(
        """INSERT INTO facility (id, name, facility_type, source_url, source_type)
           VALUES (?,?,?,?,?)""",
        (LIKEHOME_ID, "생활관 식당", "식당",
         "https://likehome.jbnu.ac.kr/home/main/inner.php?sMenu=B7100", "dorm"),
    )
    conn.commit()
    return conn


# ═══════════════════════════════════════════════════════════════
# T1 — 정상 주간표
# ═══════════════════════════════════════════════════════════════

def test_T1_정상_주간표_파싱():
    r = likehome.parse(WEEK)
    assert r.week_start == "2026-05-31"
    assert len(r.anchors) == 7, "일~토 7열"

    # 3끼니 × 7일 = 21건이 빠짐없이 나와야 한다
    assert len(r.meals) + len(r.quarantined) == 21

    dates = {m.date for m in r.meals}
    assert dates <= {f"2026-06-{d:02d}" for d in range(1, 7)} | {"2026-05-31"}
    assert {m.meal_type for m in r.meals} == {"breakfast", "lunch", "dinner"}

    # 월요일 아침은 실제 메뉴가 있다
    mon = [m for m in r.meals
           if m.date == "2026-06-01" and m.meal_type == "breakfast"][0]
    assert mon.service_status == "operating"
    assert [i.name for i in mon.items][:3] == ["흰밥", "소고기샤브국", "오징어호박볶음"]
    assert mon.items[0].display_order == 0


def test_T1b_미운영_표현은_품목으로_저장되지_않는다():
    """'식사없음'이 menu_item.name 이 되면 챗봇이 '오늘 메뉴: 식사없음'이라 답한다."""
    r = likehome.parse(WEEK)
    all_names = {i.name for m in r.meals for i in m.items}
    assert "식사없음" not in all_names
    assert "[지방선거]" not in all_names

    closed = [m for m in r.meals if m.service_status == "closed_temporary"]
    assert closed, "'식사없음'/'[지방선거]' 칸이 closed_temporary 로 잡혀야 한다"
    assert all(m.items == [] for m in closed)
    # 사유는 note 에 보존한다
    assert any(m.note == "[지방선거]" for m in closed)


# ═══════════════════════════════════════════════════════════════
# T15 — 행/열 정렬 회귀 ★ 게이트가 못 잡는 오류
# ═══════════════════════════════════════════════════════════════

def test_T15_지방선거일이_정확한_요일_칸에_들어간다():
    """외부에서 검증 가능한 앵커. 2026-06-03 은 제9회 전국동시지방선거일(수)."""
    r = likehome.parse(WEEK)
    election = [m for m in r.meals if m.note == "[지방선거]"]
    assert election, "[지방선거] 칸을 찾지 못했다"
    for m in election:
        assert m.date == "2026-06-03"
        assert dt.date.fromisoformat(m.date).weekday() == 2, "수요일이어야 한다"


def test_T15b_row_cells는_문서순서를_지킨다():
    """css('td,th') 는 셀렉터별로 묶어 반환해 행 머리글을 끝으로 민다.

    이 픽스처에서 두 방식이 실제로 다르다는 것 자체를 고정해 둔다.
    다르지 않게 되면(라이브러리 변경 등) 이 테스트가 알려준다.
    """
    table = HTMLParser(WEEK).css_first("table.calendar_box")
    body = table.css("tbody tr")[0]

    doc_order = [(n.tag, re.sub(r"\s+", " ", (n.text() or "")).strip()[:6])
                 for n in likehome.row_cells(body)]
    sel_order = [(n.tag, re.sub(r"\s+", " ", (n.text() or "")).strip()[:6])
                 for n in body.css("td,th")]

    assert doc_order[0] == ("th", "아침"), "문서 순서에선 끼니 라벨이 맨 앞"
    assert sel_order[0][0] == "td", "css('td,th') 는 td 를 먼저 내놓는다"
    assert doc_order != sel_order, "두 방식이 같아졌다면 이 함정의 전제가 바뀐 것"


def test_T15c_주_시작일이_어긋나면_앵커_게이트가_막는다():
    """하루 밀린 주 시작일을 주면 파싱은 '성공'하지만 정렬이 틀린다.

    스키마도 맞고 값도 정상 범위이고 개수도 맞는다 — 1~3번 게이트를 전부 통과한다.
    4번 앵커 게이트만 이걸 잡는다.
    """
    with pytest.raises(AnchorMismatch) as e:
        likehome.parse(WEEK, week_start="2026-06-01")
    assert "불일치" in str(e.value)


def test_T15d_앵커_실패시_레코드가_하나도_안_생긴다(dorm_conn, tmp_path):
    res = _fetch_result(WEEK, "likehome_week_menu")
    report = ingest_mod.ingest(
        dorm_conn, res,
        parser=lambda h: likehome.parse(h, week_start="2026-06-01"),
        snapshot_dir=tmp_path, extraction_method="html_selector",
    )
    assert report.outcome == "parse_error"
    n = dorm_conn.execute("SELECT COUNT(*) c FROM meal_service").fetchone()["c"]
    assert n == 0


# ═══════════════════════════════════════════════════════════════
# T2 — 빈 칸(방학) 픽스처
# ═══════════════════════════════════════════════════════════════

def test_T2_방학_빈칸은_unknown이지_parse_error가_아니다():
    r = likehome.parse(VACATION)
    assert r.week_start == "2026-08-09"
    assert len(r.meals) == 21, "표 구조는 정상 — 3끼니 × 7일"
    assert {m.service_status for m in r.meals} == {"unknown"}
    assert all(m.items == [] for m in r.meals)


def test_T2b_빈칸_해석은_저장이_아니라_질의계층에서_갈린다(dorm_conn, tmp_path):
    """같은 unknown 이 serves_meal 에 따라 B / C-1 / C-2 로 갈린다.

    저장된 사실은 하나뿐이다 — '관측하지 못했다'.
    '방학이라 쉰다'는 판단을 파싱 시점에 굳히지 않았기 때문에 가능한 분기다.
    """
    res = _fetch_result(VACATION, "likehome_week_menu",
                        fetched_at="2026-08-10T06:00:00+09:00")
    ingest_mod.ingest(dorm_conn, res, parser=likehome.parse, snapshot_dir=tmp_path)

    now = dt.datetime.fromisoformat("2026-08-10T12:00:00+09:00")
    kwargs = dict(facility_id=LIKEHOME_ID, date="2026-08-10",
                  meal_type="breakfast", now=now)

    # C-2 — 운영시간 데이터가 없다. 아무것도 모른다.
    # reason 까지 고정한다. 신선도 초과로도 C-2 가 나오므로 분기만 보면
    # 엉뚱한 이유로 통과할 수 있다.
    a = branch.resolve_meal(dorm_conn, **kwargs)
    assert a.branch is branch.Branch.C2 and a.serves is None
    assert a.reason == "unknown" and not a.stale

    # 여전히 C-2 — 점심 시간표만 넣었고 coverage 는 partial 이다.
    # 아침 행이 없는 게 '안 한다'인지 '아직 안 긁었다'인지 구분되지 않는다 (T20).
    _hours(dorm_conn, term="방학", weekday=1, meal_type="lunch")
    dorm_conn.commit()
    still = branch.resolve_meal(dorm_conn, **kwargs)
    assert still.branch is branch.Branch.C2 and still.serves is None

    # B — 시간표를 통째로 수집했다고 선언해야 비로소 부정을 말할 수 있다
    repo.set_hours_coverage(dorm_conn, LIKEHOME_ID, "complete")
    dorm_conn.commit()
    b = branch.resolve_meal(dorm_conn, **kwargs)
    assert b.branch is branch.Branch.B and b.serves is False
    assert b.reason == "not_offered"

    # C-1 — 아침을 운영하는 건 아는데 메뉴만 아직 없다
    _hours(dorm_conn, term="방학", weekday=1, meal_type="breakfast")
    dorm_conn.commit()
    c = branch.resolve_meal(dorm_conn, **kwargs, term="방학")
    assert c.branch is branch.Branch.C1 and c.serves is True
    assert c.reason == "not_published"


def test_T2c_메뉴가_있으면_A분기(dorm_conn, tmp_path):
    res = _fetch_result(WEEK, "likehome_week_menu")
    ingest_mod.ingest(dorm_conn, res, parser=likehome.parse, snapshot_dir=tmp_path)
    a = branch.resolve_meal(dorm_conn, facility_id=LIKEHOME_ID, date="2026-06-01",
                            meal_type="breakfast", now=NOW)
    assert a.branch is branch.Branch.A
    assert a.operating_rows and a.operating_rows[0]["items"]


def test_T2d_원천이_명시한_미운영은_B분기(dorm_conn, tmp_path):
    """[지방선거] 는 관측된 미운영이다. 운영시간을 몰라도 B 로 답할 수 있다."""
    res = _fetch_result(WEEK, "likehome_week_menu")
    ingest_mod.ingest(dorm_conn, res, parser=likehome.parse, snapshot_dir=tmp_path)
    a = branch.resolve_meal(dorm_conn, facility_id=LIKEHOME_ID, date="2026-06-03",
                            meal_type="breakfast", now=NOW)
    assert a.branch is branch.Branch.B
    assert a.reason == "closed_observed"
    assert a.serves is None, "운영시간을 몰라도 관측된 미운영이면 B 다"


def test_신선도_초과는_값이_있어도_C2(dorm_conn, tmp_path):
    res = _fetch_result(WEEK, "likehome_week_menu")
    ingest_mod.ingest(dorm_conn, res, parser=likehome.parse, snapshot_dir=tmp_path)
    late = dt.datetime.fromisoformat("2026-06-05T12:00:00+09:00")  # 4일 후
    a = branch.resolve_meal(dorm_conn, facility_id=LIKEHOME_ID, date="2026-06-01",
                            meal_type="breakfast", now=late)
    assert a.branch is branch.Branch.C2 and a.stale


# ═══════════════════════════════════════════════════════════════
# T3 — 셀렉터가 깨졌을 때
# ═══════════════════════════════════════════════════════════════

def test_T3_셀렉터_깨짐은_parse_error이고_DB는_무변경(dorm_conn, tmp_path):
    # 먼저 정상 크롤로 데이터를 넣는다
    ok = _fetch_result(WEEK, "likehome_week_menu")
    r1 = ingest_mod.ingest(dorm_conn, ok, parser=likehome.parse, snapshot_dir=tmp_path)
    assert r1.outcome == "success"
    before = _snapshot_of_db(dorm_conn)
    assert before["meal_service"] == 21

    # 사이트 개편으로 테이블 클래스가 바뀐 상황
    broken = WEEK.replace('class="calendar_box"', 'class="menu_table_v2"')
    res = _fetch_result(broken, "likehome_week_menu")
    r2 = ingest_mod.ingest(dorm_conn, res, parser=likehome.parse, snapshot_dir=tmp_path)

    assert r2.outcome == "parse_error"
    assert "찾지 못했다" in (r2.error or "")
    assert _snapshot_of_db(dorm_conn) == before, "기존 데이터를 건드리면 안 된다"


def test_T3b_파싱_실패는_다음_회차를_막지_않는다(dorm_conn, tmp_path):
    """실패한 회차의 해시를 '본 것'으로 기록하면 같은 내용이 계속 스킵된다.

    셀렉터를 고쳐도 복구가 안 되는, 조용히 멈추는 버그다.
    """
    broken = WEEK.replace('class="calendar_box"', 'class="menu_table_v2"')
    res = _fetch_result(broken, "likehome_week_menu")
    r1 = ingest_mod.ingest(dorm_conn, res, parser=likehome.parse, snapshot_dir=tmp_path)
    assert r1.outcome == "parse_error"

    # 셀렉터를 고쳤다 = 같은 원문을 다시 파싱할 수 있어야 한다
    r2 = ingest_mod.ingest(
        dorm_conn, res, snapshot_dir=tmp_path,
        parser=lambda h: likehome.parse(
            h, selectors={**likehome.load_selectors(), "table": "table.menu_table_v2"}),
    )
    assert r2.parser_called, "unchanged 로 스킵되면 안 된다"
    assert r2.outcome == "success" and r2.parsed == 21


# ═══════════════════════════════════════════════════════════════
# T5 — 동일 해시 재크롤
# ═══════════════════════════════════════════════════════════════

def test_T5_동일_해시면_파서를_부르지_않는다(dorm_conn, tmp_path):
    res = _fetch_result(WEEK, "likehome_week_menu")
    r1 = ingest_mod.ingest(dorm_conn, res, parser=likehome.parse, snapshot_dir=tmp_path)
    assert r1.outcome == "success" and r1.parser_called

    calls = []

    def spy(h):
        calls.append(1)
        return likehome.parse(h)

    res2 = _fetch_result(WEEK, "likehome_week_menu",
                         fetched_at="2026-06-01T16:00:00+09:00")
    r2 = ingest_mod.ingest(dorm_conn, res2, parser=spy, snapshot_dir=tmp_path)
    assert r2.outcome == "unchanged"
    assert not r2.parser_called and calls == []


def test_T5b_캐시버스터가_있어도_unchanged로_판정된다(dorm_conn, tmp_path):
    """실전에서 발견한 것 — likehome 은 CSS/JS 에 ?ver=<유닉스타임> 을 붙인다.

    같은 페이지를 1초 간격으로 받아도 원문 해시가 달라진다.
    바이트 해시로 비교하면 'unchanged' 가 영영 성립하지 않아
      · 파서가 매번 불리고
      · 스냅샷이 무한히 쌓인다.
    픽스처는 바이트가 동일해서 T5 만으로는 이 결함이 드러나지 않는다.
    """
    assert re.search(r"\?ver=\d+", WEEK), "픽스처에 캐시버스터가 있어야 한다(전제)"
    # 1초 뒤 다시 받은 것처럼 캐시버스터만 증가시킨다
    b_html = re.sub(r"\?ver=(\d+)",
                    lambda m: f"?ver={int(m.group(1)) + 1}", WEEK)

    a = _fetch_result(WEEK, "likehome_week_menu")
    b = _fetch_result(b_html, "likehome_week_menu",
                      fetched_at="2026-06-01T16:00:00+09:00")

    assert a.content_hash != b.content_hash, "원문 해시는 달라야 한다(전제)"
    assert a.stable_hash == b.stable_hash, "정규화 후에는 같아야 한다"

    r1 = ingest_mod.ingest(dorm_conn, a, parser=likehome.parse, snapshot_dir=tmp_path)
    assert r1.outcome == "success"

    calls = []
    r2 = ingest_mod.ingest(dorm_conn, b, snapshot_dir=tmp_path,
                           parser=lambda h: (calls.append(1), likehome.parse(h))[1])
    assert r2.outcome == "unchanged" and calls == []

    # 스냅샷도 한 행으로 모여야 한다 (무한 증식 방지)
    n = dorm_conn.execute(
        "SELECT COUNT(*) c FROM source_snapshot WHERE source_key = ?",
        ("likehome_week_menu",),
    ).fetchone()["c"]
    assert n == 1


# ═══════════════════════════════════════════════════════════════
# 헬퍼
# ═══════════════════════════════════════════════════════════════

def _fetch_result(html: str, source_key: str,
                  fetched_at: str = "2026-06-01T06:00:00+09:00") -> fetch_mod.FetchResult:
    url = "https://likehome.jbnu.ac.kr/home/main/inner.php?sMenu=B7100"
    return fetch_mod.make_result(source_key, url, url, 200, html.encode("utf-8"),
                                 fetched_at, "html")


def _snapshot_of_db(conn) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
            for t in ("meal_service", "menu_item")}


def _hours(conn, *, term: str, weekday: int, meal_type: str) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO operating_hours
             (id, facility_id, term, weekday, meal_type, open_time, close_time,
              source_id, source_url, observed_at, valid_from,
              confidence, extraction_method, tier)
           VALUES (?,?,?,?,?, '08:00','09:30', ?,?, '2026-08-10T06:00:00+09:00',
                   '2026-08-10', 0.95, 'html_selector', 'T2')""",
        (f"jbnu:hours/생활관/{term}/{weekday}/{meal_type}", LIKEHOME_ID, term,
         weekday, meal_type, SNAPSHOT_ID, SOURCE_URL),
    )
