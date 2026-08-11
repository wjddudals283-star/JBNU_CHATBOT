"""스킬서버 3분기 — T6 / T7 / T8 + 처리 순서.

★ 이 파일이 지키는 가장 중요한 것: **안전 분기가 인텐트 분류보다 먼저**라는 순서.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import statistics
import time

import pytest

from crawler import fetch as fetch_mod
from crawler import ingest as ingest_mod
from crawler.parsers import coop_week_menu as coop
from skill import branch, kakao, safety, server, templates
from store import repo

FIX = pathlib.Path(__file__).parent / "fixtures"
WEEK_JSON = (FIX / "coop_week_20260810.json").read_text(encoding="utf-8")
SRC = "https://coopjbnu.kr/menu/week_menu.php"
KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime.fromisoformat("2026-08-10T12:00:00+09:00")


@pytest.fixture()
def db(tmp_path):
    """실데이터를 넣은 파일 DB. 서버는 파일 경로로 연결한다."""
    path = tmp_path / "t.db"
    c = repo.connect(path)
    repo.init_db(c)
    for fid, name in (("jbnu:facility/후생관-푸드코트", "후생관"),
                      ("jbnu:facility/진수원", "진수원"),
                      ("jbnu:facility/의대식당", "의대식당")):
        c.execute("""INSERT OR IGNORE INTO facility
                       (id, name, facility_type, source_url, source_type)
                     VALUES (?,?,?,?,'coop')""", (fid, name, "식당", SRC))
    c.commit()
    res = fetch_mod.make_result("coop_week_menu", SRC, SRC, 200,
                                WEEK_JSON.encode("utf-8"),
                                "2026-08-10T06:00:00+09:00", "json")
    ingest_mod.ingest(c, res, parser=coop.parse, snapshot_dir=tmp_path,
                      extraction_method="json_api")
    c.close()
    return path


def _payload(utterance: str, **params) -> dict:
    return {
        "userRequest": {"utterance": utterance, "user": {"id": "botuser-1"}},
        "action": {"params": params, "detailParams": {}},
        "flow": {"trigger": {"type": "TEXT_INPUT"}},
    }


# ═══════════════════════════════════════════════════════════════
# 처리 순서 — 안전 분기가 먼저
# ═══════════════════════════════════════════════════════════════

def test_안전분기가_인텐트보다_먼저_동작한다(db):
    """식단 블록으로 들어와도, 발화가 민감하면 식단을 조회조차 하지 않는다."""
    r = server.handle(db, "food.menu.today",
                      _payload("밥도 안 넘어가고 죽고싶어", outlet="후생관"),
                      now=NOW)
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "109" in text, "안전 응답이어야 한다"
    assert "메뉴" not in text and "점심" not in text
    assert "listCard" not in r["template"]["outputs"][0]
    assert "quickReplies" not in r["template"], "대화를 이어가지 않는다"


def test_안전분기_응답도_카카오_규격을_지킨다(db):
    r = server.handle(db, "food.menu.today", _payload("성폭력 당했어요"), now=NOW)
    assert kakao.validate(r) == []


# ═══════════════════════════════════════════════════════════════
# 3분기
# ═══════════════════════════════════════════════════════════════

def test_A분기_정상응답(db):
    r = server.handle(db, "food.menu.today",
                      _payload("후생관 점심 뭐야", outlet="후생관", meal_type="점심"),
                      now=NOW)
    assert kakao.validate(r) == []
    card = r["template"]["outputs"][0]["listCard"]
    assert "후생관" in card["header"]["title"]
    assert 1 <= len(card["items"]) <= 5


def test_B분기_원천이_명시한_미운영(db):
    r = server.handle(db, "food.menu.today",
                      _payload("후생관 아침", outlet="후생관", meal_type="조식"),
                      now=NOW)
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "운영하지 않아요" in text
    assert "메뉴가 없" not in text


def test_T6_25시간_지난_데이터는_C분기(db):
    """신선도 게이트. 값이 있어도 오래되면 쓰지 않는다."""
    late = dt.datetime.fromisoformat("2026-08-11T08:00:00+09:00")  # 26시간 후
    r = server.handle(db, "food.menu.today",
                      _payload("후생관 점심", outlet="후생관", meal_type="점심"),
                      now=late)
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "확인하지 못했어요" in text
    assert SRC in text, "원문 링크는 반드시 같이 나간다"


def test_C분기는_추측한_메뉴를_내놓지_않는다(db):
    """미래 날짜 — 원천에 데이터가 없다."""
    r = server.handle(db, "food.menu.today",
                      _payload("후생관 점심", outlet="후생관", meal_type="점심",
                               date="2026-09-20"),
                      now=NOW)
    outs = r["template"]["outputs"][0]
    assert "listCard" not in outs, "메뉴 카드를 만들면 안 된다"
    assert "확인하지 못했어요" in outs["simpleText"]["text"]


def test_모르는_식당은_전체_목록으로_간다(db):
    """★ 폴백이 아니다. 자료는 있고 어느 식당인지만 모르는 상태다.

    '없는식당'은 사전에 없으니 식당 미지정과 같은 취급이 맞다.
    """
    r = server.handle(db, "food.menu.today", _payload("없는식당 메뉴", outlet="없는식당"),
                      now=NOW)
    assert kakao.validate(r) == []
    card = r["template"]["outputs"][0]["listCard"]
    assert "후생관" in [i["title"] for i in card["items"]]


def test_지원하지_않는_블록만_폴백(db):
    r = server.handle(db, "council.pledge.status", _payload("공약 어떻게 됐어"), now=NOW)
    assert "준비되지 않았어요" in r["template"]["outputs"][0]["simpleText"]["text"]


def test_별칭이_같은_식당으로_모인다(db):
    """T10 의 서버 쪽 절반."""
    ids = {server._resolve_facility({"outlet": a})
           for a in ("후생관", "후생", "푸드코트", "공대식당")}
    assert ids == {"jbnu:facility/후생관-푸드코트"}
    assert server._resolve_facility({"outlet": "진수당"}) == "jbnu:facility/진수원"


def test_sys_date_JSON문자열을_해석한다():
    """'금요일' → {"date": "2026-08-14", ...} JSON 문자열로 온다."""
    detail = {"date": {"value": json.dumps(
        {"date": "2026-08-14", "dateType": "specific"}, ensure_ascii=False)}}
    assert server._resolve_date({}, detail, NOW) == "2026-08-14"
    # 파라미터로 직접 온 경우
    assert server._resolve_date({"date": "2026-08-12"}, {}, NOW) == "2026-08-12"
    # 없으면 오늘
    assert server._resolve_date({}, {}, NOW) == "2026-08-10"


def test_끼니_미지정이면_시각으로_정한다():
    morning = dt.datetime.fromisoformat("2026-08-10T08:00:00+09:00")
    noon = dt.datetime.fromisoformat("2026-08-10T12:00:00+09:00")
    evening = dt.datetime.fromisoformat("2026-08-10T18:00:00+09:00")
    assert server._resolve_meal_type({}, morning) == "breakfast"
    assert server._resolve_meal_type({}, noon) == "lunch"
    assert server._resolve_meal_type({}, evening) == "dinner"
    assert server._resolve_meal_type({"meal_type": "석식"}, noon) == "dinner"


# ═══════════════════════════════════════════════════════════════
# T7 — 응답 시간
# ═══════════════════════════════════════════════════════════════

def test_T7_응답시간_p95가_300ms_미만(db):
    """핸들러 안에서 크롤링·외부 호출을 하면 여기서 터진다."""
    payload = _payload("후생관 점심", outlet="후생관", meal_type="점심")
    times = []
    for _ in range(100):
        t0 = time.perf_counter()
        server.handle(db, "food.menu.today", payload, now=NOW)
        times.append((time.perf_counter() - t0) * 1000)
    p95 = statistics.quantiles(times, n=20)[18]
    assert p95 < 300, f"p95={p95:.1f}ms (평균 {statistics.mean(times):.1f}ms)"


# ═══════════════════════════════════════════════════════════════
# T8 — 모든 분기가 규격을 지킨다
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("utterance,params,when", [
    ("후생관 점심", {"outlet": "후생관", "meal_type": "점심"}, NOW),
    ("후생관 아침", {"outlet": "후생관", "meal_type": "조식"}, NOW),
    ("후생관 점심", {"outlet": "후생관", "meal_type": "점심", "date": "2026-09-20"}, NOW),
    ("진수원 점심", {"outlet": "진수원", "meal_type": "점심"}, NOW),
    ("죽고싶어요", {}, NOW),
    ("아무거나", {}, NOW),
])
def test_T8_모든_분기의_응답이_규격을_통과한다(db, utterance, params, when):
    r = server.handle(db, "food.menu.today", _payload(utterance, **params), now=when)
    assert kakao.validate(r) == [], f"{utterance} → {kakao.validate(r)}"
    assert r["version"] == "2.0"


# ═══════════════════════════════════════════════════════════════
# B분기 근거 노출 — caveat 대신 관측
# ═══════════════════════════════════════════════════════════════

def test_B분기는_단서_대신_운영시간_관측을_보여준다(db):
    c = repo.connect(db)
    snap = c.execute("SELECT id, url FROM source_snapshot LIMIT 1").fetchone()
    meta = repo.SourceMeta(source_id=snap["id"], source_url=snap["url"],
                           observed_at="2026-08-10T07:00:00+09:00", confidence=0.95,
                           extraction_method="html_selector", tier="T2",
                           valid_from="2026-08-10")
    for wd in (1, 2, 3, 4, 5):
        repo.upsert_hours(c, facility_id="jbnu:facility/진수원", term="unspecified",
                          weekday=wd, meal_type="lunch", is_closed=False,
                          open_time="11:30", close_time="14:00", meta=meta)
    for wd in (0, 6):
        repo.upsert_hours(c, facility_id="jbnu:facility/진수원", term="unspecified",
                          weekday=wd, meal_type="lunch", is_closed=True, meta=meta)
    repo.set_hours_coverage(c, "jbnu:facility/진수원", "complete")
    c.commit()
    c.close()

    r = server.handle(db, "food.menu.today",
                      _payload("진수원 아침", outlet="진수원", meal_type="조식"),
                      now=NOW)
    text = r["template"]["outputs"][0]["simpleText"]["text"]

    assert "운영하지 않아요" in text
    # 근거를 보여준다 — 학생이 직접 검증할 수 있는 형태로
    assert "11:30–14:00" in text and "평일" in text
    # ★ 단서는 쓰지 않는다. 행동을 안 바꾸는 단서는 소음이고,
    #   소음이 쌓이면 진짜 경고까지 묻힌다.
    for noise in ("학기 구분", "일 수 있", "확실하지", "추정"):
        assert noise not in text, f"단서 문구가 들어갔다: {noise!r}"


def test_주말_미운영과_끼니_미운영은_다른_문장이다(db):
    """자기모순 방지 — 렌더해 보고 발견한 오류.

    일요일이라 닫힌 건데 "점심은 운영하지 않아요"라고 하면서
    평일 점심시간을 근거로 보여주면 학생이 뭘 믿어야 할지 모른다.
    """
    c = repo.connect(db)
    snap = c.execute("SELECT id, url FROM source_snapshot LIMIT 1").fetchone()
    meta = repo.SourceMeta(source_id=snap["id"], source_url=snap["url"],
                           observed_at="2026-08-10T07:00:00+09:00", confidence=0.95,
                           extraction_method="html_selector", tier="T2",
                           valid_from="2026-08-10")
    fid = "jbnu:facility/후생관-푸드코트"
    for wd in (1, 2, 3, 4, 5):
        repo.upsert_hours(c, facility_id=fid, term="unspecified", weekday=wd,
                          meal_type="lunch", is_closed=False,
                          open_time="11:30", close_time="14:00", meta=meta)
    for wd in (0, 6):
        repo.upsert_hours(c, facility_id=fid, term="unspecified", weekday=wd,
                          meal_type="lunch", is_closed=True, meta=meta)
    repo.set_hours_coverage(c, fid, "complete")
    c.commit()
    c.close()

    # 2026-09-20 은 일요일 → 그 날 쉬는 것이지 점심을 아예 안 하는 게 아니다
    r = server.handle(db, "food.menu.today",
                      _payload("후생관 점심", outlet="후생관", meal_type="점심",
                               date="2026-09-20"), now=NOW)
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "일요일" in text
    assert "점심은 운영하지 않아요" not in text, "그 끼니를 아예 안 한다는 뜻이 된다"
    assert "평소 운영시간" in text and "11:30–14:00" in text

    # 아침은 시간표에 아예 없다 → 끼니 자체를 안 하는 경우
    r2 = server.handle(db, "food.menu.today",
                      _payload("후생관 아침", outlet="후생관", meal_type="조식",
                               date="2026-09-21"), now=NOW)   # 월요일
    t2 = r2["template"]["outputs"][0]["simpleText"]["text"]
    assert "아침은 운영하지 않아요" in t2


def test_코너가_없는_원천은_설명을_비워두지_않는다(tmp_path):
    """생활관은 zone/corner 가 ''. description 에 빈 문자열을 넣으면
    카드에 빈 줄이 생겨 망가져 보인다 — 실제 렌더에서 발견했다."""
    from crawler.parsers import likehome_week_menu as likehome
    path = tmp_path / "d.db"
    c = repo.connect(path)
    repo.init_db(c)
    c.execute("""INSERT INTO facility (id, name, facility_type, source_url, source_type)
                 VALUES (?,?,?,?,'dorm')""",
              (likehome.FACILITY_ID, "생활관 식당", "식당", SRC))
    c.commit()
    html = (FIX / "likehome_20260531_week.html").read_text(encoding="utf-8",
                                                           errors="replace")
    res = fetch_mod.make_result("likehome_week_menu", SRC, SRC, 200,
                                html.encode("utf-8"),
                                "2026-06-01T06:00:00+09:00", "html")
    ingest_mod.ingest(c, res, parser=likehome.parse, snapshot_dir=tmp_path)
    c.close()

    when = dt.datetime.fromisoformat("2026-06-01T12:00:00+09:00")
    r = server.handle(path, "food.menu.today",
                      _payload("기숙사 점심", outlet="생활관", meal_type="점심",
                               date="2026-06-01"), now=when)
    card = r["template"]["outputs"][0]["listCard"]
    for it in card["items"]:
        assert it.get("description", None) != "", "빈 description 을 넣지 않는다"
    # 단가표가 없는 시설이므로 '단가표 참고'로 유도하면 안 된다
    joined = " ".join(o.get("simpleText", {}).get("text", "")
                      for o in r["template"]["outputs"])
    assert "단가표" not in joined


def test_원천_미운영_문구가_중복되지_않는다(db):
    """'운영하지 않아요 (운영없음)' 은 같은 말을 두 번 하는 것이다."""
    r = server.handle(db, "food.menu.today",
                      _payload("후생관 아침", outlet="후생관", meal_type="조식"),
                      now=NOW)
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "(운영없음)" not in text


def test_운영시간_요약_포맷():
    hours = [{"meal_type": "lunch", "open_time": "11:30", "close_time": "14:00",
              "is_closed": 0, "weekday": wd, "observed_at": "2026-08-10T07:00:00+09:00"}
             for wd in (1, 2, 3, 4, 5)]
    hours += [{"meal_type": "dinner", "open_time": "17:30", "close_time": "19:00",
               "is_closed": 0, "weekday": wd,
               "observed_at": "2026-08-10T07:00:00+09:00"}
              for wd in (1, 2, 3, 4, 5)]
    hours += [{"meal_type": "lunch", "open_time": None, "close_time": None,
               "is_closed": 1, "weekday": 6,
               "observed_at": "2026-08-10T07:00:00+09:00"}]
    s = templates.hours_summary(hours)
    assert s == "평일 점심 11:30–14:00, 평일 저녁 17:30–19:00"


# ═══════════════════════════════════════════════════════════════
# 하트비트
# ═══════════════════════════════════════════════════════════════

def test_freshness_엔드포인트가_stale을_알린다(db, monkeypatch):
    """하트비트 — 24시간 성공 크롤이 없으면 경보. 침묵이 가장 위험하다."""
    from skill import auth
    token = "freshness-token-0123456789"
    monkeypatch.setenv(auth.TOKEN_ENV, token)
    # 픽스처가 2026-08-10 로 고정이므로 서버 시계도 고정한다.
    monkeypatch.setattr(server, "now_kst", lambda: NOW)
    app = server.create_app(db, with_scheduler=False)
    from fastapi.testclient import TestClient
    client = TestClient(app)
    hdr = {auth.HEADER_NAME: token}

    assert client.get("/health").json()["ok"] is True

    fr = client.get("/admin/freshness", headers=hdr).json()
    by_key = {s["source_key"]: s for s in fr["sources"]}
    assert "coop_week_menu" in by_key
    assert by_key["coop_week_menu"]["stale"] is False, "방금 넣은 크롤은 신선하다"
    assert fr["any_stale"] is False

    # 오래 전 성공 이후로 성공이 없는 소스를 하나 만든다
    c = repo.connect(db)
    repo.start_crawl(c, run_id="old-1", source_key="likehome_week_menu",
                     started_at="2026-08-05T06:00:00+09:00")
    repo.finish_crawl(c, "old-1", outcome="success",
                      finished_at="2026-08-05T06:00:10+09:00")
    c.commit()
    c.close()

    fr2 = client.get("/admin/freshness", headers=hdr).json()
    old = {s["source_key"]: s for s in fr2["sources"]}["likehome_week_menu"]
    assert old["stale"] is True and old["age_hours"] > 24
    assert fr2["any_stale"] is True


def test_서버는_시계를_한_군데서만_읽는다():
    """달력에 따라 깨지는 테스트는 없는 것만 못하다.

    시계를 여러 곳에서 읽으면 테스트가 시각을 갈아끼울 수 없고,
    자정을 넘기는 순간 조용히 깨진다 — 실제로 세 개가 그렇게 깨졌다.
    """
    import re
    src = pathlib.Path(server.__file__).read_text(encoding="utf-8")
    body = src.split("def now_kst()", 1)[1].split("\n\n\n", 1)[1]
    offenders = re.findall(r"dt\.datetime\.now\(", body)
    assert not offenders, (
        f"now_kst() 밖에서 시계를 {len(offenders)}번 읽는다 — "
        "테스트가 시각을 고정할 수 없게 된다")
