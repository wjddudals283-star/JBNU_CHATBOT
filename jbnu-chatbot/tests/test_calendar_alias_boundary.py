"""학사일정 별칭이 낱말 안쪽에 걸리지 않는다.

★ '시험' 을 별칭에 넣어야 '시험 언제' 가 학사일정으로 간다 —
  학생이 제일 많이 묻는 축인데 검색 되묻기로 샜다.
  그런데 title_keywords 가 [중간시험, 기말시험] 이라, 경계 규칙이 없으면
  '종합시험 언제'(공지 88건에 쓰이는 말)가 **중간시험 완료**로 답한다.
  같은 병을 이미 세 번 봤다 (학자금 대출 · 인공지능 · 등록).
"""

import pytest

from skill import calendar_search as C


@pytest.mark.parametrize("q,want", [
    ("시험 언제", "시험"),
    ("중간고사 언제", "시험"),
    ("시험기간 언제", "시험"),      # 뒤에 붙는 건 우리말에서 자연스럽다
    ("개강 언제", "개강"),
    ("등록금 언제", "등록금"),
    ("졸업식 언제", "학위수여식"),
])
def test_걸려야_하는_말(q, want):
    t = C.find_topic(q)
    assert t is not None and t.key == want


@pytest.mark.parametrize("q", [
    "종합시험 언제",     # 대학원 종합시험 — 학사 시험이 아니다
    "외국어시험 언제",
    "임용시험 언제",
    "자격시험 일정",
    "동아리 등록 기간",   # '등록' 덫도 같은 규칙이 막는다
])
def test_낱말_안쪽에는_안_걸린다(q):
    assert C.find_topic(q) is None


def test_문장_처음도_경계다():
    assert C.find_topic("시험") is not None
