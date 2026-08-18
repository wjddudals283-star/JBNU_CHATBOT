"""재는 자가 실제 경로를 타는지 — 자에 자를 붙인다.

★ 같은 병이 두 번 났다. 둘 다 사람이 눈으로 발견했다.
    1차  검수 CSV 가 공지 문항을 안내 검색 결과로 실었다
    2차  리포트가 '수강신청 언제야' 를 검색으로 쟀다 (학생은 학사일정으로 간다)
  세 번째는 이 테스트가 잡는다.
"""

import pytest

from skill import server
from tools import answer_path
from tools.answerability_report import QUESTIONS


def test_갈래를_서버에서_끌어온다():
    """도구가 자기 if 문으로 갈래를 정하면 안 된다."""
    for _t, q, _e, _m in QUESTIONS:
        route, _ = server.route_of(answer_path.payload(q), None)
        assert route  # route_of 가 늘 갈래를 준다


@pytest.mark.parametrize("route,kind", [
    ("info.search", "search"),
    ("notice.search", "notices"),
    ("deadline.upcoming", "calendar"),
])
def test_아는_갈래는_목록에_있다(route, kind):
    for s in (answer_path._AS_SEARCH, answer_path._AS_NOTICES,
              answer_path._AS_CALENDAR):
        if route in s:
            return
    pytest.fail(f"{route} 가 어느 목록에도 없다")


def test_모르는_갈래는_조용히_검색으로_안_떨어진다(tmp_path):
    """★ 이게 핵심이다. 새 경로가 생기면 '못 읽음' 으로 시끄럽게 남아야 한다.
    검색으로 떨어뜨리면 '재고 있다' 는 착각만 남는다."""
    obs = answer_path.Observed(question="x", route="새경로", why="w",
                               kind="못 읽음", result=None)
    assert not obs.readable
