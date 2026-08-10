"""시계열 테이블 식별자 규칙 — 다른 날짜로 두 번 넣어본다.

★ 이 유형은 **하루 뒤에** 터진다. 당일 테스트로는 절대 안 잡힌다.
  operating_hours 에서 실제로 겪었다(이튿날 07:00 크롤이 PK 충돌로 죽었을 것).
  그래서 시계열 테이블은 전부 '다른 valid_from 으로 2회 삽입'을 규칙으로 둔다.
"""

from __future__ import annotations

import sqlite3

import pytest

from conftest import FACILITY_ID, SNAPSHOT_ID, SOURCE_URL
from store import repo

D1, D2 = "2026-08-24", "2026-09-07"


def _meta(valid_from: str, *, tier: str = "T2", **kw) -> repo.SourceMeta:
    return repo.SourceMeta(
        source_id=SNAPSHOT_ID, source_url=SOURCE_URL,
        observed_at=f"{valid_from}T07:00:00+09:00", confidence=0.95,
        extraction_method="html_selector", tier=tier, valid_from=valid_from, **kw)


# ═══════════════════════════════════════════════════════════════
# 식별자에 시점이 들어 있는가 (정적 점검)
# ═══════════════════════════════════════════════════════════════

def test_시계열_식별자에는_시점이_들어간다():
    assert D1 in repo.operating_hours_id(FACILITY_ID, "unspecified", 1, "lunch", D1)
    assert D1 in repo.menu_price_id(FACILITY_ID, "김밥", "전체", D1)
    assert D1 in repo.procedure_id("휴학 신청", D1)
    assert D1 in repo.pledge_progress_id("jbnu:pledge/58/03", D1)

    # 같은 대상 · 다른 시점 → 다른 ID
    for fn, args in (
        (repo.operating_hours_id, (FACILITY_ID, "unspecified", 1, "lunch")),
        (repo.menu_price_id, (FACILITY_ID, "김밥", "전체")),
        (repo.procedure_id, ("휴학 신청",)),
        (repo.pledge_progress_id, ("jbnu:pledge/58/03",)),
    ):
        assert fn(*args, D1) != fn(*args, D2), f"{fn.__name__} 이 시점을 무시한다"


def test_날짜가_없는_테이블은_규칙_대상이_아니다():
    """meal_service·menu_item 은 ID 에 이미 date 가 있어 안전하다."""
    a = repo.meal_service_id(FACILITY_ID, "2026-08-24", "lunch", "한식", "백반")
    b = repo.meal_service_id(FACILITY_ID, "2026-09-07", "lunch", "한식", "백반")
    assert a != b and "2026-08-24" in a


# ═══════════════════════════════════════════════════════════════
# 실제로 두 날짜에 넣어본다 (동적 점검)
# ═══════════════════════════════════════════════════════════════

def test_operating_hours_두_날짜_삽입(conn):
    for d in (D1, D2):
        repo.upsert_hours(conn, facility_id=FACILITY_ID, term="unspecified",
                          weekday=1, meal_type="lunch", is_closed=False,
                          open_time="11:30", close_time="14:00", meta=_meta(d))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM operating_hours").fetchone()["c"] == 2
    assert repo.hours_observation_dates(conn, FACILITY_ID) == [D1, D2]


def test_menu_price_두_날짜_삽입(conn):
    """단가 개정. 이전 가격을 덮어쓰면 '언제 올랐나'를 답할 수 없다."""
    repo.upsert_price(conn, facility_id=FACILITY_ID, name="생협김밥",
                      price_text="3,000원", price_min=3000, price_max=3000,
                      meta=_meta(D1))
    repo.upsert_price(conn, facility_id=FACILITY_ID, name="생협김밥",
                      price_text="3,500원", price_min=3500, price_max=3500,
                      meta=_meta(D2))
    conn.commit()
    rows = conn.execute(
        "SELECT valid_from, price_text FROM menu_price ORDER BY valid_from").fetchall()
    assert [(r["valid_from"], r["price_text"]) for r in rows] == [
        (D1, "3,000원"), (D2, "3,500원")]


def test_procedure_두_날짜_삽입(conn):
    """요강 개정. 개정본은 이전 것과 별개 레코드다."""
    for d, fee in ((D1, "없음"), (D2, "1만원")):
        conn.execute(
            """INSERT INTO procedure
                 (id, title, domain, fee,
                  source_id, source_url, observed_at, valid_from,
                  confidence, extraction_method, tier)
               VALUES (?,?,?,?,?,?,?,?,0.9,'pdf_parse','T3')""",
            (repo.procedure_id("휴학 신청", d), "휴학 신청", "D1", fee,
             SNAPSHOT_ID, SOURCE_URL, f"{d}T07:00:00+09:00", d))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) c FROM procedure").fetchone()["c"] == 2


def test_pledge_progress_두_날짜_삽입(conn):
    """공약 진행상황. 덮어쓰면 '언제 완료됐나'를 답할 수 없다.

    투명성이 기능인 도메인이라 이력 소실이 특히 비싸다.
    """
    conn.execute("INSERT INTO pledge (id, term, number, title) VALUES (?,?,?,?)",
                 ("jbnu:pledge/58/03", 58, 3, "열람실 24시간 개방"))
    for d, status in ((D1, "진행중"), (D2, "완료")):
        conn.execute(
            """INSERT INTO pledge_progress
                 (id, pledge_id, status_value, author, approved_by,
                  source_id, source_url, observed_at, valid_from, valid_to,
                  confidence, extraction_method, tier)
               VALUES (?,?,?,?,?,?,?,?,?,?,1.0,'manual_admin','T4')""",
            (repo.pledge_progress_id("jbnu:pledge/58/03", d), "jbnu:pledge/58/03",
             status, "정책국장", "부총학생회장", SNAPSHOT_ID, SOURCE_URL,
             f"{d}T10:00:00+09:00", d, "2026-12-31"))
    conn.commit()
    rows = conn.execute(
        "SELECT valid_from, status_value FROM pledge_progress ORDER BY valid_from"
    ).fetchall()
    assert [(r["valid_from"], r["status_value"]) for r in rows] == [
        (D1, "진행중"), (D2, "완료")]


def test_시점을_뺀_ID는_이튿날_충돌한다(conn):
    """규칙을 어겼을 때 실제로 무슨 일이 나는지 고정한다."""
    fixed = "jbnu:hours/시점없음"
    for d in (D1, D2):
        m = _meta(d).as_row()
        stmt = ("""INSERT INTO operating_hours
                     (id, facility_id, term, weekday, meal_type, is_closed,
                      open_time, close_time,
                      source_id, source_url, observed_at, valid_from,
                      confidence, extraction_method, status, tier)
                   VALUES (?,?,'unspecified',1,'lunch',0,'11:30','14:00',
                           ?,?,?,?,?,?,?,?)""")
        args = (fixed, FACILITY_ID, m["source_id"], m["source_url"],
                m["observed_at"], m["valid_from"], m["confidence"],
                m["extraction_method"], m["status"], m["tier"])
        if d == D1:
            conn.execute(stmt, args)
        else:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(stmt, args)
