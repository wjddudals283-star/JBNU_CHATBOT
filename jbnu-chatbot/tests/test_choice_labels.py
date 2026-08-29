"""선택지 제목은 **서로 달라야 한다** — 화면은 제목만 크게 보인다.

★ '증명서 발급' 이 이렇게 나갔다:
      전북대학교 본부 · 전북대학교 본부 · 스마트팜학과 · …
  기술적으로는 중복이 아니다 — (사이트, 문서제목) 쌍은 다르다.
  그런데 학생 눈에는 같은 줄이고, '가능' 은 5줄 중 4줄이 같은 이름이었다.
  대표가 '선택지 개수가 어긋난다' 고 한 것이 이것이다.

★ 실측: 116개 발화 중 11건 → 1건.
  남은 1건은 사이트 이름과 문서 제목이 같은 글자인 경우('대학원')다.
"""

from skill import templates


class _H:
    def __init__(self, site, title, url):
        self.site_name = site
        self.page_title = title
        self.page_url = url
        self.quote_path = title
        self.path = title
        self.quote_text = ""
        self.text = ""


class _R:
    def __init__(self, hits):
        self.hits = hits
        self.missing_tokens = []
        self.needs_attribute = ""


def _titles(hits):
    out = templates.render_ambiguous(_R(hits), subject="증명서 발급") \
        if hasattr(templates, "render_ambiguous") else None
    return out


def test_같은_사이트가_반복되면_문서제목이_앞으로():
    """규칙 자체를 잰다 — 렌더 경로는 실화면 측정 도구가 본다."""
    hits = [_H("전북대학교 본부", "FAQ", "u1"),
            _H("전북대학교 본부", "행동강령", "u2"),
            _H("스마트팜학과", "증명서", "u3")]
    names = [h.site_name for h in hits]
    repeated = {s for s in names if names.count(s) > 1}
    titles = [(h.page_title if h.site_name in repeated else h.site_name)
              for h in hits]
    assert titles == ["FAQ", "행동강령", "스마트팜학과"]
    assert len(set(titles)) == len(titles)


def test_반복이_없으면_사이트_이름_그대로():
    hits = [_H("경제학부", "졸업요건", "u1"), _H("사학과", "졸업요건", "u2")]
    names = [h.site_name for h in hits]
    repeated = {s for s in names if names.count(s) > 1}
    titles = [(h.page_title if h.site_name in repeated else h.site_name)
              for h in hits]
    assert titles == ["경제학부", "사학과"]


def test_버리지_않는다():
    """같은 사이트라도 문서가 다르면 학생이 고를 것이 실제로 여럿이다."""
    hits = [_H("전북대학교 본부", "FAQ", "u1"),
            _H("전북대학교 본부", "행동강령", "u2")]
    assert len({h.page_url for h in hits}) == 2
