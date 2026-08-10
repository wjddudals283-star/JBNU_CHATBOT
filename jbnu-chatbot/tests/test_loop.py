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


def test_할_일이_없어도_틱_로그를_남긴다(isolated_db, monkeypatch, caplog):
    """★ 이게 없으면 '안 돎'과 '할 일 없음'이 로그상 같은 모양이 된다.

    실제로 배포 후 '스케줄러 흔적을 못 찾겠다'가 나왔고, 원인의 절반이 이거였다.
    """
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)
    conn = repo.connect(isolated_db)
    repo.init_db(conn)
    for key in sched.load_schedule():
        repo.start_crawl(conn, run_id=f"ok-{key}", source_key=key,
                         started_at="2026-08-10T06:00:00+09:00")
        repo.finish_crawl(conn, f"ok-{key}", outcome="success",
                          finished_at="2026-08-10T06:00:05+09:00")
    conn.commit()
    conn.close()

    lp = loop_mod.SchedulerLoop()
    with caplog.at_level("INFO", logger="jbnu.scheduler"):
        got = lp.tick(dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST))
    assert got == []
    text = caplog.text
    assert "[scheduler] TICK #1" in text
    assert "due=none" in text
    # 검색 가능한 ASCII 여야 한다. 한국어로만 남기면 'sched' 로 못 찾는다.
    assert "scheduler" in text


def test_로그_접두사가_ASCII다(isolated_db, monkeypatch, caplog):
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)
    lp = loop_mod.SchedulerLoop()
    with caplog.at_level("INFO", logger="jbnu.scheduler"):
        lp.tick(dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST))
    lines = [r.getMessage() for r in caplog.records]
    assert lines and all(l.startswith("[scheduler] ") for l in lines)
    for l in lines:
        l.encode("ascii", errors="strict")   # 한글이 섞이면 여기서 터진다


def test_status가_상태를_구조화해_돌려준다(isolated_db, monkeypatch):
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)
    lp = loop_mod.SchedulerLoop()
    lp.tick(dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST))
    s = lp.status()
    assert s["ticks"] == 1
    assert set(s) >= {"started_at", "ticks", "runs", "last_tick",
                      "last_targets", "last_error", "interval_sec", "alive"}


def test_한_소스가_죽어도_나머지는_돈다(isolated_db, monkeypatch, caplog):
    calls = []

    def flaky(argv):
        calls.append(argv[1])
        if argv[1] == "coop_week_menu":
            raise RuntimeError("원천 폭발")
        return 0

    monkeypatch.setattr(run_mod, "main", flaky)
    lp = loop_mod.SchedulerLoop()
    with caplog.at_level("INFO", logger="jbnu.scheduler"):
        got = lp.tick(dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST))
    assert len(calls) == len(got), "한 소스가 죽어도 나머지를 건너뛰면 안 된다"
    assert "[scheduler] FAIL source=coop_week_menu" in caplog.text


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


def test_스케줄러_상태는_인증_뒤에서_보고된다(tmp_path, monkeypatch):
    """운영 상태는 /health 가 아니라 /admin/status 에 있다.

    /health 는 Render 헬스체크용이라 공개인데, 거기에 운영 정보를 담으면
    주소만 알면 누구나 크롤 상태를 본다.
    """
    from fastapi.testclient import TestClient

    from skill import auth
    token = "loop-status-token-0123456789"
    monkeypatch.setenv(auth.TOKEN_ENV, token)
    c = repo.connect(tmp_path / "y.db")
    repo.init_db(c)
    c.close()
    app = server.create_app(tmp_path / "y.db", with_scheduler=True)
    with TestClient(app) as client:
        public = client.get("/health").json()
        assert public == {"ok": True}
        assert client.get("/admin/status").status_code == 401
        body = client.get("/admin/status",
                          headers={auth.HEADER_NAME: token}).json()
    assert body["ok"] is True
    assert body["scheduler"] is not None and "ticks" in body["scheduler"]
