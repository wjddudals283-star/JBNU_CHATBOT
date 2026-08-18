"""지난 일정을 지났다고 밝힌다 — 빼지는 않는다.

★ 개강 앞둔 학생이 '2026학년도 제2학기 수강신청 — 7/30' 을 보면
  "내가 놓쳤나" 한다. 맨 위 답은 정확한데 그 아래가 흔들었다.

★ 취업 공지와 **다른 자리**다
  취업은 '지원할 수 있나' 가 전부라 지난 건 뺐다.
  학사일정은 '수강신청 언제였지' 도 유효한 질문이다.
  게다가 수강신청변경·개강은 후보가 2개뿐이라 빼면 관련 일정이 0개가 된다.
"""

import datetime as dt

from skill import templates as T


def _e(start, end=None, title="x"):
    return {"start_date": start, "end_date": end, "title": title}


TODAY = dt.date(2026, 8, 18)


def test_지난_것에_표를_단다():
    assert T._past_tag(_e("2026-07-30"), TODAY) == " (지났어요)"


def test_앞으로_올_것엔_안_단다():
    assert T._past_tag(_e("2026-09-01", "2026-09-07"), TODAY) == ""


def test_진행_중이면_안_단다():
    """끝나는 날이 오늘이면 아직 안 지났다 — 오늘 마감을 지났다고 하면 안 된다."""
    assert T._past_tag(_e("2026-08-10", "2026-08-18"), TODAY) == ""


def test_해가_다르면_해를_밝힌다():
    assert T._period_text(_e("2027-02-01"), TODAY) == "2027년 2/1"


def test_같은_해면_해를_안_붙인다():
    assert T._period_text(_e("2026-09-01", "2026-09-07"), TODAY) == "9/1~9/7"


def test_오늘을_모르면_예전처럼_낸다():
    """today 를 안 주면 해를 안 붙인다 — 기존 호출부가 안 깨진다."""
    assert T._period_text(_e("2027-02-01")) == "2/1"
