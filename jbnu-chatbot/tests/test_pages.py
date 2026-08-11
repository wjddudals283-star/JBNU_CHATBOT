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


def _seed(db, url="/s", body=LIST_BLOCK, title="교내장학금",
          host="www.jbnu.ac.kr"):
    repo.upsert_page(db, page_url=url, host=host, path=url,
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
    r = ss.search(db, "A+ 등급", repo=repo)
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


def test_학과가_갈리면_찍지_않는다(db):
    """위험한 것은 섹션을 잘못 고르는 게 아니라 **학과를 잘못 고르는 것**이다.

    다른 학과의 규정을 내밀면 학생이 그 사실을 알 방법이 없다.
    같은 사이트 안이면 링크로 확인할 수 있으니 답한다.
    """
    _seed(db, url="/a", title="장학금 안내 A", host="me.jbnu.ac.kr")
    _seed(db, url="/b", title="장학금 안내 B", host="ee.jbnu.ac.kr")
    r = ss.search(db, "1종 장학금", repo=repo)
    assert r.outcome is ss.Outcome.AMBIGUOUS
    assert len({h.host for h in r.hits}) == 2


def test_같은_사이트_안에서는_답한다(db):
    _seed(db, url="/a", title="장학금 안내 A", host="me.jbnu.ac.kr")
    _seed(db, url="/b", title="장학금 안내 B", host="me.jbnu.ac.kr")
    r = ss.search(db, "1종 장학금", repo=repo)
    assert r.outcome is ss.Outcome.FOUND


def test_질문의_낱말을_못_찾으면_단정하지_않는다(db):
    """긍정 단정에는 높은 근거를 요구한다.

    '기숙사 통금' 에서 '통금' 을 놓치고 학술교류 협정문을 답으로 준 적이 있다.
    """
    _seed(db, url="/a", title="장학금 안내")
    r = ss.search(db, "장학금 통금시간", repo=repo)
    # 보류든 '못 찾음' 이든 **답하지 않는 것**이 핵심이다.
    # 후보를 드문 낱말로 뽑게 된 뒤로는 '통금시간' 으로 뽑아 0건이 나온다 —
    # 더 정확한 판정이다.
    assert r.outcome is not ss.Outcome.FOUND


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


# ── 전문 검색 색인 ───────────────────────────────────────────────────────

def test_한글_낱말_안쪽도_찾는다(db):
    """'교내장학금만' 안의 '장학금' — 조사가 붙어 한 낱말이 되는 언어라 필요하다."""
    _seed(db, url="/f", body='<div class="com-box-01"><h2>안내</h2><ul>'
          '<li>교내장학금만 신청할 수 있습니다</li>'
          '<li>다른 항목</li></ul></div>', title="장학")
    assert repo.has_fts(db)
    r = ss.search(db, "장학금 신청", repo=repo)
    assert r.outcome is ss.Outcome.FOUND
    assert "교내장학금만" in r.top.text


def test_짧은_토큰은_색인이_못_잡아도_답을_놓치지_않는다(db):
    """trigram 은 3글자 미만을 매칭하지 못한다. 그때는 LIKE 로 떨어져야 한다.

    색인의 한계 때문에 있는 답을 없다고 하면, 그건 색인이 아니라 거짓말이다.
    """
    _seed(db, url="/g", body='<div class="com-box-01"><h2>학적</h2><ul>'
          '<li>휴학 신청은 학기 개시일 전에 하여야 한다</li>'
          '<li>다른 항목</li></ul></div>', title="학적")
    r = ss.search(db, "휴학", repo=repo)
    assert r.outcome in (ss.Outcome.FOUND, ss.Outcome.AMBIGUOUS)
    assert any("휴학" in h.text for h in r.hits)


def test_사라진_섹션은_검색_색인에서도_빠진다(db):
    """본문에서 지웠는데 색인에 남으면 없는 문장을 인용하게 된다."""
    _seed(db, url="/h")
    assert db.execute("SELECT COUNT(*) FROM page_section_fts").fetchone()[0] > 0
    _seed(db, url="/h", body=TABLE_BLOCK, title="시험성적")
    left = db.execute(
        "SELECT COUNT(*) FROM page_section_fts WHERE text LIKE '%1종 장학금%'"
    ).fetchone()[0]
    assert left == 0


# ── 확신의 문턱 ─────────────────────────────────────────────────────────
#
# 놓치면 학생이 다른 데를 찾는다. 틀리면 잘못된 곳으로 간다.
# 둘을 같은 무게로 재지 않는다.

def test_개인_기록은_검색으로_답하지_않는다(db):
    """'내 성적' 에 학칙을 인용하면 학생은 자기 성적을 물었는데 규정을 받는다."""
    _seed(db)
    for q in ["내 성적 알려줘", "제 장학금 얼마 받아요", "본인 수강신청 내역"]:
        r = ss.search(db, q, repo=repo)
        assert r.outcome is ss.Outcome.PERSONAL, q
    # 제도를 묻는 것은 답한다
    assert ss.search(db, "1종 장학금", repo=repo).outcome is not ss.Outcome.PERSONAL


def test_핵심_낱말만_필수다(db):
    """'재수강 규정' 의 뜻은 '재수강' 에 있지 '규정' 에 있지 않다.

    흔한 낱말까지 다 요구하면 동의어를 쓰는 정답이 전부 죽는다.
    """
    _seed(db, url="/r", title="교과 재이수",
          body='<div class="com-box-01"><h2>1종 장학금 안내</h2><ul>'
               '<li>1종 장학금은 등록금 전액을 지원한다</li></ul></div>')
    r = ss.search(db, "1종 장학금 방법", repo=repo)
    # '방법' 은 문서에 없지만 핵심('1종')이 맞으므로 답한다
    assert r.outcome is ss.Outcome.FOUND


def test_본문에서_스친_낱말로는_단정하지_않는다(db):
    """'기숙사' 가 학술교류 표 안쪽 셀에 있다고 그 표가 기숙사 안내는 아니다."""
    # 첫머리는 협정대학 이야기고, 자전거는 한참 뒤에 한 번 스친다
    body = ('<div class="com-box-01"><h2>국내대학교간 학술교류 협정대학</h2><ul>'
            '<li>협정대학: 강원대, 경북대, 경상국립대, 고려대, 공주대, 군산대, '
            '동국대, 목포대, 부산대, 서울대, 서울시립대, 순천대, 전남대, 제주대, '
            '충남대, 충북대, 한국교원대와 학점교류 협정을 맺고 있으며, 교류 기간과 '
            '신청 절차 및 학점 인정 범위는 매 학기 학사과 공지를 따른다</li>'
            '<li>파견 학생의 자전거는 본인이 보관</li></ul></div>')
    _seed(db, url="/x", title="학술교류", body=body)
    r = ss.search(db, "자전거 보관", repo=repo)
    assert r.outcome is not ss.Outcome.FOUND
    assert "제목·첫머리에 없음" in r.defer_reason


def test_메뉴_라벨은_답이_아니다():
    """'증명서발급' 한 낱말이 목차에서 1등이 됐던 적이 있다."""
    assert ss.is_label("증명서발급")
    assert ss.is_label("휴학")
    assert not ss.is_label("1종 장학금 : 등록금 전액")
    assert not ss.is_label("A+ | 4.5 | 95 ~ 100")


def test_후보는_드문_낱말로_뽑는다(db):
    """'복학 신청' 에서 '신청' 으로 후보를 뽑으면 목록이 넘쳐 정답이 잘린다.

    실제로 '휴학 / 복학' 페이지에 '복학' 이 든 잎이 22개인데
    후보 600개에 0개였다. 흔한 낱말은 후보를 넓히기만 하고 변별력이 없다.
    """
    # 흔한 낱말('안내')이 든 페이지를 여럿, 드문 낱말('복학')은 한 곳에만
    for i in range(6):
        _seed(db, url=f"/c{i}", title=f"안내{i}",
              body=('<div class="com-box-01"><h2>안내</h2><ul>'
                    f'<li>{i}번 안내 문서입니다. 신청 안내를 담고 있습니다</li>'
                    '</ul></div>'), host="www.jbnu.ac.kr")
    _seed(db, url="/target", title="휴학 / 복학",
          body=('<div class="com-box-01"><h2>복학</h2><ul>'
                '<li>복학 신청은 매 학기 개강 6주 전부터 가능합니다</li>'
                '</ul></div>'), host="www.jbnu.ac.kr")
    r = ss.search(db, "복학 신청", repo=repo)
    assert r.top is not None
    assert "복학" in r.top.page_title, "드문 낱말로 뽑았으면 정답이 후보에 든다"


def test_후보가_잘리면_그_사실을_남긴다(db):
    """후보 절단이 두 번 터졌는데 두 번 다 **오답이 나온 뒤에야** 알았다.

    잘렸다는 사실이 어디에도 안 남았기 때문이다.
    상한을 얼마로 할지 추측하지 말고 천장에 몇 번 닿는지 센다.
    """
    for i in range(6):
        _seed(db, url=f"/t{i}", title=f"장학 안내 {i}",
              body=('<div class="com-box-01"><h2>장학</h2><ul>'
                    f'<li>{i}번 장학금 신청 안내 문서</li></ul></div>'))
    stats: dict = {}
    rows = repo.search_sections(db, ["장학금"], limit=2, stats=stats)
    assert len(rows) == 2
    assert stats["truncated"] is True
    assert stats["matched"] > stats["returned"], "몇 개가 잘렸는지 알 수 있어야 한다"


def test_안_잘렸으면_잘렸다고_하지_않는다(db):
    _seed(db)
    stats: dict = {}
    repo.search_sections(db, ["1종"], limit=600, stats=stats)
    assert stats["truncated"] is False
    assert "matched" not in stats, "안 잘렸으면 굳이 세지 않는다 (비용)"


def test_진행은_묶음_경계가_아니라_일정_간격으로_찍는다():
    """묶음 끝에서만 찍으면 묶음이 느려질 때 그만큼 침묵한다.

    실제로 4000/6985 이후 59분간 진행 로그가 0건이었고,
    살아 있는지 멈췄는지 구별할 수 없었다.
    스케줄러에서 고쳤던 것과 같은 문제가 다른 자리에서 났다.
    """
    from crawler import pages_run
    assert pages_run.PROGRESS_EVERY < pages_run.CHUNK_SIZE, (
        "간격이 묶음보다 크면 묶음 경계에서만 찍히는 것과 같다")
    src = pathlib.Path(pages_run.__file__).read_text(encoding="utf-8")
    # 진행 로그가 fetch 루프 **안**에 있어야 한다
    loop = src.split("for j, r in enumerate(rows, 1):", 1)[1].split("\n\n", 1)[0]
    assert "PROGRESS_EVERY" in loop
    assert "s/page" in loop, "느려지면 숫자가 먼저 말해줘야 한다"
