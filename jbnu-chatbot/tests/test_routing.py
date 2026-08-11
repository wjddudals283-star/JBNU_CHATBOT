"""단일 진입점 POST /skill — 블록 라우팅.

블록이 12개 도메인까지 늘 예정이라, 블록마다 스킬을 새로 등록하는 구조는
그때마다 토큰을 다시 붙여넣게 만든다.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest
from fastapi.testclient import TestClient

from crawler import fetch as fetch_mod
from crawler import ingest as ingest_mod
from crawler.parsers import coop_week_menu as coop
from skill import auth, kakao, routing, server
from store import repo

FIX = pathlib.Path(__file__).parent / "fixtures"
WEEK_JSON = (FIX / "coop_week_20260810.json").read_text(encoding="utf-8")
SRC = "https://coopjbnu.kr/menu/week_menu.php"
KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime.fromisoformat("2026-08-10T12:00:00+09:00")
TOKEN = "routing-token-0123456789ab"


@pytest.fixture(autouse=True)
def _clean():
    routing.clear_unmapped()
    yield
    routing.clear_unmapped()


@pytest.fixture()
def db(tmp_path):
    from skill import aliases
    path = tmp_path / "r.db"
    c = repo.connect(path)
    repo.init_db(c)
    for fid in aliases.all_facility_ids():
        c.execute("""INSERT OR IGNORE INTO facility
                       (id,name,facility_type,source_url,source_type)
                     VALUES (?,?,?,?,'coop')""",
                  (fid, aliases.canonical_name(fid), "식당", SRC))
    c.commit()
    res = fetch_mod.make_result("coop_week_menu", SRC, SRC, 200,
                                WEEK_JSON.encode("utf-8"),
                                "2026-08-10T06:00:00+09:00", "json")
    ingest_mod.ingest(c, res, parser=coop.parse, snapshot_dir=tmp_path,
                      extraction_method="json_api")
    c.close()
    return path


def _payload(utterance="후생관 점심", *, block_name=None, block_id=None,
             intent_name=None, **params):
    p = {"userRequest": {"utterance": utterance, "user": {"id": "u"}},
         "action": {"params": params, "detailParams": {}}}
    if block_name or block_id:
        p["userRequest"]["block"] = {"id": block_id or "", "name": block_name or ""}
    if intent_name:
        p["intent"] = {"name": intent_name}
    return p


# ═══════════════════════════════════════════════════════════════
# 라우팅 우선순위
# ═══════════════════════════════════════════════════════════════

def test_경로가_있으면_경로_우선():
    h, via = routing.resolve(_payload(block_name="학사일정"),
                             path_block="food.menu.today")
    assert h == "food.menu.today" and via == "path"


def test_block_id가_이름보다_우선(tmp_path):
    """★ 이름은 총학이 바꿀 수 있다. id 는 안 바뀐다."""
    cfg = tmp_path / "b.yaml"
    cfg.write_text(
        "handlers:\n  food.menu.today: [학식]\n  deadline.upcoming: [학사일정]\n"
        "ids:\n  '12345': deadline.upcoming\n", encoding="utf-8")
    h, via = routing.resolve(_payload(block_id="12345", block_name="학식"),
                             config_path=cfg)
    assert h == "deadline.upcoming" and via == "block.id"


def test_한국어_블록명으로_라우팅된다():
    """오픈빌더 블록 이름은 보통 한국어다. 우리 내부 키가 아니다."""
    for name in ("오늘 학식", "학식", "식단", "오늘 메뉴"):
        h, via = routing.resolve(_payload(block_name=name))
        assert h == "food.menu.today", name
        assert via.startswith("block.name")


def test_내부_키를_블록명으로_써도_된다():
    h, via = routing.resolve(_payload(block_name="food.menu.today"))
    assert h == "food.menu.today" and via == "block.name"


def test_공백은_무시한다():
    h, _ = routing.resolve(_payload(block_name="오늘  학식"))
    assert h == "food.menu.today"


def test_intent_name은_보조():
    h, via = routing.resolve(_payload(intent_name="학사일정"))
    assert h == "deadline.upcoming" and via == "intent.name:alias"


# ═══════════════════════════════════════════════════════════════
# ★ 모르는 블록은 추측하지 않는다
# ═══════════════════════════════════════════════════════════════

def test_모르는_블록은_폴백이고_기록된다():
    """키워드로 대충 맞히면 새 블록이 조용히 엉뚱한 답을 한다."""
    h, via = routing.resolve(_payload("공약 어떻게 됐어", block_name="총학 공약"))
    assert h is None and via == "unmapped"

    un = routing.unmapped_blocks()
    assert len(un) == 1
    assert un[0]["block_name"] == "총학 공약"
    assert "공약" in un[0]["sample_utterance"]


def test_같은_블록이_여러_번_오면_횟수만_는다():
    for _ in range(3):
        routing.resolve(_payload(block_name="총학 공약"))
    un = routing.unmapped_blocks()
    assert len(un) == 1 and un[0]["hits"] == 3


def test_비슷한_이름이라고_아무_핸들러로_안_보낸다():
    """'학식당 위치'는 식단 블록이 아니다. 부분일치로 맞히면 오답이 된다."""
    h, _ = routing.resolve(_payload(block_name="학식당 위치 안내"))
    assert h is None


# ═══════════════════════════════════════════════════════════════
# 서버 통합
# ═══════════════════════════════════════════════════════════════

@pytest.fixture()
def client(db, monkeypatch):
    monkeypatch.setenv(auth.TOKEN_ENV, TOKEN)
    # ★ 시계를 고정한다. 픽스처 날짜는 박혀 있는데 서버가 진짜 시계를 보면
    #   자정을 넘기는 순간 테스트가 깨진다 — 실제로 그렇게 깨졌다.
    #   달력에 따라 결과가 달라지는 테스트는 없는 것만 못하다.
    monkeypatch.setattr(server, "now_kst", lambda: NOW)
    return TestClient(server.create_app(db, with_scheduler=False))


def _post(client, path, payload):
    return client.post(path, json=payload, headers={auth.HEADER_NAME: TOKEN})


def test_단일_진입점이_식단을_처리한다(client):
    r = _post(client, "/skill",
              _payload("후생관 점심 뭐야", block_name="오늘 학식"))
    assert r.status_code == 200
    body = r.json()
    assert kakao.validate(body) == []
    assert "후생관" in body["template"]["outputs"][0]["listCard"]["header"]["title"]


def test_단일_진입점이_학사일정을_처리한다(client):
    r = _post(client, "/skill", _payload("곧 뭐 있어", block_name="학사일정"))
    assert r.status_code == 200
    assert kakao.validate(r.json()) == []


def test_기존_경로도_계속_동작한다(client):
    """마이그레이션 없이 넘어갈 수 있어야 한다."""
    r = _post(client, "/skill/food.menu.today", _payload("후생관 점심"))
    assert r.status_code == 200
    assert "listCard" in r.json()["template"]["outputs"][0]


def test_단일_진입점도_인증이_필요하다(client):
    assert client.post("/skill", json=_payload()).status_code == 401


def test_안전분기가_라우팅보다_먼저다(client):
    """★ 어떤 블록으로 들어왔든 민감 발화면 거기서 끝난다."""
    r = _post(client, "/skill",
              _payload("죽고싶어", block_name="오늘 학식", outlet="후생관"))
    text = r.json()["template"]["outputs"][0]["simpleText"]["text"]
    assert "109" in text
    assert "listCard" not in r.json()["template"]["outputs"][0]


def test_모르는_블록은_폴백_응답(client):
    r = _post(client, "/skill", _payload("공약", block_name="총학 공약"))
    body = r.json()
    assert kakao.validate(body) == []
    assert "준비되지 않았어요" in body["template"]["outputs"][0]["simpleText"]["text"]


def test_admin_blocks가_해제_경로를_준다(client):
    _post(client, "/skill", _payload("공약", block_name="총학 공약"))
    r = client.get("/admin/blocks", headers={auth.HEADER_NAME: TOKEN})
    body = r.json()
    assert "food.menu.today" in body["handlers"]
    assert any(u["block_name"] == "총학 공약" for u in body["unmapped"])
    assert "blocks.yaml" in body["hint"]


def test_admin_blocks도_인증_필요(client):
    assert client.get("/admin/blocks").status_code == 401


def test_설정에_정의된_핸들러가_전부_구현돼_있다():
    """설정에만 있고 코드에 없는 핸들러가 있으면 조용히 폴백이 된다."""
    import inspect
    src = inspect.getsource(server.handle)
    for h in routing.known_handlers():
        assert f'"{h}"' in src, f"{h} 가 server.handle 에 없다"
