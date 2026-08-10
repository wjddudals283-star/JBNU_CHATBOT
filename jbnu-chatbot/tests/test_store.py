"""저장소 계층 수용 기준 — T9 / T11 / T17 + 불변 규칙 가드.

이 테스트들은 "정확한 정보만"을 코드로 증명하는 부분이다.
통과가 목적이 아니라, 완화하면 실패하는 것이 목적이다.
"""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from conftest import FACILITY_ID, SNAPSHOT_ID, SOURCE_URL
from store import repo

NOW = dt.datetime.fromisoformat("2026-08-10T12:20:00+09:00")


HOURS_URL = "https://www.jbnu.ac.kr/web/unvrslife/campuslife/cafeteria.do"


def _hours_meta() -> repo.SourceMeta:
    return repo.SourceMeta(
        source_id=SNAPSHOT_ID, source_url=HOURS_URL,
        observed_at="2026-08-10T06:00:00+09:00", confidence=0.95,
        extraction_method="html_selector", tier="T2", valid_from="2026-08-10",
    )


def _add_hours(conn, *, term: str, weekday: int, meal_type: str,
               open_time: str = "11:30", close_time: str = "14:00") -> None:
    repo.upsert_hours(conn, facility_id=FACILITY_ID, term=term, weekday=weekday,
                      meal_type=meal_type, is_closed=False, open_time=open_time,
                      close_time=close_time, meta=_hours_meta())


def _add_closed(conn, *, term: str, weekday: int, meal_type: str) -> None:
    """원천이 명시한 미운영 행."""
    repo.upsert_hours(conn, facility_id=FACILITY_ID, term=term, weekday=weekday,
                      meal_type=meal_type, is_closed=True,
                      note="주말·공휴일 미운영 (원천 명시)", meta=_hours_meta())


def _week_of_meals() -> list[repo.ParsedMeal]:
    """후생관 8/10 점심 — 실측 구조 축약본 (zone 2단, 코너 다수)."""
    return [
        repo.ParsedMeal(
            facility_id=FACILITY_ID, date="2026-08-10", meal_type="lunch",
            service_status="operating", zone="한식", corner="찌개*돌솥",
            items=[repo.ParsedItem("돈목살짜글이*계란후라이", display_order=0)],
        ),
        repo.ParsedMeal(
            facility_id=FACILITY_ID, date="2026-08-10", meal_type="lunch",
            service_status="closed_temporary", zone="한식", corner="돌솥", items=[],
        ),
        repo.ParsedMeal(
            facility_id=FACILITY_ID, date="2026-08-10", meal_type="lunch",
            service_status="operating", zone="양식", corner="돈까스류",
            items=[
                repo.ParsedItem("통등심돈까스", display_order=0),
                repo.ParsedItem("치즈돈까스", display_order=1),
            ],
        ),
        repo.ParsedMeal(
            facility_id=FACILITY_ID, date="2026-08-10", meal_type="lunch",
            service_status="operating", zone="분식", corner="라면",
            items=[repo.ParsedItem("신라면", display_order=0)],
        ),
    ]


# ═══════════════════════════════════════════════════════════════
# T9 — 출처 없는 사실은 물리적으로 저장될 수 없다
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("missing", ["source_id", "source_url", "observed_at",
                                     "confidence", "extraction_method", "tier"])
def test_T9_출처메타_NULL_삽입은_DB제약_위반(conn, missing):
    row = {
        "id": "jbnu:meal/2026-08-10/후생관-푸드코트/lunch",
        "facility_id": FACILITY_ID, "date": "2026-08-10", "meal_type": "lunch",
        "service_status": "operating", "zone": "", "corner": "",
        "source_id": SNAPSHOT_ID, "source_url": SOURCE_URL,
        "observed_at": "2026-08-10T06:00:00+09:00", "valid_from": "2026-08-10",
        "confidence": 0.95, "extraction_method": "json_api", "tier": "T1",
    }
    row[missing] = None
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO meal_service
               (id, facility_id, date, meal_type, service_status, zone, corner,
                source_id, source_url, observed_at, valid_from,
                confidence, extraction_method, tier)
               VALUES (:id,:facility_id,:date,:meal_type,:service_status,:zone,:corner,
                       :source_id,:source_url,:observed_at,:valid_from,
                       :confidence,:extraction_method,:tier)""",
            row,
        )


def test_T9b_존재하지_않는_snapshot_참조는_외래키_위반(conn):
    """SQLite 외래키는 연결마다 기본 OFF. 꺼져 있으면 이 테스트가 실패한다."""
    assert repo.foreign_keys_on(conn), "PRAGMA foreign_keys 가 꺼져 있다"
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO meal_service
               (id, facility_id, date, meal_type, service_status,
                source_id, source_url, observed_at, valid_from,
                confidence, extraction_method, tier)
               VALUES ('x', ?, '2026-08-10', 'lunch', 'operating',
                       'jbnu:snap/없는스냅샷', ?, '2026-08-10T06:00:00+09:00',
                       '2026-08-10', 0.95, 'json_api', 'T1')""",
            (FACILITY_ID, SOURCE_URL),
        )


# ═══════════════════════════════════════════════════════════════
# T11 — 만료 없는 수기(T4) 정보는 저장 불가
# ═══════════════════════════════════════════════════════════════

def test_T11_pledge_progress_valid_to_없이_삽입_실패(conn):
    conn.execute("INSERT INTO pledge (id, term, number, title) VALUES (?,?,?,?)",
                 ("jbnu:pledge/58/03", 58, 3, "열람실 24시간 개방"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO pledge_progress
               (id, pledge_id, status_value, author, approved_by,
                source_id, source_url, observed_at, valid_from, valid_to,
                confidence, extraction_method, tier)
               VALUES ('jbnu:pp/58/03/1', 'jbnu:pledge/58/03', '진행중',
                       '정책국장', '부총학생회장',
                       ?, ?, '2026-07-15T10:00:00+09:00', '2026-07-15', NULL,
                       1.0, 'manual_admin', 'T4')""",
            (SNAPSHOT_ID, SOURCE_URL),
        )


@pytest.mark.parametrize("field", ["author", "approved_by"])
def test_T11b_T4는_작성자_승인자_없이_저장_불가(conn, field):
    conn.execute("INSERT INTO pledge (id, term, number, title) VALUES (?,?,?,?)",
                 ("jbnu:pledge/58/07", 58, 7, "셔틀버스 배차 확대"))
    vals = {"author": "정책국장", "approved_by": "부총학생회장"}
    vals[field] = None
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO pledge_progress
               (id, pledge_id, status_value, author, approved_by,
                source_id, source_url, observed_at, valid_from, valid_to,
                confidence, extraction_method, tier)
               VALUES ('jbnu:pp/58/07/1', 'jbnu:pledge/58/07', '완료',
                       :author, :approved_by,
                       :sid, :url, '2026-07-15T10:00:00+09:00', '2026-07-15',
                       '2026-09-30', 1.0, 'manual_admin', 'T4')""",
            {**vals, "sid": SNAPSHOT_ID, "url": SOURCE_URL},
        )


def test_T11c_pledge_progress_밖에서도_T4는_valid_to_필수(conn):
    """불변 규칙은 한 테이블에만 걸면 새는다. CHECK 로 전 fact 테이블에 강제."""
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO meal_service
               (id, facility_id, date, meal_type, service_status,
                source_id, source_url, observed_at, valid_from, valid_to,
                confidence, extraction_method, tier)
               VALUES ('jbnu:meal/t4', ?, '2026-08-10', 'lunch', 'operating',
                       ?, ?, '2026-08-10T06:00:00+09:00', '2026-08-10', NULL,
                       1.0, 'manual_admin', 'T4')""",
            (FACILITY_ID, SNAPSHOT_ID, SOURCE_URL),
        )


# ═══════════════════════════════════════════════════════════════
# T17 — zone/corner UNIQUE 가 실제로 동작하는가 (중복 폭증 방지)
# ═══════════════════════════════════════════════════════════════

def test_T17_같은_주간표_두번_크롤해도_행수_불변(conn, meta):
    meals = _week_of_meals()

    for m in meals:
        repo.upsert_meal(conn, m, meta)
    conn.commit()
    first_meals = conn.execute("SELECT COUNT(*) c FROM meal_service").fetchone()["c"]
    first_items = conn.execute("SELECT COUNT(*) c FROM menu_item").fetchone()["c"]

    # 두 번째 크롤 — 관측 시각만 바뀐다
    meta2 = repo.SourceMeta(
        source_id=SNAPSHOT_ID, source_url=SOURCE_URL,
        observed_at="2026-08-10T10:30:00+09:00", confidence=0.95,
        extraction_method="json_api", tier="T1", valid_from="2026-08-10",
    )
    for m in _week_of_meals():
        repo.upsert_meal(conn, m, meta2)
    conn.commit()

    assert conn.execute("SELECT COUNT(*) c FROM meal_service").fetchone()["c"] == first_meals
    assert conn.execute("SELECT COUNT(*) c FROM menu_item").fetchone()["c"] == first_items
    assert first_meals == 4, "코너별로 별개 행이어야 한다"

    row = conn.execute(
        "SELECT observed_at FROM meal_service WHERE zone='한식' AND corner='돌솥'"
    ).fetchone()
    assert row["observed_at"] == "2026-08-10T10:30:00+09:00", "갱신은 돼야 한다"


def test_T17b_zone_corner_NULL은_거부된다(conn):
    """NULL 을 허용하면 UNIQUE 가 무력화돼 매 크롤마다 중복이 쌓인다.

    가상의 위험이 아니다. 의대식당 조식은 원천이 cate2='' cate3='' 를 준다
    (주간 72건 중 5건). '비었으니 NULL' 로 매핑하면 정확히 그 행들이 매 크롤마다
    중복 삽입된다. 실측: 3회 크롤 → 3배(12행). NOT NULL DEFAULT '' 가 이걸 막는다.
    """
    for col in ("zone", "corner"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                f"""INSERT INTO meal_service
                   (id, facility_id, date, meal_type, service_status, {col},
                    source_id, source_url, observed_at, valid_from,
                    confidence, extraction_method, tier)
                   VALUES ('jbnu:meal/null-{col}', ?, '2026-08-10', 'lunch',
                           'operating', NULL, ?, ?, '2026-08-10T06:00:00+09:00',
                           '2026-08-10', 0.95, 'json_api', 'T1')""",
                (FACILITY_ID, SNAPSHOT_ID, SOURCE_URL),
            )


def test_T17c_항목이_줄면_이전_항목이_남지_않는다(conn, meta):
    """5개 → 2개로 줄었을 때 이전 3개가 답변에 섞이면 안 된다."""
    many = repo.ParsedMeal(
        facility_id=FACILITY_ID, date="2026-08-11", meal_type="lunch",
        service_status="operating", zone="분식", corner="라면",
        items=[repo.ParsedItem(f"메뉴{i}", display_order=i) for i in range(5)],
    )
    repo.upsert_meal(conn, many, meta)
    few = repo.ParsedMeal(
        facility_id=FACILITY_ID, date="2026-08-11", meal_type="lunch",
        service_status="operating", zone="분식", corner="라면",
        items=[repo.ParsedItem("메뉴0", display_order=0)],
    )
    repo.upsert_meal(conn, few, meta)
    conn.commit()
    n = conn.execute(
        """SELECT COUNT(*) c FROM menu_item mi JOIN meal_service ms
             ON ms.id = mi.meal_service_id WHERE ms.date='2026-08-11'"""
    ).fetchone()["c"]
    assert n == 1


# ═══════════════════════════════════════════════════════════════
# 불변 규칙 가드 — status='verified' 필터
# ═══════════════════════════════════════════════════════════════

def test_격리된_레코드는_조회되지_않는다(conn, meta):
    for m in _week_of_meals():
        repo.upsert_meal(conn, m, meta)
    conn.commit()

    facts = repo.query_meal(conn, facility_id=FACILITY_ID, date="2026-08-10",
                            meal_type="lunch", now=NOW)
    assert len(facts.rows) == 4

    target = repo.meal_service_id(FACILITY_ID, "2026-08-10", "lunch", "양식", "돈까스류")
    repo.quarantine(conn, "meal_service", target, reason="가격 이상치")
    conn.commit()

    facts2 = repo.query_meal(conn, facility_id=FACILITY_ID, date="2026-08-10",
                             meal_type="lunch", now=NOW)
    assert len(facts2.rows) == 3
    assert all(r["corner"] != "돈까스류" for r in facts2.rows)


def test_status_필터_우회_시도는_예외(conn):
    with pytest.raises(repo.VerifiedFilterBypass):
        repo._fact_select(conn, "meal_service", "status = 'quarantine'", ())


def test_신선도_초과는_stale_로_표시된다(conn, meta):
    """T6 의 저장소 쪽 절반. 값을 지우지 않고 stale 플래그만 세운다."""
    for m in _week_of_meals():
        repo.upsert_meal(conn, m, meta)
    conn.commit()
    later = dt.datetime.fromisoformat("2026-08-11T13:00:00+09:00")  # 31시간 후
    facts = repo.query_meal(conn, facility_id=FACILITY_ID, date="2026-08-10",
                            meal_type="lunch", now=later)
    assert facts.found and facts.stale
    assert facts.max_age_hours is not None and facts.max_age_hours > 24


# ═══════════════════════════════════════════════════════════════
# 가격 조인 — 정확 일치만 (T16 의 저장소 쪽 절반)
# ═══════════════════════════════════════════════════════════════

def test_T16_유사한_이름에는_가격이_붙지_않는다(conn, meta):
    """단가표 '등심돈까스'/'계란라면' 은 식단표 '통등심돈까스'/'신라면' 과 다른 상품이다."""
    price_meta = repo.SourceMeta(
        source_id=SNAPSHOT_ID, source_url=SOURCE_URL,
        observed_at="2026-08-10T06:00:00+09:00", confidence=0.95,
        extraction_method="html_selector", tier="T2", valid_from="2026-08-01",
    )
    repo.upsert_price(conn, facility_id=FACILITY_ID, name="등심돈까스",
                      price_text="6,000원", price_min=6000, price_max=6000,
                      meta=price_meta)
    repo.upsert_price(conn, facility_id=FACILITY_ID, name="계란라면",
                      price_text="3,000원", price_min=3000, price_max=3000,
                      meta=price_meta)
    repo.upsert_price(conn, facility_id=FACILITY_ID, name="치즈돈까스",
                      price_text="6,500원", price_min=6500, price_max=6500,
                      meta=price_meta)
    for m in _week_of_meals():
        repo.upsert_meal(conn, m, meta)
    conn.commit()

    facts = repo.query_meal(conn, facility_id=FACILITY_ID, date="2026-08-10",
                            meal_type="lunch", now=NOW)
    result = repo.attach_prices(conn, facts.rows, facility_id=FACILITY_ID,
                                on_date="2026-08-10")

    by_name = {i["name"]: i for r in facts.rows for i in r["items"]}
    assert by_name["통등심돈까스"]["price_text"] is None
    assert by_name["통등심돈까스"]["price_display"] == "—"
    assert by_name["신라면"]["price_text"] is None
    assert by_name["치즈돈까스"]["price_text"] == "6,500원"   # 정확 일치만 붙는다
    assert result.matched == 1 and result.total == 4
    assert result.match_rate == 0.25


def test_정규화는_괄호_안_내용을_지우지_않는다():
    """'오므라이스류(기본)' → '오므라이스류' 로 접히면 다른 상품에 가격이 붙는다."""
    assert repo.normalize_name("오므라이스류(기본)") != repo.normalize_name("오므라이스류")
    # 공백·괄호문자·유니코드 표기 차이만 흡수한다
    assert repo.normalize_name("생협 김밥") == repo.normalize_name("생협김밥")
    assert repo.normalize_name("특식(덮밥/볶음밥)") == repo.normalize_name("특식덮밥/볶음밥")
    # 접두어는 절대 벗기지 않는다
    assert repo.normalize_name("신라면") != repo.normalize_name("라면")
    assert repo.normalize_name("통등심돈까스") != repo.normalize_name("등심돈까스")


def test_T16b_가격표기_범위는_하한으로_접히지_않는다(conn, meta):
    """6,000~6,500원짜리를 '6,000원'이라 답하면 학생이 돈이 모자란다.

    가격을 낮게 말하는 건 높게 말하는 것보다 나쁘다 → price_text 원문 렌더.
    """
    pm = repo.SourceMeta(
        source_id=SNAPSHOT_ID, source_url=SOURCE_URL,
        observed_at="2026-08-10T06:00:00+09:00", confidence=0.95,
        extraction_method="html_selector", tier="T2", valid_from="2026-08-01",
    )
    repo.upsert_price(conn, facility_id=FACILITY_ID, name="스페셜오므라이스",
                      price_text="6,000원 - 6,500원", price_min=6000, price_max=6500, meta=pm)
    repo.upsert_price(conn, facility_id=FACILITY_ID, name="찌개",
                      price_text="6,000원 부터", price_min=6000, price_max=None,
                      note="메뉴별 상이", meta=pm)
    repo.upsert_price(conn, facility_id=FACILITY_ID, name="백반",
                      price_text="7,000원", price_min=7000, price_max=7000,
                      audience="구성원", meta=pm)
    repo.upsert_price(conn, facility_id=FACILITY_ID, name="백반",
                      price_text="8,500원", price_min=8500, price_max=8500,
                      audience="외부인", meta=pm)

    repo.upsert_meal(conn, repo.ParsedMeal(
        facility_id=FACILITY_ID, date="2026-08-10", meal_type="lunch",
        service_status="operating", zone="양식", corner="오므라이스",
        items=[repo.ParsedItem("스페셜오므라이스", display_order=0),
               repo.ParsedItem("찌개", display_order=1),
               repo.ParsedItem("백반", display_order=2)],
    ), meta)
    conn.commit()

    assert conn.execute("SELECT COUNT(*) c FROM menu_price").fetchone()["c"] == 4, \
        "구성원/외부인은 같은 이름이라도 별개 행이어야 한다"

    facts = repo.query_meal(conn, facility_id=FACILITY_ID, date="2026-08-10",
                            meal_type="lunch", now=NOW)
    repo.attach_prices(conn, facts.rows, facility_id=FACILITY_ID, on_date="2026-08-10")
    by_name = {i["name"]: i for r in facts.rows for i in r["items"]}

    # 원문 그대로. 하한만 뽑아 '6,000원'으로 접지 않는다.
    assert by_name["스페셜오므라이스"]["price_display"] == "6,000원 - 6,500원"
    assert by_name["찌개"]["price_display"] == "6,000원 부터"
    # 학생이 사용자이므로 구성원가가 기본. 외부인가는 덮어쓰지 않고 병기한다.
    assert by_name["백반"]["price_display"] == "7,000원"
    assert by_name["백반"]["price_audience"] == "구성원"
    assert by_name["백반"]["price_other_audiences"] == ["외부인 8,500원"]


def test_price_text가_비면_저장_거부(conn):
    pm = repo.SourceMeta(
        source_id=SNAPSHOT_ID, source_url=SOURCE_URL,
        observed_at="2026-08-10T06:00:00+09:00", confidence=0.95,
        extraction_method="html_selector", tier="T2", valid_from="2026-08-01",
    )
    with pytest.raises(sqlite3.IntegrityError):
        repo.upsert_price(conn, facility_id=FACILITY_ID, name="무언가",
                          price_text="", price_min=1000, meta=pm)


# ═══════════════════════════════════════════════════════════════
# 추론 금지 — 운영시간으로 service_status 를 만들지 않는다
# ═══════════════════════════════════════════════════════════════

def test_운영시간_데이터가_없으면_serves_meal은_None(conn):
    """모를 때 False 로 답하면 그게 추론이다. None 은 '모른다'를 명시한다."""
    assert repo.serves_meal(conn, facility_id=FACILITY_ID, date="2026-08-10",
                            meal_type="breakfast") is None


def test_term을_모르면_긍정은_단정하지_않는다(conn):
    """'방학' 행이 있어도 그 날짜가 방학인지 모르면 '한다'고 말할 수 없다.

    긍정을 잘못 쓰면 학생이 헛걸음한다. 부정보다 훨씬 비싼 오류다.
    """
    _add_hours(conn, term="방학", weekday=1, meal_type="lunch")
    conn.commit()
    assert repo.weekday_index("2026-08-10") == 1   # 월요일, 0=일 규약
    assert repo.serves_meal(conn, facility_id=FACILITY_ID, date="2026-08-10",
                            meal_type="lunch") is None
    # term 을 알면 그 행을 그대로 쓴다
    assert repo.serves_meal(conn, facility_id=FACILITY_ID, date="2026-08-10",
                            meal_type="lunch", term="방학") is True


def test_partial이면_행이_없어도_부정을_단정하지_않는다(conn):
    """행의 부재가 '안 한다'인지 '아직 안 긁었다'인지 구분되지 않는다 (T20)."""
    _add_hours(conn, term="방학", weekday=1, meal_type="lunch")
    conn.commit()
    assert repo.hours_coverage(conn, FACILITY_ID) == "partial"
    assert repo.serves_meal(conn, facility_id=FACILITY_ID, date="2026-08-10",
                            meal_type="dinner", term="방학") is None

    repo.set_hours_coverage(conn, FACILITY_ID, "complete")
    conn.commit()
    assert repo.serves_meal(conn, facility_id=FACILITY_ID, date="2026-08-10",
                            meal_type="dinner", term="방학") is False


def test_명시된_미운영은_coverage와_무관하게_부정이다(conn):
    """원천이 '주말 미운영'이라고 적은 건 관측이다. 폐쇄세계 가정이 필요 없다."""
    _add_hours(conn, term="unspecified", weekday=1, meal_type="lunch")
    _add_closed(conn, term="unspecified", weekday=6, meal_type="lunch")
    conn.commit()
    assert repo.hours_coverage(conn, FACILITY_ID) == "partial"
    # 2026-08-15 는 토요일 → 명시적 미운영 행이 있다
    assert repo.serves_meal(conn, facility_id=FACILITY_ID, date="2026-08-15",
                            meal_type="lunch") is False


def test_term별로_답이_갈리면_단정하지_않는다(conn):
    """학기중엔 석식을 하고 방학엔 안 한다면, 어느 term 인지 모르는 채로 단정할 수 없다."""
    _add_hours(conn, term="학기중", weekday=1, meal_type="dinner")
    _add_hours(conn, term="방학", weekday=1, meal_type="lunch")
    conn.commit()
    assert repo.serves_meal(conn, facility_id=FACILITY_ID, date="2026-08-10",
                            meal_type="dinner") is None


def test_그_요일_데이터가_없으면_None(conn):
    _add_hours(conn, term="방학", weekday=1, meal_type="lunch")
    conn.commit()
    # 2026-08-15 는 토요일(weekday 6). 해당 행이 없고 coverage=partial → 모른다
    assert repo.serves_meal(conn, facility_id=FACILITY_ID, date="2026-08-15",
                            meal_type="lunch") is None


# ═══════════════════════════════════════════════════════════════
# 크롤 지표
# ═══════════════════════════════════════════════════════════════

def test_크롤지표_기록과_조회(conn):
    repo.start_crawl(conn, run_id="run-1", source_key="coop_week_menu",
                     started_at="2026-08-10T06:00:00+09:00")
    repo.finish_crawl(conn, "run-1", outcome="success",
                      finished_at="2026-08-10T06:00:12+09:00", items_parsed=15)
    repo.record_metric(conn, "run-1", "price_match_rate", 0.25,
                       numerator=1, denominator=4)
    repo.record_metric(conn, "run-1", "conflict_rate", 0.0, numerator=0, denominator=15)
    conn.commit()
    hist = repo.metric_history(conn, "coop_week_menu", "price_match_rate")
    assert len(hist) == 1 and hist[0]["value"] == 0.25
