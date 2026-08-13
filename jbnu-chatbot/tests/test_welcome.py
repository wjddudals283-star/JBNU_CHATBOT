"""웰컴 블록을 스킬로 — 오픈빌더 정적 카드가 두 번 저장에 실패했다.

폴백을 스킬데이터로 바꾼 경로가 안정적이었으므로 같은 방식으로 간다.
그리고 버튼 문구를 코드에서 관리하면 답이 바뀔 때 같이 고칠 수 있다.
"""

from __future__ import annotations

import pytest

from skill import routing, server, templates
from store import repo


def _pay(utterance: str = "", block: str | None = None) -> dict:
    ur: dict = {"utterance": utterance}
    if block is not None:
        ur["block"] = {"id": "b", "name": block}
    return {"userRequest": ur, "action": {"params": {}}}


def _db(tmp_path):
    p = tmp_path / "w.db"
    c = repo.connect(p)
    repo.init_db(c)
    c.commit()
    c.close()
    return p


def test_빈_발화면_웰컴이다():
    """★ 폴백과 가르는 것은 **발화가 비었는지**다.

    폴백도 블록 정보가 비어서 온다. 웰컴은 거기에 '할 말이 없다' 가 더해진다.
    검색할 것이 없으니 검색으로 보낼 이유도 없다.
    """
    assert routing.is_welcome(_pay(""))
    assert routing.is_welcome(_pay("   "))
    assert not routing.is_welcome(_pay("휴학"))


def test_이름으로도_알아본다():
    for name in ("welcome", "웰컴 블록", "웰컴블록", "시작하기"):
        assert routing.is_welcome(_pay("", name))


@pytest.mark.parametrize("u", ["처음으로", "시작하기"])
def test_처음으로_버튼이_검색으로_새지_않는다(tmp_path, u):
    """★ '처음으로' 는 우리가 **모든 답변에 붙이는 버튼**이다.

    누르면 그 말이 발화로 오는데, 검색으로 보내면
    "'처음'은 학과마다 달라요" 같은 엉뚱한 답이 나간다 — 실제로 그랬다.
    """
    out = server.handle(_db(tmp_path), None, _pay(u))
    assert "총학생회 챗봇" in out["template"]["outputs"][0]["simpleText"]["text"]


def test_메뉴는_다섯_개고_전부_답이_확인된_것이다():
    """★ 메뉴는 약속이다.

    눌렀는데 '못 찾았어요' 가 나오면 그 뒤로 아무것도 안 누른다.
    다섯 개 전부 봇테스트로 답을 확인했다 (2026-08-13).
    '총학 공지' 는 원천이 인스타라 뺐다 — 크롤이 못 닿는다.
    """
    assert templates.WELCOME_MENU == ["오늘 학식", "학사일정", "휴학",
                                      "자퇴 절차", "졸업요건"]
    qr = templates.render_welcome()["template"]["quickReplies"]
    # 보내는 말과 화면 라벨이 같아야 한다 — 줄이면 검색이 달라진다
    assert all(q["label"] == q["messageText"] for q in qr)


def test_카카오_규격을_통과한다():
    from skill import kakao
    assert kakao.validate(templates.render_welcome()) == []


def test_웰컴보다_안전_분기가_먼저다(tmp_path):
    """빈 발화가 아니면 웰컴이 아니고, 위험한 말이면 안전이 먼저다."""
    out = server.handle(_db(tmp_path), None, _pay("죽고싶어"))
    assert "109" in out["template"]["outputs"][0]["simpleText"]["text"]
