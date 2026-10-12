"""/health 의 수집 신선도는 **원천별**이어야 한다.

★ 지표가 침묵을 가렸다 (2026-10-13)
  처음엔 이렇게 냈다 —

      SELECT MAX(started_at) FROM crawl_run
       WHERE outcome IN ('success','unchanged')

  안내 페이지 전수(jbnu_pages)가 새벽 03:10 에 성공하면 그것만으로
  last_crawl_ok 가 오늘이 되고 crawl_stale=False 가 된다.
  그 뒤에서 생협이 **두 달(1507h)** 죽어 있어도 초록불이다.
  공지·총학시트·어휘사전도 같이 가려진다.

  침묵을 잡으려고 만든 지표가 침묵을 가렸으니 **없는 것보다 나빴다.**
  「뭉쳐 세지 마라」를 침묵 감지 커밋에서 깼다.

★ 임계는 원천마다 다르다 — 그 판단을 여기 다시 적지 않는다
  생협은 대표 PC 백필이라 192h 가 정상이고 24h 를 대면 매일 거짓 경보가 뜬다.
  crawler.schedule.source_freshness 가 config/sources.yaml 의
  stale_after_hours 를 읽어 판단한다. 두 벌이면 조용히 어긋난다.
"""

from __future__ import annotations

import datetime as dt

from starlette.testclient import TestClient

from skill import server
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))


def _db(tmp_path, runs):
    p = tmp_path / "h.db"
    c = repo.connect(p)
    repo.init_db(c)
    for i, (key, started, outcome) in enumerate(runs):
        c.execute(
            "INSERT INTO crawl_run(id,source_key,started_at,finished_at,"
            "outcome,items_parsed,items_quarantined) VALUES(?,?,?,?,?,0,0)",
            (f"r{i}", key, started, started, outcome))
    c.commit()
    c.close()
    return p


def test_한_원천이_성공해도_죽은_원천을_가리지_않는다(tmp_path):
    now = dt.datetime.now(KST)
    p = _db(tmp_path, [
        # 안내 페이지는 세 시간 전에 성공했다 — 뭉쳐 세면 이것만 보인다
        ("jbnu_pages", (now - dt.timedelta(hours=3)).isoformat(), "success"),
        # 생협은 두 달째 성공이 없다 (Render 403, 대표 PC 백필이 멈춤)
        ("coop_week_menu", (now - dt.timedelta(days=63)).isoformat(), "success"),
        ("coop_week_menu", (now - dt.timedelta(minutes=15)).isoformat(),
         "fetch_error"),
    ])
    with TestClient(server.create_app(p)) as client:
        body = client.get("/health").json()

    stale = body["crawl_stale"]
    assert isinstance(stale, list), "bool 하나로는 어느 원천인지 못 말한다"
    assert "coop_week_menu" in stale, "두 달 죽은 원천이 가려졌다"
    assert "jbnu_pages" not in stale

    last = body["last_crawl_ok"]
    assert isinstance(last, dict), "원천별로 내야 어디가 멈췄는지 보인다"
    assert last["jbnu_pages"], "성공한 원천의 시각이 비어 있다"


def test_건수와_경로는_여전히_안_낸다(tmp_path):
    """★ 뒤집은 것은 **소스 이름**뿐이다. 규모·경로는 운영 정보로 남는다."""
    now = dt.datetime.now(KST)
    p = _db(tmp_path, [("jbnu_pages", now.isoformat(), "success")])
    with TestClient(server.create_app(p)) as client:
        body = client.get("/health").json()
    assert set(body) <= {"ok", "warm", "commit", "last_crawl_ok", "crawl_stale"}
    flat = repr(body)
    for leak in ("db_path", "items_parsed", "sections", "/var/data"):
        assert leak not in flat


def test_DB_가_비어도_health_는_산다(tmp_path):
    """★ health 가 DB 때문에 죽으면 배포 확인 수단까지 같이 죽는다."""
    p = _db(tmp_path, [])
    with TestClient(server.create_app(p)) as client:
        r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True
