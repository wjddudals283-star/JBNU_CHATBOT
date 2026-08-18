"""1등 동점을 무엇으로 깨는가 — 원칙에 자를 붙인다.

★ 답한 38건 중 19건이 1등 동점이었다. 깨는 기준이 없어 SQL 순서가 답이 됐다.
  규칙이 조용히 뒤집히면 19건이 한꺼번에 움직인다. 그래서 자를 붙인다.
"""

from skill import section_search as S


def _hit(title, url, matched):
    return S.Hit(section_key="k", page_url=url, host="h", site_name="s",
                 page_title=title, path=title, text="t", quote_key=None,
                 page_modified=None, observed_at="", score=1.0,
                 page_score=1.0, matched=matched)


def test_군더더기가_적은_제목이_앞선다():
    """'교수' 를 물으면 '교수' 가 '명예교수'·'평생지도교수제' 보다 앞이다."""
    a = _hit("교수", "https://x/a", ["교수"])
    b = _hit("명예교수", "https://x/b", ["교수"])
    c = _hit("평생지도교수제", "https://x/c", ["교수"])
    order = sorted([b, c, a], key=lambda h: S._tiebreak(h, 1))
    assert [h.page_title for h in order] == ["교수", "명예교수", "평생지도교수제"]


def test_낱말을_담은_제목이_못_담은_제목을_이긴다():
    a = _hit("취업진로상담", "https://x/a", ["취업", "상담"])
    b = _hit("학생타운 2층 212호 개별상담실3", "https://x/b", ["상담"])
    assert S._tiebreak(a, 1) < S._tiebreak(b, 1)


def test_제목이_같으면_잎이_많은_문서가_앞선다():
    a = _hit("FAQ", "https://x/a", [])
    b = _hit("FAQ", "https://x/b", [])
    assert S._tiebreak(a, 5) < S._tiebreak(b, 1)


def test_그래도_같으면_상위_페이지가_앞선다():
    a = _hit("안내", "https://x/career/1.do", [])
    b = _hit("안내", "https://x/career/room/212/1.do", [])
    assert S._tiebreak(a, 1) < S._tiebreak(b, 1)


def test_제목에_안_들어간_낱말은_군더더기로_안_센다():
    """맞은 낱말만 뺀다 — 질문 전체를 빼면 긴 제목이 유리해진다."""
    assert S._title_fit("교수", ["기계공학과", "교수"]) == (1, 0)
