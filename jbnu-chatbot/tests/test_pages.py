"""정적 안내 페이지: 파서 · 보일러플레이트 · 커버리지 레지스트리."""

from __future__ import annotations

import pathlib

import pytest

from crawler import boilerplate as bp
from crawler.parsers import jbnu_subview as SV
from crawler.validate import ParseError
from store import repo

SCHEMA = (pathlib.Path(__file__).resolve().parents[1] / "store" / "schema.sql")


def page(body: str, *, survey: bool = True, title: str = "안내") -> str:
    noise = ("""
      <div class="satis">
        <p>만족도조사결과 (참여인원:0명)</p>
        <ul><li>매우만족 0표</li><li>만족 0표</li><li>불만족 0표</li></ul>
      </div>""" if survey else "")
    return f"""<html><body>
      <div id="sp-content"><div class="com-inner-1300">
        <h1 class="com-title-01">{title}</h1>
        {body}{noise}
      </div></div>
      <div class="last">최종수정일 : 2026-07-28</div>
    </body></html>"""


LIST_BLOCK = """
  <div class="com-box-01">
    <h2 class="com-title-02"><p class="title">교내 장학금</p></h2>
    <ul>
      <li>재학생 등록금으로 형성된 대학회계를 재원으로 한다</li>
      <li>금액별 분류
        <ul>
          <li>1종 장학금 : 등록금 전액</li>
          <li>2종 장학금 : 수업료 전액</li>
        </ul>
      </li>
    </ul>
  </div>"""

TABLE_BLOCK = """
  <div class="com-box-01">
    <h2 class="com-title-02"><p class="title">성적평가</p></h2>
    <div class="com-tbl-wrap">
      <table summary="성적 등급표">
        <tr><th>등급</th><th>평점</th><th>비고</th></tr>
        <tr><td>A+</td><td>4.5</td><td>95 ~ 100</td></tr>
        <tr><td>A</td><td>4.0</td><td>90 ~ 94</td></tr>
      </table>
    </div>
  </div>"""


# ── 색인 / 인용 분리 ─────────────────────────────────────────────────────

def test_잎을_찾으면_부모_블록을_인용한다():
    res = SV.parse(page(LIST_BLOCK), page_url="u")
    leaf = next(s for s in res.leaves if "1종 장학금" in s.text)
    quote = res.by_key[leaf.quote_key]

    assert leaf.text == "1종 장학금 : 등록금 전액"          # 색인은 작게
    assert "2종 장학금" in quote.text                       # 인용은 크게 — 형제까지
    assert leaf.path_text.startswith("교내 장학금 > 금액별 분류")


def test_인용_정책을_바꿔도_재크롤이_필요없게_부모를_저장한다():
    res = SV.parse(page(LIST_BLOCK), page_url="u")
    leaf = next(s for s in res.leaves if "1종 장학금" in s.text)
    parent = res.by_key[leaf.parent_key]
    assert parent.parent_key is not None      # 블록까지 사슬이 이어진다
    assert res.by_key[parent.parent_key].kind == "block"


def test_표는_행으로_색인하고_표_전체를_인용한다():
    res = SV.parse(page(TABLE_BLOCK), page_url="u")
    row = next(s for s in res.sections if s.kind == "table_row" and "A+" in s.text)
    table = res.by_key[row.quote_key]

    assert table.kind == "table"
    # 행만 인용하면 머리글이 사라져 숫자만 남는다. 맥락 없는 숫자는 오답이다.
    assert "등급" in table.quote_text and "평점" in table.quote_text
    assert "A | 4.0" in table.quote_text
    assert "\n" in table.quote_text            # 원문 줄 구조를 살린다


def test_표_셀은_문서_순서를_지킨다():
    """css('td,th') 는 문서 순서를 보장하지 않는다 — 예전에 식단표에서 당했다."""
    res = SV.parse(page(TABLE_BLOCK), page_url="u")
    row = next(s for s in res.sections if s.kind == "table_row" and "A+" in s.text)
    assert row.text == "A+ | 4.5 | 95 ~ 100"


def test_요약하지_않고_원문을_그대로_들고_있다():
    res = SV.parse(page(LIST_BLOCK), page_url="u")
    leaf = next(s for s in res.leaves if "1종" in s.text)
    assert leaf.quote_text  # raw_text 가 비면 text 로 떨어진다
    assert "…" not in leaf.quote_text and "요약" not in leaf.quote_text


# ── 403 오탐 회귀 ────────────────────────────────────────────────────────

def test_스크립트_안의_접근권한_문구를_403으로_오해하지_않는다():
    """조건문 속 문구는 관측이 아니다. 107페이지 중 18개를 이렇게 오탐했었다."""
    html = page(LIST_BLOCK).replace(
        "</body>",
        "<script>if(read=='false'){alert('접근권한이 없습니다.');}</script></body>")
    res = SV.parse(html, page_url="u")
    assert res.leaves


def test_본문에_접근거부가_찍혀_있으면_403으로_본다():
    with pytest.raises(ParseError, match="403"):
        SV.parse(page('<div class="com-box-01">접근이 거부되었습니다</div>',
                      survey=False), page_url="u")


# ── 보일러플레이트: 관측으로 가려내고, 근거 없으면 안 지운다 ──────────────

def _pages(n: int, *, survey: bool = True) -> list[str]:
    """본문은 페이지마다 다르고 템플릿은 같다 — 실제 사이트의 성질을 그대로 흉내낸다.

    본문까지 똑같이 만들면 그건 정의상 템플릿이라 파서가 지우는 게 맞다.
    """
    return [page(LIST_BLOCK.replace("교내 장학금", f"장학금{i}")
                 .replace("재학생 등록금으로", f"{i}번 페이지 고유 문장, 재학생 등록금으로")
                 .replace("1종 장학금 : 등록금 전액", f"1종 장학금 : 등록금 전액 ({i}유형)")
                 .replace("2종 장학금 : 수업료 전액", f"2종 장학금 : 수업료 {i}0% 지원"),
                 survey=survey)
            for i in range(n)]


def test_표본이_적으면_아무것도_지우지_않는다():
    frags = [SV.page_fragments(h) for h in _pages(3)]
    rep = bp.detect(frags)
    assert rep.hashes == set()
    assert "판정 보류" in rep.skipped_reason


def test_반복되는_조각을_하드코딩_없이_찾아낸다():
    frags = [SV.page_fragments(h) for h in _pages(10)]
    rep = bp.detect(frags)
    assert rep.hashes
    assert any("만족도조사결과" in d["sample"] for d in rep.detail)


def test_잘라낸_뒤에는_블록_본문에도_노이즈가_없다():
    """섹션 단위로 지우면 블록 텍스트에 남는다. 그래서 파싱 **전에** 자른다."""
    htmls = _pages(10)
    rep = bp.detect([SV.page_fragments(h) for h in htmls])
    res = SV.parse(htmls[0], page_url="u", boilerplate_report=rep)
    assert all("만족도" not in s.text for s in res.sections)
    assert res.leaves                                  # 본문은 살아 있다
    assert any("1종 장학금" in s.text for s in res.leaves)


def test_본문을_통째로_날릴_조각은_임계를_넘어도_남긴다():
    """지우는 쪽이 되돌리기 어렵다."""
    same = [page('<div class="com-box-01"><h2>제목</h2>'
                 '<ul><li>모든 페이지가 완전히 같은 본문을 가진 경우</li></ul></div>',
                 survey=False) for _ in range(10)]
    rep = bp.detect([SV.page_fragments(h) for h in same])
    res = SV.parse(same[0], page_url="u", boilerplate_report=rep)
    assert res.sections
    assert res.pruned["held"] >= 1


def test_경계선_조각을_보고한다():
    """조용히 넘어가면 임계가 언제 틀렸는지 알 수 없다."""
    frags = [{"h_common": "모든 페이지"} for _ in range(10)]
    for i, f in enumerate(frags):
        if i < 2:                       # 임계(2) 의 절반 이상, 임계 미만
            f["h_edge"] = "가끔 나오는 조각"
    rep = bp.detect(frags, ratio=0.4, min_pages=4)
    assert any(d["sample"] == "가끔 나오는 조각" for d in rep.borderline)


# ── 커버리지 레지스트리 ──────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path):
    conn = repo.connect(tmp_path / "t.db")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()
    yield conn
    conn.close()


def _page_row(db, url, **kw):
    repo.upsert_page(db, page_url=url, host="h", path=url,
                     discovered_at="2026-08-11T00:00:00+09:00", **kw)


def test_없다의_갈래를_상태로_구분한다(db):
    _page_row(db, "/a", parse_status="ok", last_success_at="2026-08-11T00:00:00+09:00")
    _page_row(db, "/b", parse_status="empty")
    _page_row(db, "/c", parse_status="parse_error")
    _page_row(db, "/d", parse_status="blocked")
    s = repo.coverage_summary(db)
    assert s["by_status"]["ok"] == 1
    assert s["by_status"]["empty"] == 1
    assert s["by_status"]["parse_error"] == 1
    assert s["by_status"]["blocked"] == 1          # 못 한 게 아니라 안 한 것
    assert s["answerable_ratio"] == 0.25
    assert [g["parse_status"] for g in repo.coverage_gaps(db)][:2] == \
        ["parse_error", "empty"]                   # 고칠 수 있는 것부터


def test_이번에_실패해도_마지막_성공_시각은_남는다(db):
    _page_row(db, "/a", parse_status="ok", last_success_at="2026-08-01T00:00:00+09:00")
    _page_row(db, "/a", parse_status="fetch_error", error_message="HTTP 403")
    row = repo.page_status(db, "/a")
    assert row["parse_status"] == "fetch_error"
    # 이게 없으면 '고장난 것'과 '원래 없는 것'을 못 가른다
    assert row["last_success_at"] == "2026-08-01T00:00:00+09:00"


def test_사라진_섹션은_남지_않는다(db):
    _page_row(db, "/a", parse_status="ok")
    res = SV.parse(page(LIST_BLOCK, survey=False), page_url="/a")
    repo.replace_sections(db, page_url="/a", sections=res.sections,
                          observed_at="2026-08-11T00:00:00+09:00")
    before = db.execute("SELECT COUNT(*) FROM page_section").fetchone()[0]

    smaller = SV.parse(page(TABLE_BLOCK, survey=False), page_url="/a")
    repo.replace_sections(db, page_url="/a", sections=smaller.sections,
                          observed_at="2026-08-11T01:00:00+09:00")
    rows = db.execute("SELECT text FROM page_section").fetchall()
    # 없어진 문장을 인용하는 것은 지어내는 것과 구별되지 않는다
    assert before and all("1종 장학금" not in r[0] for r in rows)


def test_섹션은_출처와_관측시각을_반드시_들고_있다(db):
    _page_row(db, "/a", parse_status="ok")
    res = SV.parse(page(LIST_BLOCK, survey=False), page_url="/a")
    repo.replace_sections(db, page_url="/a", sections=res.sections,
                          observed_at="2026-08-11T00:00:00+09:00",
                          page_last_modified="2026-07-28")
    r = db.execute("SELECT * FROM page_section LIMIT 1").fetchone()
    assert r["source_url"] == "/a"
    assert r["observed_at"].startswith("2026-08-11")
    assert r["page_last_modified"] == "2026-07-28"


def test_알_수_없는_상태는_거부한다(db):
    with pytest.raises(ValueError):
        _page_row(db, "/a", parse_status="아마도_됨")


# ── 섹션 검색 · 인용 ─────────────────────────────────────────────────────

from skill import section_search as ss          # noqa: E402
from skill import templates                     # noqa: E402


def _seed(db, url="/s", body=LIST_BLOCK, title="교내장학금"):
    repo.upsert_page(db, page_url=url, host="www.jbnu.ac.kr", path=url,
                     discovered_at="2026-08-11T00:00:00+09:00",
                     parse_status="ok", title=title, last_modified="2026-07-28")
    res = SV.parse(page(body, survey=False, title=title), page_url=url)
    repo.replace_sections(db, page_url=url, sections=res.sections,
                          observed_at="2026-08-11T00:00:00+09:00",
                          page_last_modified="2026-07-28")
    return res


def test_잎으로_찾고_부모를_인용한다(db):
    _seed(db)
    r = ss.search(db, "1종 장학금 얼마야", repo=repo)
    assert r.outcome is ss.Outcome.FOUND
    assert "1종 장학금" in r.top.text                 # 색인은 잎
    assert "2종 장학금" in r.top.quote_text           # 인용은 부모 블록
    assert r.top.quote_path == "교내 장학금 > 금액별 분류"


def test_표는_행으로_찾고_표_전체를_인용한다(db):
    _seed(db, url="/t", body=TABLE_BLOCK, title="시험성적")
    r = ss.search(db, "A+ 평점", repo=repo)
    assert r.outcome is ss.Outcome.FOUND
    assert "등급" in r.top.quote_text and "A | 4.0" in r.top.quote_text


def test_조사와_어미를_떼어_있는_페이지를_찾는다():
    assert ss.tokenize("자퇴하려면") == ["자퇴"]
    assert ss.tokenize("졸업 학점 몇 학점이야") == ["졸업", "학점"]
    assert "A+" in ss.tokenize("성적 A+ 몇 점")


def test_인사말은_검색어가_아니다():
    """이걸 검색하면 총장 연설문이 나온다 — 실제로 그랬다."""
    assert ss.tokenize("안녕하세요") == []
    assert ss.tokenize("고마워") == []


def test_자료가_없는_것과_찾아도_없는_것을_가른다(db):
    r = ss.search(db, "장학금", repo=repo)
    assert r.outcome is ss.Outcome.NO_DATA        # 아직 안 긁었다
    _seed(db)
    r2 = ss.search(db, "기숙사 통금", repo=repo)
    assert r2.outcome is ss.Outcome.NOT_FOUND     # 조회는 했다
    assert r2.searched_sections > 0


def test_비슷한_후보가_여럿이면_찍지_않는다(db):
    _seed(db, url="/a", title="장학금 안내 A")
    _seed(db, url="/b", title="장학금 안내 B")
    r = ss.search(db, "1종 장학금", repo=repo)
    assert r.outcome is ss.Outcome.AMBIGUOUS
    assert len({h.page_url for h in r.hits}) == 2


def test_답변에_경로와_출처가_들어간다(db):
    _seed(db)
    r = ss.search(db, "1종 장학금", repo=repo)
    text = templates.render_section(r)["template"]["outputs"][0]["simpleText"]["text"]
    assert "교내 장학금 > 금액별 분류" in text     # 질문 대상을 판단할 수 있어야 한다
    assert "/s" in text                            # 원문 링크
    assert "2026-07-28" in text                    # 언제 기준인지


def test_긴_인용은_자르되_잘랐다고_말한다(db):
    long_body = ('<div class="com-box-01"><h2>규정</h2><ul><li>총칙'
                 '<ul>' + "".join(f"<li>제{i}조 " + "가" * 60 + "</li>"
                                  for i in range(20)) + "</ul></li></ul></div>")
    _seed(db, url="/long", body=long_body, title="학칙")
    r = ss.search(db, "제3조 총칙", repo=repo)
    assert r.outcome in (ss.Outcome.FOUND, ss.Outcome.AMBIGUOUS)
    if r.outcome is ss.Outcome.FOUND:
        text = templates.render_section(r)["template"]["outputs"][0]["simpleText"]["text"]
        # 조용히 자르면 잘린 조건이 없는 조건처럼 읽힌다
        assert "뒷부분이 있어요" in text
        assert len(text) <= 1000
