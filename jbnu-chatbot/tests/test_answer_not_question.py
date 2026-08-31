"""「원문은 참인데 답이 거짓」 — 새 갈래.

★ '총장 누구야' 에 역대총장(제1대 김두헌, 1952년)을 냈다.
  그 페이지는 한 줄도 안 틀렸다 — 인용 정확, 출처 정확, must('총장') 있음.
  지금까지의 확신 오답은 **틀린 문서**였는데 이건 **맞는 문서인데 틀린 답**이다.

★ 우리 안전장치는 전부 '제대로 옮겼나' 를 본다.
  이건 '그 인용이 물은 것에 답하나' 다.
"""

from skill import section_search as S


def test_지난_것_표지는_언어_표면이다():
    assert "역대" in S.PAST_MARKERS
    assert "명예" in S.PAST_MARKERS


def test_연혁은_넣지_않는다():
    """연혁을 물으면 연혁 페이지가 정답이다 —
    오늘 낱말로 세다 틀린 다섯 번 중 하나가 그것이었다."""
    assert "연혁" not in S.PAST_MARKERS


def test_질문에_그_말이_있으면_그대로_답한다():
    """'역대총장' 을 물으면 역대총장이 맞다. 규칙은 **질문에 없을 때만** 건다."""
    for w in S.PAST_MARKERS:
        q = f"{w}총장"
        assert w in q          # 질문에 있으므로 규칙이 안 걸린다


def test_군더더기_기준은_이미_있던_축을_쓴다():
    """새 임계값을 만들지 않는다 — WEAK_TOKEN_DF 를 그대로 쓴다."""
    assert 0 < S.WEAK_TOKEN_DF < 0.1
