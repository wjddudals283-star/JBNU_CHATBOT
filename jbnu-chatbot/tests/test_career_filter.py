"""끝난 공고 빼기 · 활동 먼저 — 자를 붙인다.

★ 원칙에 검증이 안 붙으면 희망이지 설계가 아니다.
  '제목에 마감이 없으면 안 뺀다' 는 규칙이라, 그걸 어기는 테스트가 있어야
  나중에 누가 정규식을 넓혔을 때 소리가 난다.
"""

import datetime as dt

from skill import career


def _n(title, published="2026-08-10"):
    return {"title": title, "published_at": published, "url": "u"}


def test_제목에_적힌_마감이_지나면_뺀다():
    rows = [_n("한샘 RD 71기 산학채용 모집(~8.9(일)까지)")]
    keep, dropped = career.sort_and_filter(rows, today=dt.date(2026, 8, 18))
    assert keep == []
    assert dropped["마감 지남"] == 1


def test_마감이_안_지났으면_남긴다():
    rows = [_n("2026년도 안전보건공단 하반기 채용 공고(~8/24)")]
    keep, _ = career.sort_and_filter(rows, today=dt.date(2026, 8, 18))
    assert len(keep) == 1


def test_제목에_마감이_없으면_안_뺀다():
    """★ 이게 핵심이다 — 모르는 것을 지났다고 말하지 않는다."""
    rows = [_n("2026학년도 2학기 취업특강 안내", published="2026-01-02")]
    keep, dropped = career.sort_and_filter(rows, today=dt.date(2026, 8, 18))
    assert len(keep) == 1
    assert dropped["마감 지남"] == 0


def test_결과_알리는_글은_뺀다():
    rows = [_n("기간제계약직원 채용 서류전형 합격자 및 면접 일정 안내")]
    keep, dropped = career.sort_and_filter(rows, today=dt.date(2026, 8, 18))
    assert keep == []
    assert dropped["끝난 공고"] == 1


def test_활동이_채용보다_앞에_온다():
    rows = [_n("기간제 근로자 채용 공고", published="2026-08-17"),
            _n("취업특강 안내", published="2026-08-01")]
    keep, _ = career.sort_and_filter(rows, today=dt.date(2026, 8, 18))
    assert [career.is_activity(r["title"]) for r in keep] == [True, False]


def test_같은_묶음_안에서는_최신순():
    rows = [_n("취업특강 안내", published="2026-08-01"),
            _n("공모전 개최", published="2026-08-15")]
    keep, _ = career.sort_and_filter(rows, today=dt.date(2026, 8, 18))
    assert keep[0]["published_at"] == "2026-08-15"


def test_같은_제목은_한_번만():
    rows = [_n("재학생 멘토링 멘토 모집"), _n("재학생 멘토링 멘토 모집")]
    keep, dropped = career.sort_and_filter(rows, today=dt.date(2026, 8, 18))
    assert len(keep) == 1
    assert dropped["같은 제목"] == 1


def test_해를_넘기는_마감():
    """12월에 올린 '1/5 마감' 은 다음 해다 — 마감이 게시일보다 앞설 수 없다."""
    d = career.title_deadline("겨울 인턴 모집(~1/5)", "2025-12-20")
    assert d == dt.date(2026, 1, 5)


def test_날짜가_아닌_것은_None():
    assert career.title_deadline("제2회 공모전(~2/30)", "2026-08-10") is None
    assert career.title_deadline("취업특강 안내", "2026-08-10") is None
