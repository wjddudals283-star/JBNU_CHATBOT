"""공지 검색 — '분야 + 공지' 꼴과 사이트 좁히기.

★ 배포본 실측 (2026-08-14)
  '취업 공지' → "'공지' 가 제목에 든 공지를 찾지 못했어요. 6,282건을 확인했어요."
  제목에 '취업' 이 든 공지가 139건인데 **한 건도 못 찾았다.**

  두 가지가 겹쳐 있었다.
    ① '공지' 를 제목에서 찾았다. 그건 분야가 아니라 **요청 종류**다.
    ② '취업' 이 career.jbnu.ac.kr 사이트 별칭에 먹혔다.
       그런데 그 사이트의 공지는 **0건**이다 (페이지는 200섹션 있다).
       있는 공지를 두고 0건짜리 사이트로 좁혀서 못 찾았다.

  ★ '학자금 대출' · '컴퓨터인공지능학부' 에 이어 같은 계열이 세 번째다.
"""

from __future__ import annotations

import pytest

from skill import section_search
from store import repo

BOARD = "https://x/board"


@pytest.fixture()
def conn():
    c = repo.connect(":memory:")
    repo.init_db(c)
    rows = [
        ("n1", "4학년 재학생 대상 취업설명회", "www.jbnu.ac.kr", "교내공지"),
        ("n2", "취업 스킬UP! 프로그램 신청 안내", "www.jbnu.ac.kr", "교내공지"),
        ("n3", "기계공학전문 프로그램 내규 개선", "mech.jbnu.ac.kr", "공지사항"),
    ]
    for key, title, host, board in rows:
        c.execute(
            """INSERT INTO notice_item (item_key, url, title, published_at,
                 board_url, board_name, host, site_name, observed_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (key, f"https://x/{key}", title, "2026-08-13", BOARD, board, host,
             host.split(".")[0], "2026-08-14T09:00:00+09:00"))
        c.execute("""INSERT INTO notice_item_fts (item_key, title, board_name)
                     VALUES (?,?,?)""", (key, title, board))
    c.commit()
    return c


def test_공지는_분야가_아니라_요청_종류다(conn):
    """★ '취업 공지' 는 '취업 분야의 최근 공지' 를 묻는 말이다."""
    r = section_search.search_notices(conn, "취업 공지", repo=repo)
    assert "공지" not in r.query_tokens, r.query_tokens
    assert r.outcome is section_search.Outcome.FOUND
    assert any("취업" in h.title for h in r.hits)


def test_공지만_물으면_그건_목록_요청이다(conn):
    """다른 낱말이 남을 때만 뺀다 — '공지' 하나만 남기면 안 된다."""
    r = section_search.search_notices(conn, "공지", repo=repo)
    assert r.query_tokens == ["공지"]


def test_공지가_없는_사이트로_좁히지_않는다(conn, monkeypatch):
    """★ career.jbnu.ac.kr 은 페이지는 있는데 공지가 0건이다.

    '취업' 이 그 사이트 별칭이라 공지 검색이 그리로 좁혀졌고,
    제목에 '취업' 이 든 공지가 있는데도 한 건도 못 찾았다.
    임계값이 아니라 관측이다 — 그 호스트에 공지가 있나만 본다.
    """
    monkeypatch.setattr(section_search, "match_site",
                        lambda u: ("career.jbnu.ac.kr", "취업진로지원과"))
    r = section_search.search_notices(conn, "취업 공지", repo=repo)
    assert r.outcome is section_search.Outcome.FOUND, r.query_tokens
    assert not r.site_name, "공지가 없는 사이트로 좁히면 안 된다"


def test_공지가_있는_사이트로는_그대로_좁힌다(conn, monkeypatch):
    """학과 좁히기는 살아 있어야 한다 — 그게 원래 맞던 동작이다."""
    monkeypatch.setattr(section_search, "match_site",
                        lambda u: ("mech.jbnu.ac.kr", "기계공학과"))
    r = section_search.search_notices(conn, "기계공학과 공지", repo=repo)
    assert r.site_name == "기계공학과"
    assert all(h.site_name == "mech" for h in r.hits)


def test_notice_total_이_사이트별로_센다(conn):
    assert repo.notice_total(conn) == 3
    assert repo.notice_total(conn, host="www.jbnu.ac.kr") == 2
    assert repo.notice_total(conn, host="career.jbnu.ac.kr") == 0
