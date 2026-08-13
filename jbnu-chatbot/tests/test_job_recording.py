"""작업이 성공을 기록해야 따라잡기가 멈춘다.

★ 실측한 증상 (Render 로그)
    interval=900s · JOB notices done {'items': 6428} 이 **15분마다 같은 숫자로 반복**
  15분 걸리는 전량 재수집을 15분마다 돌려서 거의 쉬지 않았다.

★ 원인
  due_sources 의 따라잡기는 '예정 시각이 지났는데 오늘 성공 기록이 없으면' 실행한다.
  notices 는 crawl_run 을 안 남겨서 succeeded_today 가 영원히 False 였다.
  pages_run 은 자기가 남겨서 하루 한 번만 돈다 —
  **같은 스케줄러가 도는데 한쪽만 기록을 남기고 있었다.**
"""

from __future__ import annotations

import datetime as dt

from crawler import jobs, schedule as sched
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 12, 13, 5, tzinfo=KST)


def _db(tmp_path):
    p = tmp_path / "j.db"
    c = repo.connect(p)
    repo.init_db(c)
    c.commit()
    c.close()
    return p


def test_공지_작업이_성공을_남긴다(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr(jobs.notices_run, "run",
                        lambda *a, **k: {"items": 6428})
    jobs.run_notices(str(db), {}, NOW)
    c = repo.connect(db)
    try:
        assert sched.succeeded_today(c, "jbnu_notices", NOW)
    finally:
        c.close()


def test_한_번_성공하면_따라잡기가_멈춘다(tmp_path, monkeypatch):
    """★ 이게 15분마다 재실행을 멈추는 지점이다."""
    db = _db(tmp_path)
    sources = {"jbnu_notices": {"job": "notices", "schedule": ["07:00"]}}
    c = repo.connect(db)
    try:
        # 07:00 이 지났고 오늘 성공이 없다 → 따라잡기로 돈다
        assert "jbnu_notices" in sched.due_sources(sources, NOW, conn=c)
    finally:
        c.close()

    monkeypatch.setattr(jobs.notices_run, "run", lambda *a, **k: {"items": 1})
    jobs.run_notices(str(db), {}, NOW)

    c = repo.connect(db)
    try:
        # 성공을 남겼으니 더 안 돈다
        assert "jbnu_notices" not in sched.due_sources(sources, NOW, conn=c)
    finally:
        c.close()


def test_실패하면_error_로_남기고_다시_돈다(tmp_path, monkeypatch):
    """실패를 성공으로 적으면 조용히 안 돌게 된다. 그건 더 나쁘다."""
    db = _db(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("네트워크 끊김")

    monkeypatch.setattr(jobs.notices_run, "run", boom)
    try:
        jobs.run_notices(str(db), {}, NOW)
    except RuntimeError:
        pass
    c = repo.connect(db)
    try:
        assert not sched.succeeded_today(c, "jbnu_notices", NOW)
    finally:
        c.close()
