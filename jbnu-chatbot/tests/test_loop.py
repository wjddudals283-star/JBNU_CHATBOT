"""인프로세스 스케줄러 루프."""

from __future__ import annotations

import datetime as dt

import pytest

from crawler import loop as loop_mod
from crawler import run as run_mod
from crawler import schedule as sched
from skill import server
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    db = tmp_path / "loop.db"
    monkeypatch.setattr(run_mod, "DB_PATH", db)
    monkeypatch.setattr(run_mod, "SNAPSHOT_DIR", tmp_path / "snap")
    return db


def test_예정에_없으면_아무것도_안_돈다(isolated_db, monkeypatch):
    called = []
    monkeypatch.setattr(run_mod, "main", lambda argv: called.append(argv) or 0)
    lp = loop_mod.SchedulerLoop()
    # 성공 기록을 심어 따라잡기도 막는다
    conn = repo.connect(isolated_db)
    repo.init_db(conn)
    for key in sched.load_schedule():
        repo.start_crawl(conn, run_id=f"ok-{key}", source_key=key,
                         started_at="2026-08-10T06:00:00+09:00")
        repo.finish_crawl(conn, f"ok-{key}", outcome="success",
                          finished_at="2026-08-10T06:00:05+09:00")
    conn.commit()
    conn.close()

    got = lp.tick(dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST))
    assert got == [] and called == []


def test_따라잡기가_루프에서도_동작한다(isolated_db, monkeypatch):
    """노트북/서버가 09시에 켜져도 그날 관측을 놓치지 않는다."""
    called = []
    monkeypatch.setattr(run_mod, "main", lambda argv: called.append(argv) or 0)
    lp = loop_mod.SchedulerLoop()
    got = lp.tick(dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST))
    assert "jbnu_cafeteria_day" in got
    assert ["--source", "jbnu_cafeteria_day"] in called


def test_예외가_나도_루프가_죽지_않는다(isolated_db, monkeypatch):
    """루프가 죽으면 아무 기록도 안 남는 침묵이 된다."""
    lp = loop_mod.SchedulerLoop(interval_sec=1)

    def boom(argv):
        lp._stop.set()      # 한 바퀴만 돌고 나가게 한다
        raise RuntimeError("원천 폭발")

    monkeypatch.setattr(run_mod, "main", boom)
    lp._run()               # 예외가 밖으로 새면 여기서 터진다
    assert lp.last_error and "원천 폭발" in lp.last_error


def test_RUN_SCHEDULER_없으면_스레드를_안_띄운다(tmp_path, monkeypatch):
    monkeypatch.delenv("RUN_SCHEDULER", raising=False)
    app = server.create_app(tmp_path / "x.db")
    assert app.state.scheduler is None


def test_health가_스케줄러_상태를_보고한다(tmp_path):
    from fastapi.testclient import TestClient
    c = repo.connect(tmp_path / "y.db")
    repo.init_db(c)
    c.close()
    app = server.create_app(tmp_path / "y.db", with_scheduler=True)
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["ok"] is True
    assert body["scheduler"] is not None
    assert "ticks" in body["scheduler"]
