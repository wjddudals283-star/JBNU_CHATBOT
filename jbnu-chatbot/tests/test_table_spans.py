"""병합 셀을 펴서 행마다 칸 수를 맞춘다.

★ 왜
  실측: 표 3,024개 중 46.6% 가 행마다 칸 수가 다르다.
  **학과 졸업요건 페이지는 75.8%** — 형식 안내가 학생을 보내는 바로 그 자리다.

      8칸  적용년도 | 학과 | 교양(42이상) | … | 계
      2칸  전필 | 전선                        ← 병합으로 비어 있던 자리
      9칸  2010년이후입학자 | 사학 | 최소이수학점29-42 | …

  '졸업요건은 학과마다 달라요 → 사학과 졸업요건' 이라고 보내 놓고
  도착지를 못 읽게 하는 것이다.

여기 HTML 은 실제 페이지(history.jbnu.ac.kr 졸업요건)의 구조를 그대로 옮긴 것이다.
"""

from __future__ import annotations

from selectolax.parser import HTMLParser

from crawler.parsers.jbnu_subview import _table_rows


def rows(html: str) -> list[list[str]]:
    return _table_rows(HTMLParser(html).css_first("table"))


def test_colspan_을_편다():
    html = """<table>
      <tr><th colspan="2">전공심화</th><th>계</th></tr>
      <tr><td>전필</td><td>전선</td><td>130</td></tr>
    </table>"""
    assert rows(html) == [["전공심화", "전공심화", "계"],
                          ["전필", "전선", "130"]]


def test_rowspan_을_편다():
    html = """<table>
      <tr><th rowspan="2">적용년도</th><th>구분</th></tr>
      <tr><td>전필</td></tr>
    </table>"""
    assert rows(html) == [["적용년도", "구분"], ["적용년도", "전필"]]


def test_실제_졸업요건_표_모양():
    """이게 '사학과 졸업요건' 이 깨져 보이던 구조다."""
    html = """<table>
      <tr>
        <th rowspan="2">적용년도</th><th rowspan="2">학과</th>
        <th rowspan="2">교양(42이상)</th><th colspan="2">전공심화</th>
        <th rowspan="2">계</th>
      </tr>
      <tr><th>전필</th><th>전선</th></tr>
      <tr>
        <td>2010년이후입학자</td><td>사학</td><td>최소이수학점29-42</td>
        <td>0</td><td>42</td><td>130</td>
      </tr>
    </table>"""
    out = rows(html)
    # ★ 칸 수가 전부 같아야 한다 — 이게 안 되면 헤더와 값이 어긋난다
    assert len({len(r) for r in out}) == 1, out
    assert out[0] == ["적용년도", "학과", "교양(42이상)",
                      "전공심화", "전공심화", "계"]
    assert out[1] == ["적용년도", "학과", "교양(42이상)", "전필", "전선", "계"]
    assert out[2][2] == "최소이수학점29-42"
    # 그 값이 '교양(42이상)' 열 아래에 온다 — 학생이 무엇인지 알 수 있다
    assert out[0][2] == "교양(42이상)"


def test_병합된_값을_반복해_채운다():
    """빈칸으로 두면 칸수는 맞아도 그 열이 무엇인지 모른다."""
    html = """<table>
      <tr><th rowspan="3">공통</th><th>a</th></tr>
      <tr><td>b</td></tr>
      <tr><td>c</td></tr>
    </table>"""
    assert [r[0] for r in rows(html)] == ["공통", "공통", "공통"]


def test_이어지는_rowspan_이_끝나면_원래대로():
    html = """<table>
      <tr><th rowspan="2">x</th><th>a</th></tr>
      <tr><td>b</td></tr>
      <tr><td>y</td><td>c</td></tr>
    </table>"""
    assert rows(html) == [["x", "a"], ["x", "b"], ["y", "c"]]


def test_말도_안_되는_span_은_잘라낸다():
    """colspan='99' 같은 표기가 실제로 있다. 그대로 펴면 폭발한다."""
    html = '<table><tr><td colspan="99">x</td></tr><tr><td>y</td></tr></table>'
    assert len(rows(html)[0]) <= 20


def test_망가진_span_값에도_안_죽는다():
    for bad in ('colspan=""', 'colspan="abc"', 'rowspan="-3"', "colspan='0'"):
        html = f"<table><tr><td {bad}>x</td></tr><tr><td>y</td></tr></table>"
        assert rows(html)[0] == ["x"]


def test_병합이_없으면_예전과_같다():
    """대부분의 표는 병합이 없다. 그것들이 안 바뀌어야 한다."""
    html = """<table>
      <tr><th>등급</th><th>평점</th><th>비고</th></tr>
      <tr><td>A+</td><td>4.5</td><td>95 ~ 100</td></tr>
    </table>"""
    assert rows(html) == [["등급", "평점", "비고"], ["A+", "4.5", "95 ~ 100"]]
