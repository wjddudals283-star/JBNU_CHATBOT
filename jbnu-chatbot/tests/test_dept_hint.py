"""학과가 목록에 없을 때 — **붙여 쓴 형태**를 알려준다.

★ '어느 학과인지 알려주시면' 이 3턴에서 죽었다.
  학생은 '경제학부' 라고만 답하고, 서버는 상태가 없으니 새 질문이 된다.
  되묻기 3턴 13건 **전부** 주제가 증발했다.

★ 예시 학과를 **지어내지 않는다** — 두 번 틀리고 자리표시자로 갔다.
  1차 items 의 title 에서 골랐다 (그건 페이지 제목일 수 있다)
  2차 site_name 에서 골랐다 (후보에 있다 ≠ 그 학과가 그 문서를 가졌다)
  제안한 형태를 그대로 쳤는데 안 되면 안내가 거짓말이다.
"""

from skill import templates as T


class _H:
    def __init__(self, site_name):
        self.site_name = site_name


def test_후보에_학과가_있으면_자리표시자를_준다():
    assert T._dept_example([_H("전북대학교 본부"), _H("생명과학과")]) == "○○학과"


def test_학부도_학과로_본다():
    assert T._dept_example([_H("경제학부")]) == "○○학과"


def test_학과가_없으면_빈_문자열():
    """본부·센터만 있으면 안내 자체를 안 붙인다."""
    assert T._dept_example([_H("전북대학교 본부"), _H("도서관")]) == ""
    assert T._dept_example([]) == ""
    assert T._dept_example(None) == ""


def test_구체_학과_이름을_쓰지_않는다():
    """★ 지어낸 학과가 그 문서를 가졌다는 보장이 없다.
    '영어영문학과 수강신청 정정' 을 제안했다가 또 되물었다."""
    out = T._dept_example([_H("영어영문학과")])
    assert "영어영문" not in out
