"""서버 코퍼스에 그 낱말이 몇 번 나오나 — /admin/terms

★ 왜 만들었나 (2026-08-13)
  학교가 OASIS → JUMP 로 갈아탔을 때 우리 사본이 낡았다고 판단했는데
  **그건 로컬 사본 숫자였다.** 서버는 이미 최신이었다.
  서버 상태를 볼 방법이 로그밖에 없어서 하루를 잘못 진단했다.

  '로컬 숫자를 서버 상태로 말하지 않는다' 고 적어 놓고도 그랬다.
  볼 수단이 없으면 규율은 안 지켜진다.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from skill import auth, server
from store import repo

PAGE = "https://www.jbnu.ac.kr/x.do"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(auth.TOKEN_ENV, "t0k3n-for-tests-0123456789")
    db = tmp_path / "t.db"
    c = repo.connect(db)
    repo.init_db(c)
    c.execute("""INSERT INTO page_registry (page_url, host, path, kind,
                    discovered_at, parse_status, title, last_attempt_at)
                 VALUES (?,?,?,?,?,?,?,?)""",
              (PAGE, "www.jbnu.ac.kr", "/x.do", "page", "2026-08-13", "ok",
               "자퇴 / 제적", "2026-08-13T03:20:00+09:00"))
    for i, t in enumerate(["JUMP 에서 자퇴 신청", "학과장 확인", "JUMP 로그인"]):
        c.execute("""INSERT INTO page_section
                     (section_key, page_url, ordinal, depth, kind, path, text,
                      raw_text, is_leaf, section_hash, observed_at, source_url)
                     VALUES (?,?,?,0,'block',?,?,?,1,'h','2026-08-13',?)""",
                  (f"s{i}", PAGE, i, t, t, t, PAGE))
    c.commit()
    c.close()
    with TestClient(server.create_app(db, with_scheduler=False)) as cl:
        yield cl


def test_낱말_출현을_센다(client):
    r = client.get("/admin/terms", params={"q": "JUMP,OASIS"},
                   headers={auth.HEADER_NAME: "t0k3n-for-tests-0123456789"})
    assert r.status_code == 200
    res = {x["term"]: x for x in r.json()["results"]}
    assert res["JUMP"]["sections"] == 2 and res["JUMP"]["pages"] == 1
    assert res["OASIS"]["sections"] == 0          # 없으면 0 이라고 말한다
    assert res["JUMP"]["by_host"][0]["host"] == "www.jbnu.ac.kr"


def test_언제_본_것인지_같이_준다(client):
    """★ 이게 이번 혼선의 핵심이다. 숫자만 주면 또 로컬과 헷갈린다."""
    r = client.get("/admin/terms", params={"q": "JUMP"},
                   headers={auth.HEADER_NAME: "t0k3n-for-tests-0123456789"})
    body = r.json()
    assert "observed_at" in body
    assert body["results"][0]["sample_pages"][0]["observed"].startswith("2026-08-13")


def test_인증이_필요하다(client):
    assert client.get("/admin/terms", params={"q": "JUMP"}).status_code == 401


def test_낱말이_없으면_무엇이_필요한지_말한다(client):
    r = client.get("/admin/terms", headers={auth.HEADER_NAME: "t0k3n-for-tests-0123456789"})
    assert "q" in r.json()["error"]


def test_한꺼번에_다섯_개까지만(client):
    r = client.get("/admin/terms", params={"q": "a,b,c,d,e,f,g"},
                   headers={auth.HEADER_NAME: "t0k3n-for-tests-0123456789"})
    assert len(r.json()["results"]) == 5
