"""POST /admin/ingest — 노트북 백필을 서버 DB 로.

★ 핵심: 원문 바이트를 받아 **서버가 다시 파싱**한다.
  밖에서 들어온 데이터를 그대로 믿지 않는다.
"""

from __future__ import annotations

import base64
import datetime as dt
import pathlib

import pytest
from fastapi.testclient import TestClient

from crawler import run as run_mod
from skill import auth, server
from store import repo

FIX = pathlib.Path(__file__).parent / "fixtures"
WEEK_JSON = (FIX / "coop_week_20260810.json").read_bytes()
KST = dt.timezone(dt.timedelta(hours=9))
TOKEN = "ingest-token-0123456789abc"
NOW = dt.datetime.fromisoformat("2026-08-11T10:00:00+09:00")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.TOKEN_ENV, TOKEN)
    monkeypatch.setattr(run_mod, "SNAPSHOT_DIR", tmp_path / "snap")
    db = tmp_path / "i.db"
    c = repo.connect(db)
    repo.init_db(c)
    for fid, name in (("jbnu:facility/후생관-푸드코트", "후생관"),
                      ("jbnu:facility/진수원", "진수원"),
                      ("jbnu:facility/의대식당", "의대식당")):
        c.execute("""INSERT OR IGNORE INTO facility
                       (id,name,facility_type,source_url,source_type)
                     VALUES (?,?,?,?,'coop')""",
                  (fid, name, "식당", "https://coopjbnu.kr/menu/week_menu.php"))
    c.commit()
    c.close()
    cl = TestClient(server.create_app(db, with_scheduler=False))
    cl.db = db
    return cl


def _recent(hours_ago: float = 1.0) -> str:
    """★ 고정 시각을 쓰면 안 된다.

    엔드포인트는 실제 now 로 검증하므로, 하드코딩한 시각은 실행 시각에 따라
    '미래'가 되어 400 이 난다. (실제로 새벽에 돌리다 겪었다 —
    낮에는 통과하고 밤에는 깨지는, 우리가 계속 잡던 그 유형이다.)
    """
    return (dt.datetime.now(KST) - dt.timedelta(hours=hours_ago)).isoformat()


def _body(content: bytes = WEEK_JSON, **over) -> dict:
    b = {
        "source_key": "coop_week_menu",
        "url": "https://coopjbnu.kr/function/get_cafeteria_menu.php",
        "http_status": 200,
        "fetched_at": _recent(),
        "media_type": "json",
        "content_b64": base64.b64encode(content).decode("ascii"),
    }
    b.update(over)
    return b


def _post(client, body):
    return client.post("/admin/ingest", json=body,
                       headers={auth.HEADER_NAME: TOKEN})


# ═══════════════════════════════════════════════════════════════
# 정상 경로
# ═══════════════════════════════════════════════════════════════

def test_노트북_원문이_서버_DB에_들어간다(client):
    r = _post(client, _body())
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] and out["outcome"] == "success"
    assert out["parsed"] > 0

    c = repo.connect(client.db)
    n = c.execute("SELECT COUNT(*) c FROM meal_service").fetchone()["c"]
    c.close()
    assert n == out["parsed"], "★ 학생 답변에 쓰이는 DB 에 실제로 들어가야 한다"


def test_관측시각은_노트북_것을_유지한다(client):
    """★ 서버가 받은 시각으로 바꾸면 신선도가 실제보다 좋아 보인다."""
    laptop_time = _recent(hours_ago=3)
    _post(client, _body(fetched_at=laptop_time))
    c = repo.connect(client.db)
    row = c.execute("SELECT observed_at FROM meal_service LIMIT 1").fetchone()
    c.close()
    assert row["observed_at"][:16] == laptop_time[:16]


def test_같은_원문을_두_번_밀어도_중복이_없다(client):
    a = _post(client, _body()).json()
    b = _post(client, _body(fetched_at=_recent(0.5))).json()
    assert b["outcome"] == "unchanged"
    c = repo.connect(client.db)
    n = c.execute("SELECT COUNT(*) c FROM meal_service").fetchone()["c"]
    c.close()
    assert n == a["parsed"]


# ═══════════════════════════════════════════════════════════════
# ★ 게이트 재통과 — 밖에서 온 데이터를 믿지 않는다
# ═══════════════════════════════════════════════════════════════

def test_깨진_원문은_서버가_거른다(client):
    """노트북이 뭘 보내든 서버가 파서·게이트를 다시 통과시킨다."""
    r = _post(client, _body(b'{"status":"fail","list":[]}'))
    out = r.json()
    assert not out["ok"] and out["outcome"] == "parse_error"

    c = repo.connect(client.db)
    n = c.execute("SELECT COUNT(*) c FROM meal_service").fetchone()["c"]
    c.close()
    assert n == 0, "기존 데이터도 안 건드린다"


def test_검증_게이트도_다시_돈다(client):
    """구분자를 놓친 항목은 서버에서 격리된다 (노트북이 통과시켰더라도)."""
    import json
    data = json.loads(WEEK_JSON)
    for item in data["list"]:
        for sub in item.get("subData") or []:
            if (sub.get("diet") or "").strip():
                sub["diet"] = "흰밥 미역국 제육볶음 포기김치"
                break
        break
    out = _post(client, _body(json.dumps(data, ensure_ascii=False).encode())).json()
    assert out["quarantined"] > 0
    assert any("구분자" in why for why in out["reasons"])


def test_403_본문을_밀어도_fetch_error(client):
    out = _post(client, _body(b"blocked", http_status=403)).json()
    assert out["outcome"] == "fetch_error"


# ═══════════════════════════════════════════════════════════════
# 입력 검증
# ═══════════════════════════════════════════════════════════════

def test_인증_없으면_401(client):
    assert client.post("/admin/ingest", json=_body()).status_code == 401


def test_모르는_source는_400(client):
    assert _post(client, _body(source_key="아무거나")).status_code == 400


def test_미래_시각은_거부한다(client):
    """★ 미래 시각을 받으면 신선도가 영원히 통과한다."""
    future = (dt.datetime.now(KST) + dt.timedelta(days=1)).isoformat()
    r = _post(client, _body(fetched_at=future))
    assert r.status_code == 400 and "미래" in r.json()["detail"]


def test_너무_오래된_시각은_거부한다(client):
    old = (dt.datetime.now(KST) - dt.timedelta(days=60)).isoformat()
    r = _post(client, _body(fetched_at=old))
    assert r.status_code == 400 and "오래" in r.json()["detail"]


def test_너무_큰_본문은_거부한다(client):
    r = _post(client, _body(b"x" * (6 * 1024 * 1024)))
    assert r.status_code == 413


def test_빈_본문은_거부한다(client):
    assert _post(client, _body(b"")).status_code == 400


def test_잘못된_base64는_400(client):
    assert _post(client, _body(content_b64="!!!not-base64!!!")).status_code == 400


def test_해시는_서버가_계산한다(client):
    """클라이언트가 준 해시를 믿지 않는다 — 애초에 받지도 않는다."""
    from skill import ingest_api
    assert "content_hash" not in ingest_api.IngestPayload.model_fields
    assert "stable_hash" not in ingest_api.IngestPayload.model_fields
