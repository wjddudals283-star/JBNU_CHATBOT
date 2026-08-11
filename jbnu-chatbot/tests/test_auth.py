"""스킬 엔드포인트 인증 — 배포 전 필수.

Render URL 은 서비스명 기반이라 추측 가능하고, 저장소가 public 이라
서비스명이 render.yaml 에 그대로 있다. 배포하는 순간 실제로 열린다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from skill import auth, server
from store import repo

TOKEN = "test-token-0123456789abcdef"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.TOKEN_ENV, TOKEN)
    c = repo.connect(tmp_path / "a.db")
    repo.init_db(c)
    c.close()
    return TestClient(server.create_app(tmp_path / "a.db", with_scheduler=False))


def _payload():
    return {"userRequest": {"utterance": "후생관 점심", "user": {"id": "u"}},
            "action": {"params": {"outlet": "후생관"}, "detailParams": {}}}


# ═══════════════════════════════════════════════════════════════
# 보호 대상
# ═══════════════════════════════════════════════════════════════

def test_토큰_없으면_401(client):
    assert client.post("/skill/food.menu.today", json=_payload()).status_code == 401


def test_토큰_틀리면_401(client):
    r = client.post("/skill/food.menu.today", json=_payload(),
                    headers={auth.HEADER_NAME: "wrong-token-0123456789ab"})
    assert r.status_code == 401


def test_토큰_맞으면_통과(client):
    r = client.post("/skill/food.menu.today", json=_payload(),
                    headers={auth.HEADER_NAME: TOKEN})
    assert r.status_code == 200
    assert r.json()["version"] == "2.0"


def test_admin_freshness는_인증_필요(client):
    """★ 크롤 상태·소스 목록이 그대로 나간다. 반드시 막는다."""
    assert client.get("/admin/freshness").status_code == 401
    r = client.get("/admin/freshness", headers={auth.HEADER_NAME: TOKEN})
    assert r.status_code == 200 and "sources" in r.json()


def test_admin_status도_인증_필요(client):
    assert client.get("/admin/status").status_code == 401
    r = client.get("/admin/status", headers={auth.HEADER_NAME: TOKEN})
    assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════
# 공개 대상
# ═══════════════════════════════════════════════════════════════

def test_health는_공개지만_운영정보를_안_담는다(client):
    """Render 헬스체크가 부른다. 살아 있다는 사실만 알린다."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    # ★ 정확 일치가 아니라 '무엇이 새면 안 되는가' 로 잰다.
    #   warm 은 규모가 아니라 상태다 — '떴다' 와 '답할 준비가 됐다' 는 다르고,
    #   배포 확인에 필요하다. 반면 아래 것들은 서비스 내부를 알려준다.
    assert set(body) <= {"ok", "warm"}
    assert body["ok"] is True
    assert isinstance(body["warm"], bool)
    for leak in ("meal_service", "scheduler", "sources", "last_tick",
                 "sections", "db_path", "error"):
        assert leak not in body


# ═══════════════════════════════════════════════════════════════
# Fail closed
# ═══════════════════════════════════════════════════════════════

def test_토큰_미설정이면_열지_않고_503(tmp_path, monkeypatch):
    """★ '설정을 깜빡했다'가 곧 '누구나 호출 가능'이 되면 안 된다."""
    monkeypatch.delenv(auth.TOKEN_ENV, raising=False)
    c = repo.connect(tmp_path / "b.db")
    repo.init_db(c)
    c.close()
    cl = TestClient(server.create_app(tmp_path / "b.db", with_scheduler=False))

    assert cl.post("/skill/food.menu.today", json=_payload()).status_code == 503
    assert cl.get("/admin/freshness").status_code == 503
    # 아무 토큰이나 보내도 열리지 않는다 (HTTP 헤더는 ASCII 만 실을 수 있다)
    assert cl.post("/skill/food.menu.today", json=_payload(),
                   headers={auth.HEADER_NAME: "anything-goes-here-1234"}).status_code == 503
    # 헬스체크는 계속 살아 있어야 Render 가 배포를 실패로 안 본다
    assert cl.get("/health").status_code == 200


def test_너무_짧은_토큰은_거부(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.TOKEN_ENV, "short")
    c = repo.connect(tmp_path / "c.db")
    repo.init_db(c)
    c.close()
    cl = TestClient(server.create_app(tmp_path / "c.db", with_scheduler=False))
    assert cl.post("/skill/food.menu.today", json=_payload(),
                   headers={auth.HEADER_NAME: "short"}).status_code == 503


def test_상수시간_비교를_쓴다():
    """문자열 == 은 앞에서부터 비교하다 다르면 끝나서 응답 시간으로 토큰이 샌다."""
    import inspect
    src = inspect.getsource(auth.check)
    assert "compare_digest" in src
    assert "token == expected" not in src
