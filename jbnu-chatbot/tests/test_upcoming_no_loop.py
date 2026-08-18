"""학사일정 목록 버튼이 자기 자신을 내놓지 않는다.

★ 우리가 준 선택지인데 답이 없으면 고장이다. 자기 자신을 내놓는 건 그보다 나쁘다 —
  학생을 제자리에 묶는다.
★ 같은 고장을 빈 목록 분기에서만 고쳤었다. 목록이 있는 쪽에는 안 붙었다.
"""

import datetime as dt

from skill import templates as T

ROWS = [{"title": "제2학기 개강", "start_date": "2026-09-01", "end_date": None}]
KW = dict(today="2026-08-18", source_url="https://x", observed_at="", stale=False)


def _labels(days):
    out = T.render_upcoming(ROWS, days=days, **KW)
    return [q["label"] for q in out["template"]["quickReplies"]]


def test_14일_화면은_이번_달을_권한다():
    assert "이번 달 전체" in _labels(14)


def test_이번_달_화면은_이번_달을_또_권하지_않는다():
    """★ 이게 루프였다."""
    labels = _labels(T.MONTH_DAYS)
    assert "이번 달 전체" not in labels
    assert f"앞으로 {T.MAX_UPCOMING_DAYS}일" in labels


def test_최대_화면은_더_넓히자고_안_한다():
    labels = _labels(T.MAX_UPCOMING_DAYS)
    assert "이번 달 전체" not in labels
    assert f"앞으로 {T.MAX_UPCOMING_DAYS}일" not in labels


def test_어느_화면에서도_빠져나갈_길이_있다():
    for d in (14, T.MONTH_DAYS, T.MAX_UPCOMING_DAYS):
        assert _labels(d), d


def test_서버와_템플릿이_같은_수를_본다():
    """버튼 문구가 '이번 달'인데 서버가 31을 못 받으면 버튼이 거짓말을 한다."""
    from skill import server
    assert server._resolve_days({}, "이번 달 학사일정") == T.MONTH_DAYS
