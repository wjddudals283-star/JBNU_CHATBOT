"""공지 게시판 — 제목·게시일·링크·게시판만. 구조화하지 않는다."""

from __future__ import annotations

import pathlib

import pytest

from crawler.parsers import notice_list as NL
from skill import section_search as ss
from skill import templates
from store import repo

SCHEMA = pathlib.Path(__file__).resolve().parents[1] / "store" / "schema.sql"

DEPT_BOARD = """<html><body>
  <h2 class="com-title-02"><p class="title">학사 공지</p></h2>
  <table class="artclTable">
    <tr><th>번호</th><th>제목</th><th>작성자</th><th>작성일</th><th>조회수</th></tr>
    <tr><td>9</td>
        <td><a class="artclLinkView" href="/bbs/agct/3670/363965/artclView.do">
            2025년 2학기 국제 학술대회 지원 신청서 제출 안내</a></td>
        <td>관리자</td><td>2025.07.29</td><td>155</td></tr>
    <tr><td>8</td>
        <td><a class="artclLinkView" href="/bbs/agct/3670/251363/artclView.do">
            논문게재료 지원신청서</a></td>
        <td>관리자</td><td>2021.10.28</td><td>529</td></tr>
  </table></body></html>"""

HQ_BOARD = """<html><body>
  <h1 class="com-title-01">공모 / 스터디</h1>
  <div class="com-brd-list-01"><table>
    <tr><th>번호</th><th>분류</th><th>제목</th><th>작성자</th></tr>
    <tr><td>339</td><td>공모</td>
        <td><a href="javascript:;" onclick="pf_DetailMove('216090')">
            2026 섬진강국제실험예술제 자원활동가 모집</a> 2026-08-11</td>
        <td>서서희</td></tr>
  </table></div></body></html>"""

EMPTY_BOARD = """<html><body>
  <table class="artclTable">
    <tr><th>번호</th><th>제목</th></tr>
    <tr><td colspan="2">게시물이(가) 없습니다.</td></tr>
  </table></body></html>"""


def test_학과_게시판을_읽는다():
    r = NL.parse(DEPT_BOARD, page_url="https://agct.jbnu.ac.kr/agct/1/subview.do")
    assert r.board_name == "학사 공지"
    assert len(r.items) == 2
    it = r.items[0]
    assert it.published_at == "2025-07-29"
    assert it.url == "https://agct.jbnu.ac.kr/bbs/agct/3670/363965/artclView.do"
    assert "국제 학술대회" in it.title


def test_본부_게시판은_onclick_번호로_주소를_만든다():
    """href 가 javascript:; 라 목록만으로는 링크가 없다.

    같은 페이지에 실제 href 로도 들어 있어 규칙을 확인했다 —
    확인 없이 주소를 만들면 그건 지어내는 것이다.
    """
    r = NL.parse(HQ_BOARD, page_url="https://www.jbnu.ac.kr/web/unvrslife/square/sub01.do")
    assert len(r.items) == 1
    it = r.items[0]
    assert it.url == "https://www.jbnu.ac.kr/web/Board/216090/detailView.do"
    assert it.published_at == "2026-08-11"
    assert it.category == "공모"        # 목록에 있는 분류만 쓴다


def test_빈_게시판은_글이_없다고_본다():
    r = NL.parse(EMPTY_BOARD, page_url="u")
    assert r.items == []


def test_날짜가_없으면_지어내지_않는다():
    html = DEPT_BOARD.replace("2025.07.29", "").replace("2021.10.28", "")
    r = NL.parse(html, page_url="https://x.jbnu.ac.kr/a")
    assert all(i.published_at is None for i in r.items)


def test_게시판인지_구조로_판단한다():
    assert NL.is_board_page(DEPT_BOARD)
    assert NL.is_board_page(HQ_BOARD)
    assert not NL.is_board_page("<html><body><p>본문만 있는 페이지</p></body></html>")


@pytest.fixture()
def db(tmp_path):
    conn = repo.connect(tmp_path / "n.db")
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()
    yield conn
    conn.close()


def _seed(db, html=DEPT_BOARD, board="https://agct.jbnu.ac.kr/agct/1/subview.do"):
    r = NL.parse(html, page_url=board)
    return repo.replace_notices(db, board_url=board, items=r.items,
                                host="agct.jbnu.ac.kr", board_name=r.board_name,
                                site_name="농생명", observed_at="2026-08-11T00:00:00+09:00")


def test_게시판_목록은_통째로_갈아끼운다(db):
    """게시판은 페이지가 넘어가면 글이 밀려난다.

    부분 갱신을 하면 이미 사라진 글이 계속 남아 유령 링크가 된다.
    """
    assert _seed(db) == 2
    html = DEPT_BOARD.replace("논문게재료 지원신청서", "")
    _seed(db, html=html)
    rows = db.execute("SELECT title FROM notice_item").fetchall()
    assert all("논문게재료" not in r[0] for r in rows)


def test_제목에_없으면_찾았다고_하지_않는다(db):
    _seed(db)
    r = ss.search_notices(db, "기숙사 모집", repo=repo)
    assert r.outcome is ss.Outcome.NOT_FOUND
    assert r.searched_total == 2


def test_제목이_맞으면_링크와_날짜를_준다(db):
    _seed(db)
    r = ss.search_notices(db, "학술대회 지원", repo=repo)
    assert r.outcome is ss.Outcome.FOUND
    out = templates.render_notices(r, utterance="학술대회 지원")
    card = out["template"]["outputs"][0]["listCard"]
    assert "2025-07-29" in card["items"][0]["description"]
    assert card["items"][0]["link"]["web"].endswith("artclView.do")
    # 본문을 안 읽었으므로 내용을 아는 척하지 않는다
    text = out["template"]["outputs"][1]["simpleText"]["text"]
    assert "제목만 보고" in text


def test_자료가_없는_것과_못_찾은_것을_가른다(db):
    r = ss.search_notices(db, "장학금", repo=repo)
    assert r.outcome is ss.Outcome.NO_DATA
    _seed(db)
    r2 = ss.search_notices(db, "장학금", repo=repo)
    assert r2.outcome is ss.Outcome.NOT_FOUND
