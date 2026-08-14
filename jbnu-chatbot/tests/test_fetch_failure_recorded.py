"""가져오다 죽으면 **반드시 기록에 남는다**.

★ 배포 서버에서 coop_week_menu 가 '성공도 실패도 없음' 이었다 (2026-08-14)
  스케줄러는 그 원천을 대상으로 잡았고 last_error 도 null 이었다.
  돌긴 도는데 아무 데도 안 남는 상태 — **침묵이 가장 위험하다.**

  원인이 두 겹이었다.
    1. crawl_run 은 ingest() 안에서 쓰이는데 fetch 는 그 앞에 있다.
       403 은 결과가 돌아와 ingest 가 fetch_error 로 남기지만,
       연결 끊김·타임아웃은 예외라 그 길로 못 간다.
    2. main() 이 예외를 잡아 print 만 하고 **0 을 돌려줬다.**
       스케줄러는 성공으로 봤다.

  둘 다 고쳤다. 이 파일이 그걸 지킨다.
"""

from __future__ import annotations

import pytest

from crawler import run as run_mod
from store import repo


@pytest.fixture()
def conn(tmp_path):
    c = repo.connect(tmp_path / "r.db")
    repo.init_db(c)
    c.commit()
    return c


CFG = {"url": "https://coopjbnu.kr/x", "parser": "coop_week_menu",
       "media_type": "json", "method": "POST", "tier": "T1"}


def _runs(c) -> list[tuple[str, str]]:
    return [(r["source_key"], r["outcome"])
            for r in c.execute("SELECT source_key, outcome FROM crawl_run")]


def test_fetch_예외가_crawl_run에_남는다(conn, monkeypatch):
    def boom(*_a, **_k):
        raise ConnectionResetError("connection reset by peer")
    monkeypatch.setattr(run_mod.fetch_mod, "fetch", boom)

    with pytest.raises(ConnectionResetError):
        run_mod.run_source(conn, "coop_week_menu", CFG,
                           date=None, dry_run=False, force=False)

    assert _runs(conn) == [("coop_week_menu", "fetch_error")]
    msg = conn.execute("SELECT error_message FROM crawl_run").fetchone()[0]
    assert "ConnectionResetError" in msg


def test_기록하다_죽어도_원래_실패를_삼키지_않는다(conn, monkeypatch):
    """★ 남기는 게 목적인 코드가 원래 예외를 가리면 안 된다."""
    def boom(*_a, **_k):
        raise TimeoutError("timed out")
    monkeypatch.setattr(run_mod.fetch_mod, "fetch", boom)
    monkeypatch.setattr(run_mod.repo, "start_crawl",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db")))
    with pytest.raises(TimeoutError):
        run_mod.run_source(conn, "coop_week_menu", CFG,
                           date=None, dry_run=False, force=False)


def test_실패하면_0을_돌려주지_않는다(tmp_path, monkeypatch):
    """★ print 만 하고 0 을 돌려줘서 스케줄러가 성공으로 보고 있었다."""
    monkeypatch.setattr(run_mod, "DB_PATH", tmp_path / "m.db")
    monkeypatch.setattr(run_mod.fetch_mod, "fetch",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no route")))
    assert run_mod.main(["--source", "coop_week_menu"]) == 1


def test_성공하면_0이다(tmp_path, monkeypatch):
    monkeypatch.setattr(run_mod, "DB_PATH", tmp_path / "m2.db")
    assert run_mod.main(["--list"]) == 0
