"""알려진 차단 처리 — 경보 소음 방지 + 감시 방향 역전.

★ 실패가 '정상 상태'인 원천을 매일 ERROR 로 올리면 경고등이 상시 켜지고,
  그러면 진짜 문제가 묻힌다. 안전 분기에서 caveat 를 뺀 것과 같은 판단이다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from crawler import loop as loop_mod
from crawler import run as run_mod
from crawler import schedule as sched
from crawler import smoke as smoke_mod
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime(2026, 8, 11, 12, 0, tzinfo=KST)

BLOCKED_SOURCES = {
    "coop_week_menu": {
        "parser": "coop_week_menu", "label": "생협",
        "known_blocked": {"since": "2026-08-11", "workaround": "노트북 주 1회 백필"},
        "stale_after_hours": 192,
    },
    "jbnu_cafeteria_day": {"parser": "jbnu_cafeteria_day", "label": "학교"},
}


def _res(name, ok):
    return {"name": name, "ok": ok, "status": 200 if ok else 403,
            "method": "POST", "source_key": smoke_mod._source_of(name)}


# ═══════════════════════════════════════════════════════════════
# 1. 알려진 차단은 경보에서 뺀다
# ═══════════════════════════════════════════════════════════════

def test_알려진_차단이_계속_차단이면_경보가_없다():
    results = [_res("coop/bare", False), _res("coop/page", False),
               _res("jbnu/page", True)]
    assert smoke_mod._alerts(results, BLOCKED_SOURCES) == []


def test_알려지지_않은_차단은_경보():
    """생협은 조용하지만 학교가 막히면 그건 진짜 문제다."""
    results = [_res("coop/bare", False), _res("jbnu/page", False)]
    alerts = smoke_mod._alerts(results, BLOCKED_SOURCES)
    assert [a["source_key"] for a in alerts] == ["jbnu_cafeteria_day"]
    assert alerts[0]["kind"] == "newly_blocked"


# ═══════════════════════════════════════════════════════════════
# 2. 감시 방향 역전 — 차단이 풀리면 알린다
# ═══════════════════════════════════════════════════════════════

def test_차단이_풀리면_알린다():
    """★ 언젠가 풀릴 수 있고, 그때 바로 1차를 되찾아야 한다."""
    results = [_res("coop/bare", False), _res("coop/ua+ref", True),
               _res("jbnu/page", True)]
    alerts = smoke_mod._alerts(results, BLOCKED_SOURCES)
    assert len(alerts) == 1
    a = alerts[0]
    assert a["kind"] == "block_lifted" and a["tag"] == "BLOCK-LIFTED"
    assert a["passing"] == ["coop/ua+ref"], "어느 조합이 통과했는지 알려줘야 채택할 수 있다"
    assert "known_blocked" in a["message"], "해제 방법을 같이 알려준다"


def test_차단_해제는_ERROR가_아니라_WARNING(monkeypatch, caplog):
    """나쁜 소식이 아니라 조치가 필요한 좋은 소식이다."""
    monkeypatch.setattr(smoke_mod, "coop_variants",
                        lambda d: [smoke_mod.Probe("coop/bare", "POST", "https://x")])
    monkeypatch.setattr(smoke_mod, "other_sources", lambda: [])
    monkeypatch.setattr(smoke_mod, "run_probe",
                        lambda c, p: {"name": p.name, "ok": True, "status": 200,
                                      "method": p.method, "bytes": 10})
    with caplog.at_level("INFO", logger="jbnu.smoke"):
        out = smoke_mod.run(sources=BLOCKED_SOURCES)
    assert out["alerts"][0]["kind"] == "block_lifted"
    rec = next(r for r in caplog.records if "BLOCK-LIFTED" in r.getMessage())
    assert rec.levelname == "WARNING"


def test_스케줄러가_알려진_차단에_SMOKE_PROBLEM을_안_띄운다(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(run_mod, "DB_PATH", tmp_path / "k.db")
    monkeypatch.setattr(run_mod, "SNAPSHOT_DIR", tmp_path / "snap")
    monkeypatch.setattr(run_mod, "main", lambda argv: 0)
    monkeypatch.setattr(smoke_mod, "run", lambda *a, **k: {
        "at": "x", "verdict": "사이트 전체 차단", "coop_passing_variants": [],
        "alerts": []})            # 알려진 차단 → 경보 없음

    lp = loop_mod.SchedulerLoop(smoke=True)
    with caplog.at_level("INFO", logger="jbnu.scheduler"):
        lp.tick(NOW)
    assert "SMOKE PROBLEM" not in caplog.text
    assert "[scheduler] SMOKE ok" in caplog.text


# ═══════════════════════════════════════════════════════════════
# 3. 원천별 마지막 성공 — 백필이 멈춘 걸 잡는다
# ═══════════════════════════════════════════════════════════════

@pytest.fixture()
def fconn(conn):
    return conn


def _ok(conn, key, at):
    repo.start_crawl(conn, run_id=f"{key}/{at}", source_key=key, started_at=at)
    repo.finish_crawl(conn, f"{key}/{at}", outcome="success", finished_at=at)
    conn.commit()


def test_소스마다_임계가_다르다(fconn):
    """생협 24시간 기준을 적용하면 매일 경보가 뜬다. 8일이 정상이다."""
    _ok(fconn, "coop_week_menu", "2026-08-08T10:00:00+09:00")      # 3일 전
    _ok(fconn, "jbnu_cafeteria_day", "2026-08-08T10:00:00+09:00")  # 3일 전

    rows = {f["source_key"]: f
            for f in sched.source_freshness(fconn, BLOCKED_SOURCES, NOW)}
    assert rows["coop_week_menu"]["stale"] is False, "8일 임계 안이다"
    assert rows["jbnu_cafeteria_day"]["stale"] is True, "24시간 임계를 넘었다"


def test_백필이_8일_넘게_안_돌면_잡는다(fconn):
    """★ 알려진 차단이라고 감시를 면제하면 백필이 멈춘 걸 영영 모른다."""
    _ok(fconn, "coop_week_menu", "2026-08-01T10:00:00+09:00")   # 10일 전
    _ok(fconn, "jbnu_cafeteria_day", "2026-08-11T07:00:00+09:00")

    stale = {a["source_key"] for a in sched.heartbeat(fconn, BLOCKED_SOURCES, NOW)}
    assert stale == {"coop_week_menu"}

    row = next(f for f in sched.source_freshness(fconn, BLOCKED_SOURCES, NOW)
               if f["source_key"] == "coop_week_menu")
    assert 10.0 <= row["age_days"] < 10.5
    assert row["known_blocked"] is True
    assert "백필" in (row["workaround"] or ""), "우회 경로를 같이 알려준다"


def test_기록이_없으면_stale(fconn):
    rows = {f["source_key"]: f
            for f in sched.source_freshness(fconn, BLOCKED_SOURCES, NOW)}
    assert all(r["stale"] for r in rows.values())
    assert rows["coop_week_menu"]["reason"] == "성공 크롤 기록 없음"


def test_실제_설정에_생협_차단이_기록돼_있다():
    """운영 설정과 코드가 어긋나지 않게 고정한다."""
    src = sched.load_schedule()
    coop = src["coop_week_menu"]
    assert coop.get("known_blocked"), "차단 사실이 설정에 기록돼야 한다"
    assert coop["stale_after_hours"] == 192, "주 1회 백필 = 8일"
    assert not src["jbnu_cafeteria_day"].get("known_blocked")
