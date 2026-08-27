"""블록이 와도 발화를 본다 — 다만 **긍정 신호가 있을 때만** 옮긴다.

★ 같은 병이 세 번째였다. 한 번은 사고, 두 번은 부류, 세 번은 구조다.
    아침  검색 블록이 학사일정 질문을 삼켰다 → 검색 블록만 고쳤다
    지금  학식 블록이 46문항 중 43건을 삼킨다 · 학사일정 블록도 43건
  문제는 학식 블록이 아니라 '블록이 오면 발화를 아예 안 본다' 는 것 자체다.

★ 부정 조건을 쓰지 않는다
  '음식 낱말이 없으면 학식이 아니다' 는 '오늘 뭐 나와' 를 학식에서 빼낸다.
"""

import pytest

from skill import server


def _pl(u):
    return {"userRequest": {"utterance": u}, "action": {"params": {}}}


@pytest.mark.parametrize("u", [
    "오늘 학식", "학식", "식단", "후생관", "진수원", "의대식당", "생활관 식당",
    "오늘 뭐 나와",        # ★ 음식 낱말이 없는 진짜 학식 질문
    "후생관 뭐 나와", "진수원 저녁", "후생관 가격", "밥 뭐야", "점심 뭐야",
])
def test_진짜_학식_질문은_학식에서_안_빠진다(u):
    assert server.route_of(_pl(u), "food.menu.today")[0] == "food.menu.today"


@pytest.mark.parametrize("u", [
    "학사일정", "마감", "수강신청 일정", "이번 달 학사일정",
    "개강 언제야", "시험 언제", "휴학 언제까지야",
])
def test_진짜_학사일정_질문은_안_빠진다(u):
    assert server.route_of(_pl(u), "deadline.upcoming")[0] == "deadline.upcoming"


@pytest.mark.parametrize("u,want", [
    ("국가 장학금 신청", "info.search"),      # 별칭이 긍정으로 가리킨다
    ("기계공학과 교수", "info.search"),       # 학과 이름이 들었다
    ("총학 공지", "council.notice"),
])
def test_발화가_다른_갈래를_가리키면_블록을_이긴다(u, want):
    assert server.route_of(_pl(u), "food.menu.today")[0] == want


def test_안전_분기는_블록보다_먼저다():
    assert server.route_of(_pl("죽고싶어"), "food.menu.today")[0] == "safety"


def test_총학이_별칭보다_먼저다():
    """'총학 장학금 공지' 가 '장학금' 에 먹히면 학교 안내가 총학 것처럼 보인다."""
    assert server.route_of(_pl("총학 장학금 공지"), None)[0] == "council.notice"


def test_시간낱말이_별칭보다_구체적이다():
    """'휴학 신청'은 절차라 검색, '휴학 언제까지야'는 날짜라 학사일정."""
    assert server.route_of(_pl("휴학 신청"), None)[0] == "info.search"
    assert server.route_of(_pl("휴학 언제까지야"), None)[0] == "deadline.upcoming"
