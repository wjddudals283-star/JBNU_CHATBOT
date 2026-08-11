"""스케줄러 · 하트비트 · 운영시간 이력 — 작업 9.

실사이트 스모크는 `-m smoke` 로 분리한다 (기본 실행에서 제외).
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from crawler import schedule as sched
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))
FIX = pathlib.Path(__file__).parent / "fixtures"
FID = "jbnu:facility/진수원"
SNAP = "jbnu:snap/x"
URL = "https://www.jbnu.ac.kr/web/unvrslife/campuslife/cafeteria.do"


@pytest.fixture()
def hconn(conn):
    conn.execute("""INSERT INTO facility (id, name, facility_type, source_url, source_type)
                    VALUES (?,?,?,?,'official')""", (FID, "진수원", "식당", URL))
    conn.commit()
    return conn


def _meta(valid_from: str, observed: str) -> repo.SourceMeta:
    return repo.SourceMeta(source_id="jbnu:snap/coop/2026-08-10T06:00", source_url=URL,
                           observed_at=observed, confidence=0.95,
                           extraction_method="html_selector", tier="T2",
                           valid_from=valid_from)


def _put_hours(conn, valid_from: str, *, lunch=("11:30", "14:00"),
               dinner: tuple | None = None) -> None:
    meta = _meta(valid_from, f"{valid_from}T07:00:00+09:00")
    for wd in (1, 2, 3, 4, 5):
        repo.upsert_hours(conn, facility_id=FID, term="unspecified", weekday=wd,
                          meal_type="lunch", is_closed=False,
                          open_time=lunch[0], close_time=lunch[1], meta=meta)
        if dinner:
            repo.upsert_hours(conn, facility_id=FID, term="unspecified", weekday=wd,
                              meal_type="dinner", is_closed=False,
                              open_time=dinner[0], close_time=dinner[1], meta=meta)
    for wd in (0, 6):
        repo.upsert_hours(conn, facility_id=FID, term="unspecified", weekday=wd,
                          meal_type="lunch", is_closed=True, meta=meta)
    conn.commit()


# ═══════════════════════════════════════════════════════════════
# 예정 시각 창
# ═══════════════════════════════════════════════════════════════

def test_예정_시각_창_안에서만_실행_대상이_된다():
    srcs = sched.load_schedule()
    at_6 = dt.datetime(2026, 8, 10, 6, 5, tzinfo=KST)
    due = sched.due_sources(srcs, at_6)
    assert "coop_week_menu" in due and "likehome_week_menu" in due

    # 예정 시각이 없는 시간대. **목록 전체를 비교하지 않는다** —
    # 원천이 늘 때마다 깨지는 테스트는 규칙이 아니라 눈금자를 지키는 것이다.
    at_13 = dt.datetime(2026, 8, 10, 13, 0, tzinfo=KST)
    due_13 = sched.due_sources(srcs, at_13)
    assert "coop_week_menu" not in due_13
    assert "likehome_week_menu" not in due_13

    at_11 = dt.datetime(2026, 8, 10, 11, 10, tzinfo=KST)
    assert "jbnu_cafeteria_day" in sched.due_sources(srcs, at_11)


def test_모든_원천이_주기_규칙을_지킨다():
    """★ 크롤 주기 ≤ 신선도 임계 ÷ 2.

    주기가 임계와 같으면 여유가 0이라 **한 번 실패하면 즉시 stale** 이다.
    실제로 jbnu_cafeteria_day 가 하루 1회 / 임계 24h 라 8/11 새벽에 C-2 로 떨어졌다.
    설정만 고치면 다시 어긋나므로 규칙을 테스트로 고정한다.
    """
    rows = sched.cadence_audit(sched.load_schedule())
    bad = [r for r in rows if not r["ok"]]
    assert not bad, "주기 규칙 위반: " + ", ".join(
        f"{r['source_key']}(간격 {r['max_gap_hours']}h > 예산 {r['budget_hours']}h)"
        for r in bad)


def test_1회_실패를_흡수한다():
    """규칙의 목적 — 한 번 실패해도 임계 안에 있어야 한다."""
    for r in sched.cadence_audit(sched.load_schedule()):
        assert r["survives_one_failure"], f"{r['source_key']} 는 1회 실패에 stale"


def test_야간_간격을_최대치로_센다():
    """★ 06:00/11:00/16:00 은 낮이 5시간이라 촘촘해 보이지만
    16:00 → 다음날 06:00 이 14시간이다. 이걸 빼먹으면 점검이 거짓말을 한다."""
    assert sched.max_gap_hours({"schedule": ["06:00", "11:00", "16:00"]}) == 14.0
    assert sched.max_gap_hours({"schedule": ["06:00", "11:00", "16:00", "21:00"]}) == 9.0
    assert sched.max_gap_hours({"schedule": ["07:00"]}) == 24.0
    assert sched.max_gap_hours({"schedule": []}) is None


def test_주간_원천은_168시간으로_센다():
    assert sched.max_gap_hours({"schedule": ["05:30"], "weekly_on": 0}) == 168.0


def test_차단된_원천은_실제_주기로_센다():
    """예정표가 실제 주기를 나타내지 않는다. 그대로 세면 점검이 통과해버린다."""
    cfg = {"schedule": ["06:00", "16:00"], "known_blocked": {"since": "x"},
           "effective_cadence_hours": 96, "stale_after_hours": 192,
           "parser": "coop_week_menu"}
    row = sched.cadence_audit({"coop_week_menu": cfg})[0]
    assert row["max_gap_hours"] == 96.0, "예정표(14h)가 아니라 실제 주기를 봐야 한다"


def test_운영시간_원천이_매일_예정에_들어있다():
    """개강 전후 변화를 관측하려면 이게 안 빠져야 한다."""
    srcs = sched.load_schedule()
    cfg = srcs["jbnu_cafeteria_day"]
    assert cfg.get("schedule"), "운영시간 원천에 예정 시각이 없다"
    assert cfg.get("hours_source") is True
    # 요일 제한이 걸려 있지 않아야 매일 돈다
    assert cfg.get("weekly_on") is None


def test_창을_놓치면_그날_안에_따라잡는다(hconn):
    """★ 노트북을 09시에 켠 날. 07:00 창은 이미 지났다.

    따라잡기가 없으면 그날 관측을 통째로 놓친다.
    개강 전후 3주는 다시 오지 않는 창이라 하루도 비우면 안 된다.
    """
    srcs = sched.load_schedule()
    at_9 = dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST)

    # conn 없이(창만 판단) → 아무것도 안 걸린다
    assert sched.due_sources(srcs, at_9) == []

    # conn 을 주면 '오늘 성공 없음'을 보고 따라잡는다
    caught = sched.due_sources(srcs, at_9, conn=hconn)
    assert "jbnu_cafeteria_day" in caught, "운영시간 관측을 놓치면 안 된다"
    assert "coop_week_menu" in caught


def test_오늘_이미_성공했으면_따라잡지_않는다(hconn):
    srcs = sched.load_schedule()
    at_9 = dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST)
    # ★ 하드코딩하지 않는다. 원천이 늘 때마다 테스트가 깨진다(실제로 깨졌다).
    for key in sched.load_schedule():
        repo.start_crawl(hconn, run_id=f"ok-{key}", source_key=key,
                         started_at="2026-08-10T07:00:00+09:00")
        repo.finish_crawl(hconn, f"ok-{key}", outcome="success",
                          finished_at="2026-08-10T07:00:05+09:00")
    hconn.commit()
    assert sched.due_sources(srcs, at_9, conn=hconn) == []


def test_예정_시각_전에는_따라잡지_않는다(hconn):
    """05시에 켰으면 06:00 예정은 아직 안 지났다. 미리 돌리지 않는다."""
    srcs = sched.load_schedule()
    at_5 = dt.datetime(2026, 8, 10, 5, 0, tzinfo=KST)
    due_5 = sched.due_sources(srcs, at_5, conn=hconn)
    # 06:00 예정인 원천은 아직 때가 아니다 (03:10 예정인 작업은 이미 지났다)
    assert "likehome_week_menu" not in due_5
    assert "coop_week_menu" not in due_5


def test_실패한_날은_따라잡는다(hconn):
    """parse_error 는 성공이 아니다. 다시 시도한다."""
    srcs = sched.load_schedule()
    repo.start_crawl(hconn, run_id="f", source_key="coop_week_menu",
                     started_at="2026-08-10T06:00:00+09:00")
    repo.finish_crawl(hconn, "f", outcome="parse_error",
                      finished_at="2026-08-10T06:00:05+09:00")
    hconn.commit()
    at_9 = dt.datetime(2026, 8, 10, 9, 0, tzinfo=KST)
    assert "coop_week_menu" in sched.due_sources(srcs, at_9, conn=hconn)


def test_창을_놓쳐도_다음_호출에서_잡힌다():
    """15~30분마다 호출하는 것을 전제로 한 창 방식."""
    srcs = sched.load_schedule()
    for minute in (0, 10, 25):
        at = dt.datetime(2026, 8, 10, 6, minute, tzinfo=KST)
        assert "coop_week_menu" in sched.due_sources(srcs, at, window_min=30)
    assert "coop_week_menu" not in sched.due_sources(
        srcs, dt.datetime(2026, 8, 10, 6, 45, tzinfo=KST), window_min=30)


# ═══════════════════════════════════════════════════════════════
# 하트비트 — 침묵 감지
# ═══════════════════════════════════════════════════════════════

def test_기록이_아예_없으면_경보(hconn):
    srcs = sched.load_schedule()
    now = dt.datetime(2026, 8, 10, 12, 0, tzinfo=KST)
    alerts = sched.heartbeat(hconn, srcs, now)
    keys = {a["source_key"] for a in alerts}
    assert "coop_week_menu" in keys
    assert any("기록 없음" in a["reason"] for a in alerts)


def test_최근_성공이_있으면_경보_없음(hconn):
    srcs = sched.load_schedule()
    now = dt.datetime(2026, 8, 10, 12, 0, tzinfo=KST)
    # ★ 하드코딩하지 않는다. 원천이 늘 때마다 테스트가 깨진다(실제로 깨졌다).
    for key in sched.load_schedule():
        repo.start_crawl(hconn, run_id=f"r-{key}", source_key=key,
                         started_at="2026-08-10T06:00:00+09:00")
        repo.finish_crawl(hconn, f"r-{key}", outcome="success",
                          finished_at="2026-08-10T06:00:05+09:00")
    hconn.commit()
    assert sched.heartbeat(hconn, srcs, now) == []


def test_실패만_있으면_경보(hconn):
    """실패는 crawl_run 에 남지만 성공이 아니다. 침묵과 같이 취급한다."""
    srcs = sched.load_schedule()
    now = dt.datetime(2026, 8, 10, 12, 0, tzinfo=KST)
    repo.start_crawl(hconn, run_id="f1", source_key="coop_week_menu",
                     started_at="2026-08-10T06:00:00+09:00")
    repo.finish_crawl(hconn, "f1", outcome="parse_error",
                      finished_at="2026-08-10T06:00:05+09:00")
    hconn.commit()
    assert any(a["source_key"] == "coop_week_menu"
               for a in sched.heartbeat(hconn, srcs, now))


def test_25시간_지나면_경보(hconn):
    """기본 임계 24시간. 생협은 백필 주기 때문에 192시간이라 여기 안 쓴다."""
    srcs = sched.load_schedule()
    assert not srcs["jbnu_cafeteria_day"].get("stale_after_hours"), "기본 임계 소스"
    repo.start_crawl(hconn, run_id="old", source_key="jbnu_cafeteria_day",
                     started_at="2026-08-09T06:00:00+09:00")
    repo.finish_crawl(hconn, "old", outcome="success",
                      finished_at="2026-08-09T06:00:05+09:00")
    hconn.commit()
    now = dt.datetime(2026, 8, 10, 12, 0, tzinfo=KST)   # 30시간 후
    a = next(x for x in sched.heartbeat(hconn, srcs, now)
             if x["source_key"] == "jbnu_cafeteria_day")
    assert a["age_hours"] > 24


def test_생협은_같은_경과시간에도_경보가_아니다(hconn):
    """★ 주 1회 백필이라 30시간은 정상이다. 24시간 기준이면 매일 경보가 뜬다."""
    srcs = sched.load_schedule()
    repo.start_crawl(hconn, run_id="c", source_key="coop_week_menu",
                     started_at="2026-08-09T06:00:00+09:00")
    repo.finish_crawl(hconn, "c", outcome="success",
                      finished_at="2026-08-09T06:00:05+09:00")
    hconn.commit()
    now = dt.datetime(2026, 8, 10, 12, 0, tzinfo=KST)
    assert not any(x["source_key"] == "coop_week_menu"
                   for x in sched.heartbeat(hconn, srcs, now))


# ═══════════════════════════════════════════════════════════════
# 운영시간 이력 — unspecified 해소 경로
# ═══════════════════════════════════════════════════════════════

def test_이력은_보존하되_질의는_한_벌만_쓴다(hconn):
    """개강 전 시간표와 개강 후 시간표가 동시에 답을 주장하면 안 된다."""
    _put_hours(hconn, "2026-08-10", lunch=("11:30", "14:00"))
    _put_hours(hconn, "2026-09-07", lunch=("11:00", "14:30"), dinner=("17:30", "19:00"))

    assert repo.hours_observation_dates(hconn, FID) == ["2026-08-10", "2026-09-07"]

    before = repo.query_operating_hours(hconn, facility_id=FID, on_date="2026-08-20")
    assert {r["open_time"] for r in before if not r["is_closed"]} == {"11:30"}
    assert all(r["meal_type"] == "lunch" for r in before)

    after = repo.query_operating_hours(hconn, facility_id=FID, on_date="2026-09-10")
    assert {r["open_time"] for r in after if not r["is_closed"]} == {"11:00", "17:30"}


def test_관측보다_이른_날짜는_근거가_없다(hconn):
    """8월에 관측한 시간표로 6월을 답하면 그게 추론이다."""
    _put_hours(hconn, "2026-08-10")
    assert repo.query_operating_hours(hconn, facility_id=FID,
                                      on_date="2026-06-01") == []
    assert repo.serves_meal(hconn, facility_id=FID, date="2026-06-01",
                            meal_type="lunch") is None


def test_시간표_변화가_관측으로_잡힌다(hconn):
    """★ 이게 term='unspecified' 를 추론이 아니라 관측으로 해소하는 경로다."""
    _put_hours(hconn, "2026-08-10", lunch=("11:30", "14:00"))
    assert repo.hours_changes(hconn, FID) == [], "관측 1회로는 변화를 말할 수 없다"

    _put_hours(hconn, "2026-08-17", lunch=("11:30", "14:00"))
    assert repo.hours_changes(hconn, FID) == [], "안 바뀌면 변화 없음"

    # 개강 후 석식이 생겼다 → 이 시간표가 학기 의존적이라는 게 관측됐다
    _put_hours(hconn, "2026-09-07", lunch=("11:30", "14:00"),
               dinner=("17:30", "19:00"))
    ch = repo.hours_changes(hconn, FID)
    assert len(ch) == 1
    assert ch[0]["from_date"] == "2026-08-17" and ch[0]["to_date"] == "2026-09-07"
    assert any(x[2] == "dinner" for x in ch[0]["added"])


def test_hours_drift_리포트(hconn):
    _put_hours(hconn, "2026-08-10")
    _put_hours(hconn, "2026-09-07", dinner=("17:30", "19:00"))
    report = sched.hours_drift(hconn)
    row = next(r for r in report if r["facility_id"] == FID)
    assert row["observations"] == 2
    assert row["first"] == "2026-08-10" and row["last"] == "2026-09-07"
    assert row["changes"]


# ═══════════════════════════════════════════════════════════════
# 실사이트 스모크 (T5b) — 기본 실행에서 제외
# ═══════════════════════════════════════════════════════════════

@pytest.mark.smoke
def test_smoke_실사이트_2회_연속_fetch가_unchanged(tmp_path):
    """시간·세션 의존 결함은 픽스처가 원리적으로 못 잡는다.

    픽스처는 바이트가 고정이라 T5 가 영원히 초록불인데
    실전에서는 캐시버스터 때문에 영원히 빨간불일 수 있다.
    """
    from crawler import fetch as fetch_mod
    url = "https://likehome.jbnu.ac.kr/home/main/inner.php"
    a = fetch_mod.fetch("likehome_week_menu", url, params={"sMenu": "B7100"})
    b = fetch_mod.fetch("likehome_week_menu", url, params={"sMenu": "B7100"})
    assert a.http_status == 200 and b.http_status == 200
    assert a.stable_hash == b.stable_hash, (
        f"정규화 후에도 해시가 다르다 — 새 변동 요소가 생겼다. "
        f"content_hash 동일 여부={a.content_hash == b.content_hash}")


@pytest.mark.smoke
def test_smoke_생협_API가_여전히_같은_스키마(tmp_path):
    from crawler import fetch as fetch_mod
    from crawler.parsers import coop_week_menu as coop
    r = fetch_mod.fetch("coop_week_menu",
                        "https://coopjbnu.kr/function/get_cafeteria_menu.php",
                        method="POST", params={"date": "20260810"},
                        media_type="json")
    assert r.http_status == 200
    parsed = coop.parse(r.text)          # 스키마가 바뀌면 ParseError
    assert parsed.meals or parsed.empty_list
