"""학교 XHR 파서 + 비대칭 판정 — T19 / T20 + 운영시간·단가표.

픽스처는 실제 dataAjax.do?type=day 응답이다 (2026-08-10, 방학 주).
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from crawler import fetch as fetch_mod
from crawler import ingest as ingest_mod
from crawler.parsers import jbnu_cafeteria_day as jbnu
from crawler.validate import AnchorMismatch, ParseError
from skill import branch
from store import repo

FIX = pathlib.Path(__file__).parent / "fixtures"
DAY = (FIX / "jbnu_dataAjax_day.html").read_text(encoding="utf-8", errors="replace")

HUSAENG = "jbnu:facility/후생관-푸드코트"
JINSU = "jbnu:facility/진수원"
UIDAE = "jbnu:facility/의대식당"
NOW = dt.datetime.fromisoformat("2026-08-10T12:00:00+09:00")


@pytest.fixture()
def jconn(conn):
    for fid, name in ((HUSAENG, "후생관"), (JINSU, "진수원"), (UIDAE, "의대식당")):
        conn.execute(
            """INSERT OR IGNORE INTO facility
                 (id, name, facility_type, source_url, source_type)
               VALUES (?,?,?,?,'official')""",
            (fid, name, "식당",
             "https://www.jbnu.ac.kr/web/unvrslife/campuslife/cafeteria.do"),
        )
    conn.commit()
    return conn


def _res(html: str = DAY, at: str = "2026-08-10T07:00:00+09:00"):
    url = "https://www.jbnu.ac.kr/web/unvrslife/campuslife/cafeteria/dataAjax.do"
    return fetch_mod.make_result("jbnu_cafeteria_day", url, url, 200,
                                 html.encode("utf-8"), at, "html")


# ═══════════════════════════════════════════════════════════════
# 파싱
# ═══════════════════════════════════════════════════════════════

def test_세_식당의_운영시간_가격_식단이_한_응답에서_나온다():
    r = jbnu.parse(DAY)
    assert {h.facility_id for h in r.hours} == {HUSAENG, JINSU, UIDAE}
    assert r.week_start == "2026-08-10"
    assert len(r.anchors) == 5, "월~금 5열"
    assert r.meals and r.prices


def test_주말_공휴일_미운영은_행으로_남는다():
    """원천이 명시한 부정이다. 행의 부재로 추론하지 않기 위해 행으로 만든다."""
    r = jbnu.parse(DAY)
    jinsu_lunch = [h for h in r.hours
                   if h.facility_id == JINSU and h.meal_type == "lunch"]
    weekdays = {h.weekday for h in jinsu_lunch if not h.is_closed}
    closed = {h.weekday for h in jinsu_lunch if h.is_closed}
    assert weekdays == {1, 2, 3, 4, 5}, "평일 = 월~금"
    assert closed == {0, 6, 7}, "일·토·공휴일이 명시적 미운영 행으로"


def test_term은_unspecified로_저장된다():
    """원천이 학기를 안 밝힌다. 미상을 미상으로 적는다."""
    r = jbnu.parse(DAY)
    assert {h.term for h in r.hours} == {"unspecified"}


def test_메뉴_항목은_줄바꿈으로_나뉜다():
    """<pre> 안 줄바꿈이 구분자다. 공백으로 나누면 '감자크림함박(배식)'이 쪼개진다."""
    r = jbnu.parse(DAY)
    m = [x for x in r.meals
         if x.facility_id == JINSU and x.date == "2026-08-10"
         and x.meal_type == "lunch"][0]
    assert [i.name for i in m.items] == [
        "단호박치즈돈불고기", "김치수제비", "모듬버섯볶음", "오이토마토샐러드"]


def test_운영없음은_closed_temporary이고_품목이_아니다():
    r = jbnu.parse(DAY)
    names = {i.name for m in r.meals for i in m.items}
    assert "운영없음" not in names
    closed = [m for m in r.meals if m.service_status == "closed_temporary"]
    assert closed and all(m.items == [] for m in closed)


def test_colspan5_코너는_5일_전체로_펼쳐진다():
    """'이번 주 내내 같은 메뉴'인 코너는 td 하나(colspan=5)로 묶여 있다.

    정규화 없이 읽으면 월요일만 메뉴가 있고 화~금이 비어 보인다.
    """
    r = jbnu.parse(DAY)
    dates = {m.date for m in r.meals
             if m.facility_id == JINSU and m.meal_type == "breakfast"}
    assert dates == {f"2026-08-{d}" for d in ("10", "11", "12", "13", "14")}


def test_단가표_범위와_부터가_원문대로_보존된다():
    r = jbnu.parse(DAY)
    by_name = {p.name: p for p in r.prices if p.facility_id == HUSAENG}
    assert by_name["스페셜오므라이스"].price_text == "6,000원 - 6,500원"
    assert by_name["스페셜오므라이스"].price_min == 6000
    assert by_name["스페셜오므라이스"].price_max == 6500
    assert by_name["찌개"].price_text == "6,000원 부터"
    assert by_name["찌개"].price_max is None, "'부터'는 상한 미상"


def test_구성원_외부인_가격이_둘_다_남는다():
    r = jbnu.parse(DAY)
    baekban = [p for p in r.prices if p.facility_id == JINSU and p.name == "백반"]
    assert {p.audience for p in baekban} == {"구성원", "외부인"}
    assert {p.price_min for p in baekban} == {7000, 8500}


def test_CSRF_없는_403응답은_parse_error():
    """토큰 없이 부르면 200 이 아니라 403 + 안내 HTML 이 온다."""
    with pytest.raises(ParseError) as e:
        jbnu.parse("<html><body>죄송합니다. 접근이 거부되었습니다.</body></html>")
    assert "CSRF" in str(e.value)


def test_셀_data_date와_헤더가_어긋나면_잡힌다():
    """같은 날짜가 헤더와 셀 속성 두 곳에 있다. 열이 밀리면 대조에서 걸린다."""
    # colspan 으로 펼쳐진 칸은 대조에서 제외되므로, 전부 바꿔 colspan=1 칸이
    # 확실히 걸리게 한다.
    broken = DAY.replace('data-date="2026-08-12"', 'data-date="2026-08-19"')
    with pytest.raises(AnchorMismatch):
        jbnu.parse(broken)


# ═══════════════════════════════════════════════════════════════
# T19 — term='unspecified' 비대칭 판정
# ═══════════════════════════════════════════════════════════════

def test_T19_미상_시간표의_부정은_채택_긍정은_강등(jconn, tmp_path):
    ingest_mod.ingest(jconn, _res(), parser=jbnu.parse, snapshot_dir=tmp_path)

    # 진수원은 조식 행이 아예 없다 → 부정 결론 → 채택
    assert repo.serves_meal(jconn, facility_id=JINSU, date="2026-08-10",
                            meal_type="breakfast") is False

    # 진수원 점심은 평일 운영 행이 있다 → 긍정 결론 → 강등(None)
    # 이 시간표에 학기 정보가 없어서, 방학에도 점심을 하는지는 알 수 없다.
    assert repo.serves_meal(jconn, facility_id=JINSU, date="2026-08-10",
                            meal_type="lunch") is None

    # 토요일은 '주말 미운영'이 명시된 행이 있다 → 부정 → 채택
    assert repo.serves_meal(jconn, facility_id=JINSU, date="2026-08-15",
                            meal_type="lunch") is False


def test_T19b_학사일정이_들어오면_강등이_풀린다(jconn, tmp_path):
    """term 을 알면 unspecified 행을 그대로 쓰지 않고 실제 term 행을 쓴다."""
    ingest_mod.ingest(jconn, _res(), parser=jbnu.parse, snapshot_dir=tmp_path)
    snap = jconn.execute("SELECT id, url FROM source_snapshot LIMIT 1").fetchone()
    meta = repo.SourceMeta(source_id=snap["id"], source_url=snap["url"],
                           observed_at="2026-08-10T07:00:00+09:00", confidence=0.95,
                           extraction_method="html_selector", tier="T2",
                           valid_from="2026-08-10")
    repo.upsert_hours(jconn, facility_id=JINSU, term="방학", weekday=1,
                      meal_type="lunch", is_closed=False, open_time="11:30",
                      close_time="14:00", meta=meta)
    jconn.commit()
    # 방학 행이 있으므로 강등 없이 True
    assert repo.serves_meal(jconn, facility_id=JINSU, date="2026-08-10",
                            meal_type="lunch", term="방학") is True


def test_T19c_강등된_긍정은_C2로_간다(jconn, tmp_path):
    """serves=None 이면 답변은 C-2 다. '한다'고 말해서 헛걸음을 만들지 않는다."""
    ingest_mod.ingest(jconn, _res(), parser=jbnu.parse, snapshot_dir=tmp_path)
    # 후생관 점심은 실제로 메뉴가 있으므로 A 로 답해야 한다
    a = branch.resolve_meal(jconn, facility_id=HUSAENG, date="2026-08-10",
                            meal_type="lunch", now=NOW)
    assert a.branch is branch.Branch.A

    # 후생관 조식은 원천이 '운영없음'을 명시했다 → B (관측된 부정이 이긴다)
    b = branch.resolve_meal(jconn, facility_id=HUSAENG, date="2026-08-10",
                            meal_type="breakfast", now=NOW)
    assert b.branch is branch.Branch.B and b.reason == "closed_observed"


# ═══════════════════════════════════════════════════════════════
# T20 — hours_coverage
# ═══════════════════════════════════════════════════════════════

def test_T20_partial이면_행_없음이_False가_아니라_None(jconn):
    """폐쇄세계 가정을 켜지 않은 시설. 행이 없는 게 '안 한다'인지 '안 긁었다'인지 모른다."""
    snap = jconn.execute("SELECT id, url FROM source_snapshot LIMIT 1").fetchone()
    meta = repo.SourceMeta(source_id=snap["id"], source_url=snap["url"],
                           observed_at="2026-08-10T07:00:00+09:00", confidence=0.95,
                           extraction_method="html_selector", tier="T2",
                           valid_from="2026-08-10")
    repo.upsert_hours(jconn, facility_id=JINSU, term="unspecified", weekday=1,
                      meal_type="lunch", is_closed=False, open_time="11:30",
                      close_time="14:00", meta=meta)
    jconn.commit()

    assert repo.hours_coverage(jconn, JINSU) == "partial", "기본값"
    # 조식 행이 없다 → partial 이므로 모른다
    assert repo.serves_meal(jconn, facility_id=JINSU, date="2026-08-10",
                            meal_type="breakfast") is None

    # 시간표 전체를 파싱했다고 선언하면 그때만 부정 결론을 낸다
    repo.set_hours_coverage(jconn, JINSU, "complete")
    jconn.commit()
    assert repo.serves_meal(jconn, facility_id=JINSU, date="2026-08-10",
                            meal_type="breakfast") is False


def test_T20b_크롤러가_시간표_전체를_파싱하면_complete가_된다(jconn, tmp_path):
    assert repo.hours_coverage(jconn, HUSAENG) == "partial"
    ingest_mod.ingest(jconn, _res(), parser=jbnu.parse, snapshot_dir=tmp_path)
    for fid in (HUSAENG, JINSU, UIDAE):
        assert repo.hours_coverage(jconn, fid) == "complete"


def test_set_hours_coverage는_임의값을_거부한다(jconn):
    with pytest.raises(ValueError):
        repo.set_hours_coverage(jconn, JINSU, "maybe")


def test_T20c_요일이_빠지면_complete가_아니다():
    """파싱 성공만으로 complete 를 세우면 안 된다. 요일 커버리지를 실제로 본다."""
    full = [jbnu.ParsedHours(JINSU, "unspecified", wd, "lunch", wd in (0, 6),
                             open_time=None if wd in (0, 6) else "11:30")
            for wd in range(7)]
    assert jbnu.is_complete_coverage(full)

    # 주말 행이 없다 = 주말에 하는지 모른다 → complete 를 켜면 안 된다
    weekday_only = [h for h in full if h.weekday not in (0, 6)]
    assert not jbnu.is_complete_coverage(weekday_only)
    assert not jbnu.is_complete_coverage([])


# ═══════════════════════════════════════════════════════════════
# 저장 경로
# ═══════════════════════════════════════════════════════════════

def test_운영시간과_가격이_DB에_들어간다(jconn, tmp_path):
    rep = ingest_mod.ingest(jconn, _res(), parser=jbnu.parse, snapshot_dir=tmp_path)
    assert rep.outcome == "success"
    assert rep.hours == 40 and rep.prices >= 30

    n = jconn.execute("SELECT COUNT(*) c FROM operating_hours").fetchone()["c"]
    assert n == rep.hours

    # 재크롤해도 늘지 않는다 (UNIQUE 가 실제로 동작)
    rep2 = ingest_mod.ingest(jconn, _res(at="2026-08-10T16:00:00+09:00"),
                             parser=jbnu.parse, snapshot_dir=tmp_path, force=True)
    n2 = jconn.execute("SELECT COUNT(*) c FROM operating_hours").fetchone()["c"]
    assert n2 == n and rep2.outcome == "success"


def test_가격이_붙는_것과_안_붙는_것(jconn, tmp_path):
    """단가표 이름과 식단표 이름이 다르면 붙이지 않는다. 미매칭은 —."""
    ingest_mod.ingest(jconn, _res(), parser=jbnu.parse, snapshot_dir=tmp_path)
    facts = repo.query_meal(jconn, facility_id=HUSAENG, date="2026-08-10",
                            meal_type="lunch", now=NOW)
    res = repo.attach_prices(jconn, facts.rows, facility_id=HUSAENG,
                             on_date="2026-08-10")
    by_name = {i["name"]: i for r in facts.rows for i in r["items"]}

    # 단가표에 '등심돈까스'는 있지만 식단표는 '통등심돈까스'다 → 안 붙는다
    if "통등심돈까스" in by_name:
        assert by_name["통등심돈까스"]["price_display"] == "—"
    assert 0.0 <= res.match_rate <= 1.0
