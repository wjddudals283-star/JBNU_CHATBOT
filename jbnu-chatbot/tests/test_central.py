"""학과 되묻기가 막다른 길인 주제 — 중앙 문서로 보낸다.

★ 실측 (2026-08-17)
  '연계전공 졸업요건' 에 "'국어국문학과 연계전공 졸업요건'처럼 학과를 붙여서"
  가 나갔다. 연계전공은 **학과 소속이 아니다** — 학생이 뭘 붙여도 답에 못 닿는다.
  되묻기가 막다른 길이 된다.

★ 표를 인용하지는 않는다
  중앙 표는 kind='table' 이라 헤더 경계가 없어 지금 규칙으로 못 그린다.
  못 그려도 **원문까지는 보낼 수 있다.** 막다른 길을 없애는 게 먼저다.
"""

from __future__ import annotations

from skill import central, templates


def test_연계전공은_학과_되묻기를_안_탄다():
    for u in ("연계전공 졸업요건", "융합전공 이수학점", "연계 전공 뭐야"):
        assert central.find(u) is not None, u


def test_공백이_달라도_같은_말이다():
    """'연계 전공' 과 '연계전공' — 붙여 쓴 발화가 폴백으로 가던 것과 같은 이유."""
    a = central.find("연계전공")
    b = central.find("연계 전공")
    assert a is not None and b is not None and a.key == b.key


def test_학과별로_갈리는_주제는_안_넣는다():
    """★ 교직·복수전공·부전공·교양은 실제로 학과별로 갈린다 (사이트 71~109곳)."""
    for u in ("졸업요건", "복수전공 신청", "부전공 이수학점", "교직 이수", "교양 학점"):
        assert central.find(u) is None, u


def test_링크가_없으면_실리지_않는다():
    """'중앙 문서로 보낸다' 는데 링크가 없으면 말이 안 된다."""
    for _words, t in central._topics():
        assert t.url.startswith("http"), t


def test_문서에_무엇이_있는지_말해준다():
    """★ '이 표에 있어요' 만 하면 학생은 눌러야 안다.

    우리가 본 것을 적으면 누를지 정할 수 있다 — 다만 **본 것만** 적는다.
    """
    t = central.find("연계전공 졸업요건")
    r = templates.render_central(t, "연계전공 졸업요건")
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "주관학과" in text and "이수학점" in text
    assert t.url in text
    # 못 그린다는 사실을 숨기지 않는다
    assert "읽기 어려워요" in text


def test_되묻지_않는다():
    """물어봐야 학생이 답할 수 없는 질문이라 되묻지 않는다."""
    t = central.find("연계전공")
    r = templates.render_central(t, "연계전공")
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "붙여서 물어봐" not in text
    assert [q["label"] for q in r["template"]["quickReplies"]] == ["처음으로"]
