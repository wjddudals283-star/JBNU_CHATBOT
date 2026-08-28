"""블록이 삼킨 자리에 **출구**를 준다.

★ 카카오가 '오늘 날씨' 를 학식 블록으로 보내면 우리는 못 가른다 —
  '오늘 뭐 나와' 와 토큰이 ['오늘', X] 로 같아서 어휘로는 안 갈린다.
  그런데 학식 화면만 내면 **엉뚱한 답을 확신 있게 준 것**처럼 보인다.
  우리 순서에서 제일 나쁜 자리다.

★ 라우팅과 달리 여기서는 부정 조건을 써도 된다
  답을 안 바꾸고 **줄 하나**만 붙이므로 틀려도 손해가 작다.
  실측: 갇힌 26건 전부가 받고, 진짜 학식 발화 22개 중 3개만 받는다.
"""

import pytest

from skill import server, templates


@pytest.mark.parametrize("u", ["오늘 날씨", "교환학생", "복학 신청", "총장 누구야"])
def test_학식_신호가_없으면_출구를_붙인다(u):
    assert server._off_topic_for("food.menu.today", u)


@pytest.mark.parametrize("u", ["학식", "오늘 학식", "후생관", "진수원 저녁",
                               "의대식당 점심", "식단", "급식"])
def test_학식_신호가_있으면_안_붙인다(u):
    assert not server._off_topic_for("food.menu.today", u)


@pytest.mark.parametrize("u", ["학사일정", "마감", "개강 언제야", "시험 언제",
                               "수강신청 일정"])
def test_학사일정_신호가_있으면_안_붙인다(u):
    assert not server._off_topic_for("deadline.upcoming", u)


def test_출구가_화면에_실제로_나온다():
    out = templates.render_meal_ask(["후생관"], date="2026-08-18", off_topic=True)
    text = out["template"]["outputs"][0]["simpleText"]["text"]
    assert templates.OFF_TOPIC_EXIT in text


def test_평소에는_안_나온다():
    out = templates.render_meal_ask(["후생관"], date="2026-08-18")
    text = out["template"]["outputs"][0]["simpleText"]["text"]
    assert templates.OFF_TOPIC_EXIT not in text


def test_답은_그대로다():
    """출구는 덧붙이는 것이지 답을 바꾸는 게 아니다."""
    a = templates.render_meal_ask(["후생관"], date="2026-08-18")
    b = templates.render_meal_ask(["후생관"], date="2026-08-18", off_topic=True)
    assert a["template"]["quickReplies"] == b["template"]["quickReplies"]
    assert b["template"]["outputs"][0]["simpleText"]["text"].startswith(
        a["template"]["outputs"][0]["simpleText"]["text"])
