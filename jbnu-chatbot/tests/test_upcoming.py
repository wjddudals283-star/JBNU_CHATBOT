"""deadline.upcoming — 작업 12.

+ B분기 근거 누락 회귀 (봇테스트에서 발견)
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from crawler.parsers import jbnu_academic_schedule as sched
from skill import branch, kakao, server, templates
from store import repo

FIX = pathlib.Path(__file__).parent / "fixtures"
Y2026 = (FIX / "schedule_2026_2.html").read_text(encoding="utf-8", errors="replace")
KST = dt.timezone(dt.timedelta(hours=9))
SCHED_URL = "https://www.jbnu.ac.kr/web/academic/schedule.do"


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "u.db"
    c = repo.connect(path)
    repo.init_db(c)
    c.execute("""INSERT INTO source_snapshot
                   (id, source_key, url, fetched_at, content_hash, content_path, media_type)
                 VALUES ('s','jbnu_academic_schedule',?, '2026-08-31T05:30:00+09:00',
                         'h','p','html')""", (SCHED_URL,))
    meta = repo.SourceMeta(source_id="s", source_url=SCHED_URL,
                           observed_at="2026-08-31T05:30:00+09:00", confidence=0.95,
                           extraction_method="html_selector", tier="T1",
                           valid_from="2026-08-31")
    for e in sched.parse(Y2026, ac_year=2026, ac_semester=2).calendar_entries:
        repo.upsert_calendar(c, e, meta)
    c.commit()
    c.close()
    return path


def _payload(utterance: str, **params):
    return {"userRequest": {"utterance": utterance, "user": {"id": "u"}},
            "action": {"params": params, "detailParams": {}}}


def _at(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso)


# ═══════════════════════════════════════════════════════════════
# 조회
# ═══════════════════════════════════════════════════════════════

def test_다가오는_일정이_나온다(db):
    r = server.handle(db, "deadline.upcoming", _payload("곧 뭐 있어"),
                      now=_at("2026-09-01T09:00:00+09:00"))
    assert kakao.validate(r) == []
    card = r["template"]["outputs"][0]["listCard"]
    titles = [i["title"] for i in card["items"]]
    assert "제2학기 개강, 일반대학원 종합시험" in titles


def test_진행_중인_기간은_마감까지_보여준다(db):
    """★ 9/3 은 수강신청 변경(9/1~9/7) 한가운데. 시작했다고 빼면 놓치게 만든다."""
    r = server.handle(db, "deadline.upcoming", _payload("마감 뭐 있어"),
                      now=_at("2026-09-03T09:00:00+09:00"))
    card = r["template"]["outputs"][0]["listCard"]
    hit = next(i for i in card["items"]
               if i["title"].startswith("제2학기 수강신청 변경"))
    assert "진행 중" in hit["description"] and "4일 남음" in hit["description"]


def test_D_day_표기(db):
    # 관측이 8/31 이라 신선도(30일) 안쪽 날짜를 쓴다. 9/28~30 이 D-1 이다.
    r = server.handle(db, "deadline.upcoming", _payload("학사일정"),
                      now=_at("2026-09-27T09:00:00+09:00"))
    card = r["template"]["outputs"][0]["listCard"]
    descs = " ".join(i["description"] for i in card["items"])
    assert "내일" in descs
    assert any("D-" in i["description"] for i in card["items"])


def test_해당_기간에_없으면_없다고_말한다(db):
    """빈 목록을 만들지 않는다. '없다'와 '모른다'는 다르다."""
    r = server.handle(db, "deadline.upcoming", _payload("곧 뭐 있어"),
                      now=_at("2026-11-05T09:00:00+09:00"))
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "예정된 학사일정이 없어요" in text
    assert SCHED_URL in text


def test_기간을_발화에서_읽는다(db):
    r = server.handle(db, "deadline.upcoming", _payload("앞으로 30일 일정"),
                      now=_at("2026-09-01T09:00:00+09:00"))
    assert "30일" in r["template"]["outputs"][0]["listCard"]["header"]["title"]

    r2 = server.handle(db, "deadline.upcoming", _payload("이번 달 학사일정"),
                       now=_at("2026-09-01T09:00:00+09:00"))
    assert "31일" in r2["template"]["outputs"][0]["listCard"]["header"]["title"]


def test_기간_상한이_있다():
    assert server._resolve_days({"days": "9999"}, "") == 90
    assert server._resolve_days({}, "0일 일정") == 1


def test_5개_넘으면_전체보기(db):
    r = server.handle(db, "deadline.upcoming", _payload("앞으로 90일 일정"),
                      now=_at("2026-09-01T09:00:00+09:00"))
    assert kakao.validate(r) == []
    card = r["template"]["outputs"][0]["listCard"]
    assert len(card["items"]) <= 5
    assert any("전체" in b["label"] for b in card.get("buttons", []))


def test_신선도_초과면_추측하지_않는다(db):
    """31일 넘게 크롤이 안 됐으면 값이 있어도 쓰지 않는다."""
    r = server.handle(db, "deadline.upcoming", _payload("곧 뭐 있어"),
                      now=_at("2026-10-15T09:00:00+09:00"))
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "확인하지 못했어요" in text and SCHED_URL in text


def test_관측시각을_노출한다(db):
    r = server.handle(db, "deadline.upcoming", _payload("곧 뭐 있어"),
                      now=_at("2026-09-01T09:00:00+09:00"))
    joined = " ".join(o.get("simpleText", {}).get("text", "")
                      for o in r["template"]["outputs"])
    assert "확인" in joined


# ═══════════════════════════════════════════════════════════════
# B분기 근거 회귀 — 봇테스트에서 발견
# ═══════════════════════════════════════════════════════════════

def _answer(reason: str, hours: list) -> branch.MealAnswer:
    return branch.MealAnswer(
        branch=branch.Branch.B, reason=reason,
        rows=[{"service_status": "closed_temporary", "note": "운영없음",
               "observed_at": "2026-08-10T14:24:00+09:00", "items": [],
               "zone": "한식", "corner": "백반"}],
        observed_at="2026-08-10T14:24:00+09:00", hours=hours)


def _hours(meal: str, o: str, c: str) -> list:
    return [{"meal_type": meal, "open_time": o, "close_time": c, "is_closed": 0,
             "weekday": wd, "observed_at": "2026-08-10T07:00:00+09:00"}
            for wd in (1, 2, 3, 4, 5)]


def test_원천명시_미운영에도_운영시간_근거가_나온다():
    """★ 봇테스트에서 빠져 있던 것.

    물어본 끼니가 닫혔으면 학생의 다음 질문은 '그럼 언제 여나'다.
    """
    a = _answer("closed_observed", _hours("lunch", "11:30", "14:00"))
    r = templates.render_meal(a, facility_name="후생관", date="2026-08-11",
                              meal_type="breakfast", source_url="https://x")
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "운영하지 않아요" in text
    assert "11:30–14:00" in text and "평일" in text


def test_운영시간_관측이_없으면_만들지_않는다():
    """serves=None 인 경우. 억지로 근거를 지어내지 않고 관측 시각만 남긴다."""
    a = _answer("closed_observed", [])
    r = templates.render_meal(a, facility_name="후생관", date="2026-08-11",
                              meal_type="breakfast", source_url="https://x")
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "운영하지 않아요" in text
    assert "운영시간은" not in text
    assert "확인 기준이에요" in text


def test_중복_문구가_안_붙는다():
    a = _answer("closed_observed", _hours("lunch", "11:30", "14:00"))
    r = templates.render_meal(a, facility_name="후생관", date="2026-08-11",
                              meal_type="breakfast", source_url="https://x")
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "(운영없음)" not in text


def test_일정이_없을_때_버튼이_자기_자신으로_안_돌아온다():
    """★ 버튼 전수를 세 겹으로 넓혔더니 무한 루프가 나왔다 (2026-08-14).

    '앞으로 14일 안에 일정이 없어요' 에 '이번 학기 전체' 버튼이 붙었는데
    보내는 말이 '학사일정 전체' 였다. '전체' 를 읽는 데가 없어서
    같은 14일 조회가 다시 돌고, 같은 답과 같은 버튼이 나왔다.

    ★ 버튼이 보내는 말은 우리가 **실제로 읽는 말**이어야 한다.
    """
    r = templates.render_upcoming([], today="2026-08-14", days=14,
                                  source_url="https://x")
    msgs = [q["messageText"] for q in r["template"]["quickReplies"]]
    assert "학사일정 전체" not in msgs
    assert f"학사일정 {templates.MAX_UPCOMING_DAYS}일" in msgs

    # 이미 최대로 넓혔으면 또 넓히자고 하지 않는다 — 그게 루프였다
    r2 = templates.render_upcoming([], today="2026-08-14",
                                   days=templates.MAX_UPCOMING_DAYS,
                                   source_url="https://x")
    msgs2 = [q["messageText"] for q in r2["template"]["quickReplies"]]
    assert not any("학사일정" in m for m in msgs2)


def test_버튼이_보내는_일수를_서버가_읽는다():
    """버튼 문구와 서버 상한이 같은 수여야 한다 — 다르면 버튼이 못 지킬 약속을 한다."""
    from skill import server
    assert server._resolve_days(
        {}, f"학사일정 {templates.MAX_UPCOMING_DAYS}일") == templates.MAX_UPCOMING_DAYS
