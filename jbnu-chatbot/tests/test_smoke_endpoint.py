"""배포 서버에서 도는 스모크 — 위치 의존 결함 대응.

★ 로컬 스모크는 네트워크 위치를 고정한 반쪽 검증이다.
  실제로 생협 API 가 한국 IP 200 / 해외 IP 403 이었고,
  로컬에서는 아무리 돌려도 초록불이었다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from crawler import ingest as ingest_mod
from crawler import fetch as fetch_mod
from crawler import loop as loop_mod
from crawler import run as run_mod
from crawler import smoke as smoke_mod
from skill import auth, server
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))
TOKEN = "smoke-token-0123456789abcd"


# ═══════════════════════════════════════════════════════════════
# 판정 로직 — 헤더 문제인지 IP 문제인지 가른다
# ═══════════════════════════════════════════════════════════════

def _r(name, status, method="POST"):
    return {"name": name, "method": method, "status": status,
            "ok": 200 <= status < 300, "bytes": 100}


def test_전부_통과하면_정상():
    coop = [_r("coop/bare", 200), _r("coop/ua", 200),
            _r("coop/page", 200, "GET")]
    assert "정상" in smoke_mod._verdict(coop)


def test_일부만_통과하면_헤더_의존():
    """어느 조합이 통과하는지 알려줘야 그걸 채택할 수 있다."""
    coop = [_r("coop/bare", 403), _r("coop/ua", 403),
            _r("coop/ua+ref+lang", 200), _r("coop/page", 200, "GET")]
    v = smoke_mod._verdict(coop)
    assert "헤더 의존" in v and "coop/ua+ref+lang" in v


def test_API만_막히고_페이지는_열리면_엔드포인트_차단():
    coop = [_r("coop/bare", 403), _r("coop/ua", 403), _r("coop/page", 200, "GET")]
    assert "API 만 차단" in smoke_mod._verdict(coop)


def test_전부_막히면_IP_차단_유력():
    """이건 헤더로 못 뚫는다. 판단이 달라져야 한다."""
    coop = [_r("coop/bare", 403), _r("coop/ua", 403), _r("coop/page", 403, "GET")]
    v = smoke_mod._verdict(coop)
    assert "IP/지역 차단" in v


def test_변형이_한_단계씩_쌓인다():
    """어디서 갈리는지 보려면 한 번에 다 넣으면 안 된다."""
    names = [p.name for p in smoke_mod.coop_variants("20260810")]
    assert names[0] == "coop/bare"
    assert "coop/page" in names, "사이트 전체 차단인지 가르려면 페이지도 봐야 한다"
    # 헤더 개수가 단조 증가해야 원인을 좁힐 수 있다
    sizes = [len(p.headers) for p in smoke_mod.coop_variants("20260810")
             if p.method == "POST"]
    assert sizes == sorted(sizes)


# ═══════════════════════════════════════════════════════════════
# 엔드포인트
# ═══════════════════════════════════════════════════════════════

def test_smoke_엔드포인트는_인증_필요(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.TOKEN_ENV, TOKEN)
    c = repo.connect(tmp_path / "s.db")
    repo.init_db(c)
    c.close()
    client = TestClient(server.create_app(tmp_path / "s.db", with_scheduler=False))
    assert client.get("/admin/smoke").status_code == 401


# ═══════════════════════════════════════════════════════════════
# 403 처리 — 차단 사유를 버리지 않는다
# ═══════════════════════════════════════════════════════════════

def test_403은_fetch_error이고_본문이_기록된다(tmp_path):
    """★ 403 본문을 파서에 넘기면 JSONDecodeError 가 나고 차단 사유가 사라진다."""
    conn = repo.connect(tmp_path / "e.db")
    repo.init_db(conn)

    body = b'{"error":"Forbidden","reason":"blocked"}'
    res = fetch_mod.make_result("coop_week_menu", "https://x/api", "https://x/api",
                                403, body, "2026-08-11T11:35:00+09:00", "json")

    called = []
    rep = ingest_mod.ingest(conn, res, snapshot_dir=tmp_path,
                            parser=lambda t: called.append(1))

    assert rep.outcome == "fetch_error"
    assert not rep.parser_called and called == []
    assert "Forbidden" in (rep.response_body or "")

    row = conn.execute(
        "SELECT outcome, error_message FROM crawl_run ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    assert row["outcome"] == "fetch_error"
    assert "HTTP 403" in row["error_message"] and "Forbidden" in row["error_message"]
    conn.close()


def test_403이어도_기존_데이터를_안_건드린다(tmp_path):
    conn = repo.connect(tmp_path / "f.db")
    repo.init_db(conn)
    conn.execute("""INSERT INTO facility (id, name, facility_type, source_url, source_type)
                    VALUES ('jbnu:facility/진수원','진수원','식당','https://x','coop')""")
    conn.commit()
    before = conn.execute("SELECT COUNT(*) c FROM meal_service").fetchone()["c"]

    res = fetch_mod.make_result("coop_week_menu", "https://x/api", "https://x/api",
                                403, b"blocked", "2026-08-11T11:35:00+09:00", "json")
    ingest_mod.ingest(conn, res, snapshot_dir=tmp_path, parser=lambda t: None)

    after = conn.execute("SELECT COUNT(*) c FROM meal_service").fetchone()["c"]
    assert after == before
    # 스냅샷도 안 남긴다 — 남기면 다음 회차가 unchanged 로 스킵한다
    assert conn.execute("SELECT COUNT(*) c FROM source_snapshot").fetchone()["c"] == 0
    conn.close()


# ═══════════════════════════════════════════════════════════════
# 스케줄러 연동 — 하루 한 번
# ═══════════════════════════════════════════════════════════════

def test_스모크는_하루_한_번만_돈다(tmp_path, monkeypatch):
    monkeypatch.setattr(run_mod, "DB_PATH", tmp_path / "l.db")
    monkeypatch.setattr(run_mod, "SNAPSHOT_DIR", tmp_path / "snap")
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)

    calls = []
    monkeypatch.setattr(smoke_mod, "run",
                        lambda *a, **k: calls.append(1) or
                        {"at": "x", "verdict": "정상", "coop_passing_variants": ["coop/bare"]})

    lp = loop_mod.SchedulerLoop(smoke=True)
    day = dt.datetime(2026, 8, 11, 9, 0, tzinfo=KST)
    lp.tick(day)
    lp.tick(day.replace(hour=10))
    assert len(calls) == 1, "같은 날 두 번 돌면 안 된다"

    lp.tick(day + dt.timedelta(days=1))
    assert len(calls) == 2, "날이 바뀌면 다시 돈다"


def test_스모크_실패는_ERROR로_로그된다(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(run_mod, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setattr(run_mod, "SNAPSHOT_DIR", tmp_path / "snap")
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)
    monkeypatch.setattr(smoke_mod, "run", lambda *a, **k: {
        "at": "x", "verdict": "사이트 전체 차단 — IP/지역 차단 유력",
        "coop_passing_variants": []})

    lp = loop_mod.SchedulerLoop(smoke=True)
    with caplog.at_level("INFO", logger="jbnu.scheduler"):
        lp.tick(dt.datetime(2026, 8, 11, 9, 0, tzinfo=KST))
    assert "[scheduler] SMOKE PROBLEM" in caplog.text


def test_스모크가_터져도_틱이_안_죽는다(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(run_mod, "DB_PATH", tmp_path / "n.db")
    monkeypatch.setattr(run_mod, "SNAPSHOT_DIR", tmp_path / "snap")
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)

    def boom(*a, **k):
        raise RuntimeError("네트워크 없음")
    monkeypatch.setattr(smoke_mod, "run", boom)

    lp = loop_mod.SchedulerLoop(smoke=True)
    with caplog.at_level("INFO", logger="jbnu.scheduler"):
        lp.tick(dt.datetime(2026, 8, 11, 9, 0, tzinfo=KST))
    assert "[scheduler] SMOKE ERROR" in caplog.text
    assert lp.ticks == 1, "스모크 실패가 틱을 죽이면 안 된다"


@pytest.mark.smoke
def test_smoke_실사이트_진단_전체():
    """로컬에서 돌리면 한국 IP 기준 결과가 나온다.

    배포 서버 결과와 **다를 수 있다는 게 요점**이다.
    비교하려면 GET /admin/smoke 를 쓴다.
    """
    out = smoke_mod.run()
    assert out["results"]
    assert "verdict" in out
