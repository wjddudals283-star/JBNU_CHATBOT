"""검색 블록으로 와도 날짜를 묻는 말은 학사일정이 답한다.

★ 메뉴 첫 칸이 8/14부터 답을 못 내던 자리다.
  카카오가 '수강신청 언제야' 를 info.search 로 분류하면 서버가 블록을
  그대로 따랐고, 학생은 '학기당 수강신청학점 하한선' 을 고르라는 되묻기를 받았다.
  근거는 자료 구조다 — 날짜는 page_section 에 없다.
"""

from skill import server


def _pl(u):
    return {"userRequest": {"utterance": u}, "action": {"params": {}}}


def test_검색블록이어도_시간질문은_학사일정으로():
    for u in ("수강신청 언제야", "개강 언제야", "방학 언제", "종강 며칠"):
        route, _ = server.route_of(_pl(u), "info.search")
        assert route == "deadline.upcoming", u


def test_메뉴_버튼_라벨_그대로도_간다():
    """★ '수강신청 일정' 은 **우리가 만든 버튼 문구**다.
    이게 시간 질문으로 안 잡히면 우리가 붙인 버튼이 우리 코드에 안 걸린다."""
    route, _ = server.route_of(_pl("수강신청 일정"), "info.search")
    assert route == "deadline.upcoming"


def test_시간어가_없으면_검색_그대로():
    for u in ("수강신청 학점 상한", "휴학 신청", "졸업요건", "등록금 분할납부"):
        route, _ = server.route_of(_pl(u), "info.search")
        assert route == "info.search", u


def test_학사일정_주제가_아니면_검색_그대로():
    """시간어만으로는 안 넘긴다 — 주제 사전에 있어야 한다."""
    route, _ = server.route_of(_pl("동아리 등록 기간"), "info.search")
    assert route == "info.search"


def test_안전_분기가_먼저다():
    route, _ = server.route_of(_pl("죽고싶어"), "info.search")
    assert route == "safety"


def test_다른_블록은_안_건드린다():
    route, _ = server.route_of(_pl("오늘 학식"), "food.menu.today")
    assert route == "food.menu.today"
