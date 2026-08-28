"""학과 이름은 「어디를 볼지」지 「무엇을 볼지」가 아니다.

★ '휴학 항공우주공학과' 가 matched=['항공우주공학'] 로 학습성과를 냈다.
  상위 5개가 전부 학과 이름만 맞고 동점이었다 — 주제어 '휴학' 은 0건.
  범위를 맞춘 것을 답을 맞춘 것으로 세면 안 된다.

★ 임계값이 아니라 뜻이다.
  코퍼스 쪽 효과는 tools/dept_topic_probe.py 가 잰다
  (딴 문서 10건 → 0건 · 오탐 0 · 46문항 영향 0).
"""

from skill import section_search as S


def test_학과이름을_뽑는다():
    assert S._dept_in("휴학 항공우주공학과") == "항공우주공학과"


def test_학과가_없으면_None():
    assert S._dept_in("졸업요건") is None
    assert S._dept_in("오늘 학식") is None


def test_긴_이름이_이긴다():
    """'컴퓨터인공지능학부' 가 짧은 이름보다 먼저 잡혀야 한다."""
    assert S._dept_in("컴퓨터인공지능학부 교육과정") == "컴퓨터인공지능학부"


def test_범위만_맞음_판정():
    """맞은 낱말이 학과 이름의 조각뿐이면 범위만 맞은 것이다."""
    dept = "항공우주공학과"
    assert all(t in dept for t in ["항공우주공학"])          # 범위만
    assert not all(t in dept for t in ["휴학", "항공우주공학"])  # 주제도 맞음


def test_학과_목록은_손으로_안_적는다():
    """sites.yaml 에서 끌어온다 — 학과가 늘면 여기도 같이 는다."""
    names = S._dept_names()
    assert len(names) > 50
    assert list(names) == sorted(names, key=len, reverse=True)
