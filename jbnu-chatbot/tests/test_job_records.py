"""작업은 **전부** crawl_run 에 성공을 남겨야 한다.

★ 같은 일이 세 번 났다 — 그래서 고치는 것만으로는 안 된다
  due_sources 의 따라잡기는 '예정 시각이 지났는데 **오늘 성공 기록이 없으면**'
  실행한다. 기록을 안 남기는 작업은 succeeded_today 가 영원히 False 라
  예정 시각 뒤 **자정까지 매 틱(15분)** 다시 돈다.

    1차  notices  — 15분 걸리는 전량 재수집을 15분마다 (crawler/jobs.py 주석)
    2차  pages    — 자기가 남기고 있었다 (괜찮았다)
    3차  vocab    — 71,215 잎 사전을 하루 78번 다시 만들었다 (2026-10-13)

  "한 번이면 사고, 두 번이면 부류, 세 번은 구조" 다.
  작업을 새로 추가하는 사람이 _record 를 기억해야 하는 구조였다.
  이 자가 그 기억을 대신한다 — **config/sources.yaml 의 job 전부**를 돈다.

★ 자가 처음엔 틀렸다 — 그리고 그 실패가 더 나은 설계를 가리켰다
  처음엔 각 작업의 속일만 바꿔 끼우고 "작업이 기록을 남기나" 를 봤다.
  pages_run·council_run 은 **스스로** 기록하는데, 속일을 바꿔 끼우니
  그 기록까지 사라져서 둘이 거짓으로 실패했다.
  고치려고 보니 원인이 더 깊었다 — **작업마다 _record 를 기억해야 하는 구조**다.
  그래서 스케줄러에 그물을 뒀다 (SchedulerLoop._ensure_recorded):
  작업을 돌린 뒤 기록이 없으면 스케줄러가 대신 남긴다.
  누가 남기든 **틱이 끝나면 기록이 있다** — 이게 따라잡기가 보는 전부다.

★ 망을 안 탄다
  속일만 바꿔 끼우고 겉껍질(_record · 그물)은 진짜를 쓴다.
  JOBS 를 바꿔 끼우면 검사하려는 겉껍질이 사라져서 아무것도 안 본다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from crawler import jobs as jobs_mod
from crawler import loop
from crawler import schedule as sched
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 10, 13, 12, 0, tzinfo=KST)


def _job_sources() -> list[tuple[str, str]]:
    """(source_key, job 이름) — 설정에 적힌 것을 그대로 쓴다."""
    return [(k, c["job"]) for k, c in sched.load_schedule().items()
            if c.get("job")]


def test_설정의_job_이_전부_JOBS_에_있다():
    for key, job in _job_sources():
        assert job in jobs_mod.JOBS, f"{key} 의 job='{job}' 이 없다"


def _stub_inner(monkeypatch):
    monkeypatch.setattr(jobs_mod.pages_run, "run",
                        lambda *a, **k: {"ok": 0}, raising=True)
    monkeypatch.setattr(jobs_mod.notices_run, "run",
                        lambda *a, **k: {"ok": 0}, raising=True)
    monkeypatch.setattr(jobs_mod.vocab, "build",
                        lambda *a, **k: {"terms": 0}, raising=True)
    monkeypatch.setattr(jobs_mod.council_run, "run",
                        lambda *a, **k: {"rows": 0}, raising=True)


@pytest.mark.parametrize("source_key,job", _job_sources())
def test_틱이_끝나면_성공_기록이_있다(tmp_path, monkeypatch, source_key, job):
    """★ 이 자가 없어서 vocab 이 하루 78번 돌았다.

    누가 남기는지는 안 묻는다 — 작업이든 그물이든, **틱이 끝나면 있어야** 한다.
    따라잡기가 보는 것이 그것뿐이기 때문이다.
    """
    db = tmp_path / f"{job}.db"
    c = repo.connect(db)
    repo.init_db(c)
    c.commit()
    c.close()
    _stub_inner(monkeypatch)
    monkeypatch.setattr(loop.run_mod, "DB_PATH", db, raising=True)
    monkeypatch.setenv("RUN_SCHEDULER", "1")

    lp = loop.SchedulerLoop()
    jobs_mod.JOBS[job](str(db), sched.load_schedule()[source_key], NOW)
    lp._ensure_recorded(source_key, NOW)

    c = repo.connect(db, readonly=True)
    try:
        assert sched.succeeded_today(c, source_key, NOW), (
            f"{source_key} 가 성공을 안 남겼다 — 따라잡기가 매 틱 다시 돌린다")
    finally:
        c.close()


def test_그물은_이미_있는_기록을_덮어쓰지_않는다(tmp_path, monkeypatch):
    """★ 겹쳐 쓰면 건수가 두 벌이 된다 — 작업이 남긴 쪽이 정확하다."""
    db = tmp_path / "net.db"
    c = repo.connect(db)
    repo.init_db(c)
    c.commit()
    c.close()
    _stub_inner(monkeypatch)
    monkeypatch.setattr(loop.run_mod, "DB_PATH", db, raising=True)
    lp = loop.SchedulerLoop()

    # vocab 은 _record 로 스스로 남긴다 → 그물은 아무것도 안 해야 한다
    jobs_mod.JOBS["vocab"](str(db), sched.load_schedule()["vocab_build"], NOW)
    assert lp._ensure_recorded("vocab_build", NOW) is False

    c = repo.connect(db, readonly=True)
    try:
        n = c.execute("SELECT COUNT(*) FROM crawl_run "
                      "WHERE source_key='vocab_build'").fetchone()[0]
    finally:
        c.close()
    assert n == 1, f"기록이 {n} 벌이다 — 그물이 겹쳐 썼다"


def test_그물이_터져도_틱을_죽이지_않는다(tmp_path, monkeypatch):
    """★ 기록을 남기려다 수집을 멈추면 거래가 거꾸로다."""
    monkeypatch.setattr(loop.run_mod, "DB_PATH",
                        tmp_path / "없는폴더" / "x.db", raising=True)
    lp = loop.SchedulerLoop()
    assert lp._ensure_recorded("vocab_build", NOW) is False


def test_알려진_차단은_따라잡지_않는다(tmp_path):
    """★ 성공할 수 없는 재시도는 값이 0이고 노이즈만 만든다.

    생협은 Render 에서 영구 403 이다. 예정 시각(06:00) 이 지나면 성공 기록이
    영영 없으므로 따라잡기가 자정까지 매 틱 재시도했다 — 하루 72회.
    그 줄이 진짜 실패를 묻어서 수집기가 멈췄다고 이틀 오진했다.
    """
    db = tmp_path / "b.db"
    c = repo.connect(db)
    repo.init_db(c)
    c.commit()
    src = sched.load_schedule()
    blocked = [k for k, v in src.items() if v.get("known_blocked")]
    assert blocked, "알려진 차단 원천이 설정에 없다 — 이 검사가 뜻을 잃었다"

    # 예정 창에서 멀리 떨어진 시각 = 따라잡기만 걸릴 수 있는 자리
    for k in blocked:
        times = src[k].get("schedule") or []
        assert times
        late = NOW.replace(hour=23, minute=7)
        due = sched.due_sources(src, late, window_min=30, conn=c)
        assert k not in due, f"{k} 를 따라잡고 있다"
    c.close()


def test_차단이_풀리면_예정_시각에는_시도한다(tmp_path):
    """★ 감시 방향을 뒤집는 경로를 막지 않는다.

    따라잡기만 뺐다. 예정 창은 그대로라 차단이 풀리면 하루 3번 시도해서
    알아챈다 (config/sources.yaml 의 '차단이 풀리면(200) 알린다').
    """
    db = tmp_path / "w.db"
    c = repo.connect(db)
    repo.init_db(c)
    c.commit()
    src = sched.load_schedule()
    for k, cfg in src.items():
        if not cfg.get("known_blocked"):
            continue
        h, m = (int(x) for x in (cfg["schedule"] or ["06:00"])[0].split(":"))
        at = NOW.replace(hour=h, minute=m)
        assert k in sched.due_sources(src, at, window_min=30, conn=c), \
            f"{k} 가 예정 시각에도 안 돈다 — 차단 해제를 영영 못 본다"
    c.close()
