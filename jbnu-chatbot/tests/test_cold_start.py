"""콜드 스타트 — 첫 학생이 침묵을 겪지 않아야 한다.

실측 (배포 직후)
    첫 요청   01:53:42,669 → 01:53:47,670   5.001초   무응답
    두 번째   01:55:36,347 → 01:55:36,607   0.26초    정상

카카오 스킬 타임아웃이 5초다. 첫 요청은 **반드시** 죽는다는 뜻이다.
5.001초가 딱 떨어진 게 단서였다 — sqlite3.connect() 의 기본 busy timeout 이
정확히 5.000초다. 성능이 아니라 **락 대기**였다.

원인 두 겹
  1. 배포하면 스케줄러가 밀린 수집을 즉시 시작한다 (crawler/loop.py).
     그 쓰기 락을 첫 요청이 기다렸다.
     WAL 에서 읽기는 원래 안 기다리는데, 우리 connect() 가 매번
     `PRAGMA journal_mode = WAL` 을 실행했다 — 그게 쓰기 연산이다.
  2. 첫 질의 비용 (모듈 로딩 · FTS 첫 접근)
"""

from __future__ import annotations

import sqlite3

import pytest
from starlette.testclient import TestClient

from skill import server
from store import repo


def _db(tmp_path):
    p = tmp_path / "w.db"
    c = repo.connect(p)
    repo.init_db(c)
    c.commit()
    c.close()
    return p


# ── 읽기 전용 연결 ───────────────────────────────────────────
def test_읽기전용은_journal_mode를_안_건드린다(tmp_path):
    """이게 콜드 스타트를 죽인 한 줄이다.

    `PRAGMA journal_mode = WAL` 은 쓰기다. 쓰는 쪽이 락을 쥐고 있으면
    **읽기만 하려던 연결이** 5초를 기다린다.
    """
    p = _db(tmp_path)
    c = repo.connect(p, readonly=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            c.execute("CREATE TABLE zzz (a INT)")
    finally:
        c.close()


def test_읽기전용도_읽기는_된다(tmp_path):
    p = _db(tmp_path)
    c = repo.connect(p, readonly=True)
    try:
        assert repo.section_total(c) == 0
    finally:
        c.close()


def test_busy_timeout이_카카오_예산보다_짧다():
    """기다리다 죽느니 일찍 포기하고 말이라도 하는 게 낫다.

    카카오는 5초에 끊는다. 우리가 5초를 기다리면 학생은 침묵을 받는다.
    """
    assert repo.READ_BUSY_MS < 5000


def test_파일이_없으면_읽기전용을_고집하지_않는다(tmp_path):
    """없는 파일을 ro 로 열면 그냥 실패한다. 그때는 평소대로 연다 —
    워밍업이 서버 기동을 막으면 안 된다."""
    c = repo.connect(tmp_path / "nope.db", readonly=True)
    c.close()


# ── 워밍업 ──────────────────────────────────────────────────
def test_기동하면_데운다(tmp_path):
    app = server.create_app(_db(tmp_path))
    with TestClient(app) as client:
        assert app.state.warm is not None       # lifespan 이 돌았다
        assert app.state.warm["ok"] is True
        assert client.get("/health").json()["warm"] is True


def test_스케줄러가_없어도_데운다(tmp_path):
    """워밍업이 스케줄러에 묶여 있으면 안 된다 — 둘은 다른 일이다."""
    app = server.create_app(_db(tmp_path), with_scheduler=False)
    with TestClient(app):
        assert app.state.warm["ok"] is True


def test_워밍업이_실패해도_서버는_뜬다(tmp_path):
    """워밍업은 편의지 기동의 조건이 아니다."""
    app = server.create_app(tmp_path / "없는파일.db")
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/health").json()["warm"] is False


def test_데우기_전엔_warm이_False다(tmp_path):
    """'떴다' 와 '답할 준비가 됐다' 는 다르다.

    이걸 구별 못 하면 배포 확인을 첫 학생이 대신 해 주게 된다.
    """
    app = server.create_app(_db(tmp_path))
    assert app.state.warm is None      # lifespan 전
