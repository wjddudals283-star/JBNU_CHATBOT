"""학사일정 특정 조회 — "수강신청 언제야".

★ 버그였던 것: 데이터가 있는데 "앞으로 14일 안에 예정된 학사일정이 없어요" 가 나왔다.
  한 블록에 목록 조회와 특정 조회가 섞여 들어오는데 서버가 안 갈랐다.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from crawler.parsers import jbnu_academic_schedule as sched
from skill import calendar_search as cs
from skill import kakao, server
from store import repo

FIX = pathlib.Path(__file__).parent / "fixtures"
Y2026 = (FIX / "schedule_2026_2.html").read_text(encoding="utf-8", errors="replace")
KST = dt.timezone(dt.timedelta(hours=9))
SCHED_URL = "https://www.jbnu.ac.kr/web/academic/schedule.do"


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "c.db"
    c = repo.connect(path)
    repo.init_db(c)
    c.execute("""INSERT INTO source_snapshot
                   (id,source_key,url,fetched_at,content_hash,content_path,media_type)
                 VALUES ('s','jbnu_academic_schedule',?,'2026-08-31T05:30:00+09:00',
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


def _payload(u: str):
    return {"userRequest": {"utterance": u, "user": {"id": "u"},
                            "block": {"id": "", "name": "학사일정"}},
            "action": {"params": {}, "detailParams": {}}}


def _at(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso)


# ═══════════════════════════════════════════════════════════════
# 주제 인식
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("utterance,key", [
    ("수강신청 언제야", "수강신청"),
    ("수강 신청 언제", "수강신청"),
    ("개강 언제야", "개강"),
    ("종강 언제", "종강"),
    ("등록금 납부 언제까지", "등록금"),
    ("휴학 신청 기간", "휴학"),
    ("계절학기 언제", "계절학기"),
    ("졸업식 언제야", "학위수여식"),
])
def test_발화에서_주제를_찾는다(utterance, key):
    t = cs.find_topic(utterance)
    assert t is not None and t.key == key


def test_긴_별칭이_먼저_걸린다():
    """'수강신청 변경'이 '수강신청'보다 먼저여야 한다."""
    assert cs.find_topic("수강정정 언제야").key == "수강신청변경"
    pairs = cs.topic_pairs()
    lens = [len(a) for a, _ in pairs]
    assert lens == sorted(lens, reverse=True)


def test_목록_질문은_주제로_안_걸린다():
    """'마감 뭐 있어'는 특정 조회가 아니다 → 목록으로 가야 한다."""
    for u in ("마감 임박한 거", "학사일정 알려줘", "곧 뭐 있어", "이번 달 일정"):
        assert cs.find_topic(u) is None, u


# ═══════════════════════════════════════════════════════════════
# ★ 못 찾음 / 자료 없음 구분
# ═══════════════════════════════════════════════════════════════

def test_자료가_없으면_NO_DATA():
    r = cs.search([], "수강신청 언제야")
    assert r.outcome is cs.Outcome.NO_DATA and r.searched_total == 0


def test_조회했는데_없으면_NOT_FOUND():
    """★ '안 찾아봤다'와 '찾아봤는데 없다'는 다르다."""
    entries = [{"title": "개교기념일(휴업일)", "start_date": "2026-10-15",
                "end_date": None}]
    r = cs.search(entries, "수강신청 언제야")
    assert r.outcome is cs.Outcome.NOT_FOUND
    assert r.searched_total == 1, "몇 건을 훑었는지가 조회했다는 증거다"


def test_찾으면_FOUND():
    entries = [{"title": "제2학기 수강신청 변경(추가) 기간",
                "start_date": "2026-09-01", "end_date": "2026-09-07"}]
    r = cs.search(entries, "수강신청 언제야")
    assert r.outcome is cs.Outcome.FOUND and len(r.entries) == 1


def test_다가오는_것이_먼저_지난_것도_남는다():
    """지난 일정을 감추면 '언제였지'를 확인할 방법이 없다."""
    entries = [
        {"title": "지난 수강신청", "start_date": "2026-07-30", "end_date": None},
        {"title": "다음 수강신청", "start_date": "2026-09-01", "end_date": "2026-09-07"},
    ]
    ranked = cs.rank(entries, "2026-08-11")
    assert ranked[0]["title"] == "다음 수강신청"
    assert ranked[1]["title"] == "지난 수강신청"


# ═══════════════════════════════════════════════════════════════
# 서버 통합 — 버그 재현
# ═══════════════════════════════════════════════════════════════

def test_수강신청_언제야가_답을_준다(db):
    """★ 이게 '없어요' 로 나오던 버그다."""
    r = server.handle(db, None, _payload("수강신청 언제야"),
                      now=_at("2026-09-01T09:00:00+09:00"))
    assert kakao.validate(r) == []
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "9/1~9/7" in text
    assert "예정된 학사일정이 없어요" not in text


def test_답변에_질문_대상이_들어간다(db):
    """★ 답에 질문 대상이 없으면 학생이 '내 질문에 답한 게 맞나'를 못 판단한다."""
    r = server.handle(db, None, _payload("수강신청 언제야"),
                      now=_at("2026-09-01T09:00:00+09:00"))
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert text.startswith("수강신청")


def test_못_찾았을_때도_질문_대상과_조회_사실을_밝힌다(db):
    # 2026-2 학기 일정에 '휴학' 항목은 없다 (실측 22건에 미포함)
    r = server.handle(db, None, _payload("휴학 언제까지야"),
                      now=_at("2026-09-01T09:00:00+09:00"))
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "휴학" in text
    assert "찾지 못했어요" in text
    assert "건을 확인했어요" in text, "조회했다는 사실을 밝혀야 한다"
    assert SCHED_URL in text


def test_휴학_신청_기간이_수강신청으로_안_간다():
    """★ '신청기간' 같은 넓은 별칭을 넣으면 오분류가 난다. 테스트가 잡았다."""
    assert cs.find_topic("휴학 신청 기간").key == "휴학"
    assert cs.find_topic("복학 신청 언제").key == "복학"


def test_목록_질문은_여전히_목록으로(db):
    r = server.handle(db, None, _payload("곧 뭐 있어"),
                      now=_at("2026-09-01T09:00:00+09:00"))
    assert "listCard" in r["template"]["outputs"][0]


def test_지난_일정도_찾아준다(db):
    """'수강신청 언제였지'도 유효한 질문이다."""
    r = server.handle(db, None, _payload("수강신청 언제야"),
                      now=_at("2026-10-01T09:00:00+09:00"))
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "수강신청" in text and "찾지 못했어요" not in text


def test_넓게_조회한다(db):
    """14일치만 보면 있는 걸 없다고 하게 된다."""
    assert server.ITEM_SEARCH_BACK_DAYS >= 180
    assert server.ITEM_SEARCH_AHEAD_DAYS >= 180


def test_신선도_초과면_추측하지_않는다(db):
    r = server.handle(db, None, _payload("수강신청 언제야"),
                      now=_at("2026-11-01T09:00:00+09:00"))
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "확인하지 못했어요" in text and "수강신청" in text


def test_자료가_아예_없으면_다른_문안(tmp_path):
    """'그 항목이 없다'와 '조회할 자료가 없다'는 다른 말이다."""
    path = tmp_path / "empty.db"
    c = repo.connect(path)
    repo.init_db(c)
    c.close()
    r = server.handle(path, None, _payload("수강신청 언제야"),
                      now=_at("2026-09-01T09:00:00+09:00"))
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "수강신청" in text
    assert "아직 가져오지 못했어요" in text


def test_학사일정에_없는_건_공지_확인을_안내한다(db):
    """★ 실측: 정식 수강신청(8/18~21)이 학사일정에 없다. 교내 공지에만 있다.

    원천이 불완전하다는 걸 알면서 그 사실을 숨기면 안 된다.
    """
    r = server.handle(db, None, _payload("수강신청 언제야"),
                      now=_at("2026-09-01T09:00:00+09:00"))
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "공지" in text
