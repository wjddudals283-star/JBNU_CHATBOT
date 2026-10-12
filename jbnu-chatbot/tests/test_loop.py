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
    """검색 가능해야 한다. 판정 문구는 사람용이라 한국어여도 되지만,
    **접두사와 키워드**는 ASCII 여야 'sched' 로 찾을 수 있다."""
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)
    lp = loop_mod.SchedulerLoop()
    with caplog.at_level("INFO", logger="jbnu.scheduler"):
        lp.tick(dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST))
    lines = [r.getMessage() for r in caplog.records]
    assert lines and all(l.startswith("[scheduler] ") for l in lines)
    for l in lines:
        # 접두사 다음 첫 토큰(TICK/RUN/DONE/FAIL…)까지는 ASCII
        head = l[len("[scheduler] "):].split(" ")[0]
        head.encode("ascii", errors="strict")


def test_스모크는_기본적으로_꺼져_있다(isolated_db, monkeypatch):
    """★ 실네트워크를 타는 동작은 명시적으로 켜야 한다.

    테스트가 무심코 외부 사이트를 두드리면 느려지고,
    원천 사정으로 우리 코드와 무관하게 빨간불이 된다.
    """
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)
    called = []
    from crawler import smoke as smoke_mod
    monkeypatch.setattr(smoke_mod, "run", lambda *a, **k: called.append(1))

    loop_mod.SchedulerLoop().tick(dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST))
    assert called == []

    loop_mod.SchedulerLoop(smoke=True).tick(dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST))
    assert len(called) == 1


def test_스모크는_환경변수를_따른다(tmp_path, monkeypatch):
    """★ with_scheduler 플래그가 아니라 RUN_SCHEDULER 환경변수를 따라야 한다.

    플래그에 묶으면 TestClient 를 여는 모든 테스트가 실사이트를 두드린다.
    (실제로 그렇게 만들었다가 테스트 스위트가 멈췄다.)
    """
    c = repo.connect(tmp_path / "p.db")
    repo.init_db(c)
    c.close()

    monkeypatch.delenv("RUN_SCHEDULER", raising=False)
    app = server.create_app(tmp_path / "p.db", with_scheduler=True)
    assert app.state.scheduler.smoke_enabled is False, "테스트 환경에서는 꺼진다"

    monkeypatch.setenv("RUN_SCHEDULER", "1")
    app2 = server.create_app(tmp_path / "p.db", with_scheduler=True)
    assert app2.state.scheduler.smoke_enabled is True, "프로덕션에서는 켜진다"


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
    # ★ 터뜨릴 원천을 **설정에서 뽑는다** (2026-10-13)
    #   `coop_week_menu` 를 박아 두었다가 생협이 known_blocked 가 되면서
    #   09:00 에 더는 due 가 아니어서 이 테스트가 깨졌다.
    #   보는 것은 '한 원천이 죽어도 나머지를 돈다' 이고 **어느 원천인지는
    #   아무 상관이 없다.** 상관없는 것을 박아 두면 설정이 바뀔 때 깨진다.
    _srcs = sched.load_schedule()
    boom = next(k for k, c in _srcs.items()
                if c.get("job") is None and not c.get("known_blocked"))

    def flaky(argv):
        calls.append(argv[1])
        if argv[1] == boom:
            raise RuntimeError("원천 폭발")
        return 0

    monkeypatch.setattr(run_mod, "main", flaky)
    lp = loop_mod.SchedulerLoop()
    with caplog.at_level("INFO", logger="jbnu.scheduler"):
        got = lp.tick(dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST))
    # 무거운 작업(job)은 RUN_SCHEDULER 가 없으면 건너뛴다 — 그건 정상이다.
    # 여기서 보는 것은 '원천 하나가 죽어도 나머지를 계속 돈다' 뿐이다.
    srcs = sched.load_schedule()
    parser_targets = [k for k in got if srcs.get(k, {}).get("job") is None]
    assert len(calls) == len(parser_targets), "한 소스가 죽어도 나머지를 건너뛰면 안 된다"
    assert f"[scheduler] FAIL source={boom}" in caplog.text


def test_예외가_나도_루프가_죽지_않는다(isolated_db, monkeypatch):
    """루프가 죽으면 아무 기록도 안 남는 침묵이 된다.

    ★ tick() 자체를 터뜨린다. run_mod.main 을 터뜨리면 **due 인 소스가 있어야만**
      호출되므로, 실행 시각에 따라 통과/무한루프가 갈린다.
      (실제로 그렇게 짰다가 자정 넘어 돌리니 멈췄다 — 예정 시각 전이라 due 가 없었다.)
    """
    lp = loop_mod.SchedulerLoop(interval_sec=1)

    def boom(self, now=None):
        lp._stop.set()      # 한 바퀴만 돌고 나가게 한다
        raise RuntimeError("원천 폭발")

    monkeypatch.setattr(loop_mod.SchedulerLoop, "tick", boom)
    lp._run()               # 예외가 밖으로 새면 여기서 터진다
    assert lp.last_error and "원천 폭발" in lp.last_error


def test_루프_테스트는_실행_시각에_의존하지_않는다(isolated_db, monkeypatch):
    """회귀 방지 — due 여부에 기대는 순간 밤낮에 따라 결과가 갈린다."""
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)
    lp = loop_mod.SchedulerLoop()
    for hour in (0, 6, 9, 23):
        got = lp.tick(dt.datetime(2026, 8, 11, hour, 5, tzinfo=KST))
        assert isinstance(got, list)   # 어느 시각이든 즉시 끝나야 한다


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
    # 실제 크롤을 타지 않게 틱을 막는다. TestClient 가 lifespan 으로 루프를 띄운다.
    monkeypatch.setattr(loop_mod.SchedulerLoop, "tick", lambda self, now=None: [])
    c = repo.connect(tmp_path / "y.db")
    repo.init_db(c)
    c.close()
    app = server.create_app(tmp_path / "y.db", with_scheduler=True)
    with TestClient(app) as client:
        public = client.get("/health").json()
        # ★ commit 은 공개 저장소의 공개 커밋이라 감출 값이 아니다.
        #   이 검사가 지키는 것은 DB 규모·경로·토큰 같은 **운영 정보**다.
        # ★ last_crawl_ok · crawl_stale 을 허용 목록에 넣는다 (2026-10-13)
        #   수집기가 이틀 죽어 있었는데 아무도 몰랐다 — 서버는 멀쩡히 답하고
        #   자료만 낡았다. 조용히 실패하는 게 시끄럽게 실패하는 것보다 위험하다.
        #   시각과 bool 은 감출 값이 아니다.
        # ★ 하루 뒤 뒤집었다 — **소스 이름도 낸다** (2026-10-13)
        #   어제는 "소스 이름·건수는 /admin 에" 라고 적었다. 그 결론이 틀렸다.
        #   MAX 하나로 뭉치면 안내 페이지가 새벽에 성공한 것만으로 초록불이 되고,
        #   생협이 두 달 죽은 걸 가린다. **원천별로 내야 지표가 일을 한다.**
        #   원천 이름은 config/sources.yaml 에 있고 리포가 Public 이라 숨길 값이 아니다.
        #   **건수**(items_parsed·페이지 수)는 여전히 /admin 에 남긴다 — 그건 규모다.
        assert set(public) <= {"ok", "warm", "commit", "last_crawl_ok", "crawl_stale"}
        assert public["ok"] is True
        assert client.get("/admin/status").status_code == 401
        body = client.get("/admin/status",
                          headers={auth.HEADER_NAME: token}).json()
    assert body["ok"] is True
    assert body["scheduler"] is not None and "ticks" in body["scheduler"]


def test_무거운_작업은_환경변수_없이는_돌지_않는다(isolated_db, monkeypatch, caplog):
    """수천 페이지를 도는 작업이 플래그에 묶이면 테스트가 실사이트를 두드린다.

    실제로 그렇게 멈췄다 — 스모크에서 이미 겪은 것과 같은 결함이다.
    건너뛴 사실은 반드시 로그에 남긴다. 침묵이 가장 위험하다.
    """
    monkeypatch.delenv("RUN_SCHEDULER", raising=False)
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)
    ran = []
    from crawler import jobs as jobs_mod
    monkeypatch.setitem(jobs_mod.JOBS, "pages",
                        lambda db, cfg, now: ran.append("pages"))

    lp = loop_mod.SchedulerLoop()
    with caplog.at_level("INFO", logger="jbnu.scheduler"):
        lp.tick(dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST))
    assert ran == [], "환경변수 없이 수천 페이지를 두드리면 안 된다"
    assert "SKIP job=" in caplog.text
