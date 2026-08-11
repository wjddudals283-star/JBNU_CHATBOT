"""카카오 스킬 서버.

    POST /skill/{block_name}      블록별 엔드포인트
    GET  /health
    GET  /admin/freshness         소스별 마지막 성공 크롤, stale 여부

★ 처리 순서가 중요하다 (02 §4)
    1. 안전 분기 — 인텐트 분류보다 먼저. 절대 뒤로 옮기지 말 것 (T13)
    2. 오픈빌더가 추출한 params 사용 (자체 NLU 없음)
    3. 온톨로지 조회 + 게이트
    4. 템플릿 렌더

★ 핸들러 안에서 크롤링·외부 API 호출 금지. DB 조회만 한다.
  응답이 늦으면 카카오가 폴백 처리한다 (T7: p95 < 300ms).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import pathlib
import re
import sqlite3
from typing import Any

from fastapi import Depends, FastAPI, Request

from skill import (aliases, auth, branch, calendar_search, ingest_api, kakao,
                   manual_answers, section_search,
                   routing, safety, templates)
from store import repo

log = logging.getLogger("jbnu.skill")

ROOT = pathlib.Path(__file__).resolve().parents[1]
_DATA = pathlib.Path(os.environ.get("JBNU_DATA_DIR", str(ROOT / "data")))
DB_PATH = pathlib.Path(os.environ.get("JBNU_DB_PATH", str(_DATA / "jbnu.db")))

KST = dt.timezone(dt.timedelta(hours=9))


def now_kst() -> dt.datetime:
    """현재 시각을 읽는 **유일한** 자리.

    ★ 시계를 여러 곳에서 읽으면 테스트가 달력에 따라 깨진다.
      실제로 자정을 넘기면서 세 개가 깨졌다 — 픽스처는 날짜가 박혀 있는데
      엔드포인트는 진짜 시계를 봤기 때문이다.
      한 군데로 모으면 테스트가 시각을 갈아끼울 수 있다.
    """
    return dt.datetime.now(KST)

# 별칭 → facility. 오픈빌더 엔티티가 canonical 값을 주지만, 방어적으로 매핑한다.
FACILITY_BY_ALIAS = {
    "후생관": "jbnu:facility/후생관-푸드코트",
    "후생": "jbnu:facility/후생관-푸드코트",
    "푸드코트": "jbnu:facility/후생관-푸드코트",
    "공대식당": "jbnu:facility/후생관-푸드코트",
    "진수원": "jbnu:facility/진수원",
    "진수당": "jbnu:facility/진수원",
    "의대식당": "jbnu:facility/의대식당",
    "의대": "jbnu:facility/의대식당",
    "생활관": "jbnu:facility/생활관-식당",
    "기숙사": "jbnu:facility/생활관-식당",
}

FACILITY_NAME = {
    "jbnu:facility/후생관-푸드코트": "후생관",
    "jbnu:facility/진수원": "진수원",
    "jbnu:facility/의대식당": "의대식당",
    "jbnu:facility/생활관-식당": "생활관 식당",
}

SOURCE_URL = {
    "jbnu:facility/후생관-푸드코트": "https://coopjbnu.kr/menu/week_menu.php",
    "jbnu:facility/진수원": "https://coopjbnu.kr/menu/week_menu.php",
    "jbnu:facility/의대식당": "https://coopjbnu.kr/menu/week_menu.php",
    "jbnu:facility/생활관-식당":
        "https://likehome.jbnu.ac.kr/home/main/inner.php?sMenu=B7100",
}

CONTACT = {
    "jbnu:facility/후생관-푸드코트": None,   # 생협 번호는 미확인이므로 넣지 않는다
}


def _scheduler_enabled() -> bool:
    return os.environ.get("RUN_SCHEDULER", "").strip() in ("1", "true", "yes")


def _configure_logging() -> None:
    """컨테이너 로그로 나가게 한다.

    ★ print() 만으로는 안 된다. 컨테이너에서 stdout 은 블록 버퍼링이라
      백그라운드 스레드의 출력이 버퍼가 찰 때까지 안 보인다.
      logging 은 stderr 로 나가고 uvicorn 이 잡아준다.
      (render.yaml 에 PYTHONUNBUFFERED=1 도 같이 걸어둔다)
    """
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    logging.getLogger("jbnu").setLevel(logging.INFO)


def create_app(db_path: pathlib.Path | None = None, *,
               with_scheduler: bool | None = None) -> FastAPI:
    _configure_logging()
    # ★ 자동 문서를 끈다. /openapi.json 은 엔드포인트 구조와 docstring 을 그대로
    #   노출한다. /health 를 축소한 것과 같은 이유 — 공개될 필요가 없는 운영 정보다.
    app = FastAPI(title="전북대 총학 챗봇 스킬서버",
                  openapi_url=None, docs_url=None, redoc_url=None)
    app.state.db_path = db_path or DB_PATH
    app.state.scheduler = None

    def conn() -> sqlite3.Connection:
        return repo.connect(app.state.db_path)

    # ★ 스케줄러를 웹 서비스 안에서 돌린다.
    #   Render 는 Cron Job 에 디스크를 못 붙여서 별도 서비스가 같은 SQLite 를 못 본다.
    if with_scheduler if with_scheduler is not None else _scheduler_enabled():
        from contextlib import asynccontextmanager

        from crawler import loop as loop_mod
        # ★ 스모크는 with_scheduler 플래그가 아니라 **환경변수**를 따른다.
        #   테스트는 with_scheduler=True 를 명시적으로 주지만 RUN_SCHEDULER 는 없다.
        #   플래그에 묶으면 TestClient 를 열 때마다 실사이트를 두드리게 된다.
        #   프로덕션(RUN_SCHEDULER=1)에서만 켜져야 한다.
        app.state.scheduler = loop_mod.SchedulerLoop(smoke=_scheduler_enabled())

        @asynccontextmanager
        async def lifespan(_app: FastAPI):
            app.state.scheduler.start()
            try:
                yield
            finally:
                app.state.scheduler.stop()

        app.router.lifespan_context = lifespan

    @app.get("/health")
    def health() -> dict:
        """공개. Render 헬스체크가 부른다.

        ★ 여기에는 운영 정보를 담지 않는다. 살아 있다는 사실만 알린다.
          상세는 /admin/status (인증 필요).
        """
        return {"ok": True}

    @app.get("/admin/status", dependencies=[Depends(auth.require_token)])
    def status() -> dict:
        try:
            c = conn()
            n = c.execute("SELECT COUNT(*) c FROM meal_service").fetchone()["c"]
            c.close()
            s = app.state.scheduler
            now = now_kst()
            from crawler import schedule as sched_mod
            c2 = conn()
            try:
                fresh = sched_mod.source_freshness(
                    c2, sched_mod.load_schedule(), now)
            finally:
                c2.close()
            return {
                "ok": True, "meal_service": n,
                "now_kst": now.isoformat(),
                "scheduler": None if s is None else s.status(),
                # 원천별 마지막 성공. 백필이 조용히 멈춘 걸 여기서 잡는다.
                "sources": fresh,
                "stale_sources": [f["source_key"] for f in fresh if f["stale"]],
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    @app.post("/admin/ingest", dependencies=[Depends(auth.require_token)])
    def push_ingest(payload: ingest_api.IngestPayload) -> dict:
        """노트북(한국 IP)이 받아온 원문을 서버 DB 로 밀어넣는다.

        ★ 파싱된 레코드가 아니라 **원문 바이트**를 받아 서버가 다시 파싱한다.
          밖에서 들어온 데이터를 그대로 믿지 않는다 — 게이트를 처음부터 다시 통과시킨다.
        """
        return ingest_api.handle_ingest(app.state.db_path, payload)

    @app.get("/admin/smoke", dependencies=[Depends(auth.require_token)])
    def smoke(date: str | None = None) -> dict:
        """실사이트 스모크를 **이 서버의 네트워크 위치에서** 돌린다.

        로컬 스모크는 네트워크 위치를 고정한 반쪽 검증이라
        '한국에서는 200, 해외에서는 403' 같은 위치 의존 결함을 못 잡는다.
        """
        from crawler import smoke as smoke_mod
        return smoke_mod.run(date)

    # ★ 크롤 상태·소스 목록이 그대로 나가므로 반드시 인증 뒤에 둔다.
    @app.get("/admin/freshness", dependencies=[Depends(auth.require_token)])
    def freshness() -> dict:
        c = conn()
        rows = c.execute(
            """SELECT source_key,
                      MAX(CASE WHEN outcome IN ('success','unchanged')
                               THEN started_at END) AS last_ok,
                      MAX(started_at) AS last_run
                 FROM crawl_run GROUP BY source_key"""
        ).fetchall()
        now = now_kst()
        out = []
        for r in rows:
            age = (repo.staleness_hours(r["last_ok"], now)
                   if r["last_ok"] else None)
            out.append({
                "source_key": r["source_key"],
                "last_success": r["last_ok"],
                "last_run": r["last_run"],
                "age_hours": round(age, 1) if age is not None else None,
                # 하트비트: 24시간 성공 크롤이 없으면 경보. 침묵이 가장 위험하다.
                "stale": age is None or age > 24,
            })
        c.close()
        return {"sources": out,
                "any_stale": any(x["stale"] for x in out) if out else True}

    # ★ 페이지 커버리지. 어디를 못 읽고 있는지 여기 하나로 본다.
    #   1000페이지가 되면 사람이 못 따라간다 — 그래서 표가 필요하다.
    #   URL·제목이 그대로 나가므로 인증 뒤에 둔다.
    @app.get("/admin/coverage", dependencies=[Depends(auth.require_token)])
    def coverage(limit: int = 50) -> dict:
        c = conn()
        try:
            summary = repo.coverage_summary(c)
            gaps = repo.coverage_gaps(c, limit=limit)
            # ★ 전체 평균은 학생이 겪는 것을 말해주지 않는다.
            #   수요가 높은데 커버가 낮은 도메인이 진짜 구멍이다.
            by_domain = repo.coverage_by_domain(c)
            weighted = repo.demand_weighted_coverage(by_domain)
            # ★ 완주했는지 중단됐는지. '도는 중' 과 '죽었음' 이 같은 모양이면 안 된다.
            progress = repo.crawl_progress(c, "jbnu_pages",
                                           total_hint=summary["total_pages"])
        finally:
            c.close()
        # '왜 못 하는가' 를 상태별로 갈라 준다. 다 '모른다' 로 뭉치면 고칠 수 없다.
        meaning = {
            "ok": "인용 가능",
            "empty": "파싱은 됐으나 원문에 본문이 없음 (스크립트로 그리는 페이지)",
            "parse_error": "구조가 안 맞아 못 읽음 — 파서 대응 필요",
            "fetch_error": "가져오지 못함 (차단·타임아웃)",
            "blocked": "정책상 긁지 않음 — 못 한 게 아니라 안 한 것",
            "not_attempted": "발견만 하고 아직 시도 안 함",
            "skipped": "대상 아님",
        }
        return {**summary, **weighted, "last_crawl": progress,
                "by_domain": by_domain, "status_meaning": meaning,
                "gaps": gaps}

    # ★ 총학이 직접 넣은 답의 상태. 만료·미확인을 여기서 본다.
    #   만료된 뒤에 아는 것은 늦으므로 30일 전에 미리 알린다.
    @app.get("/admin/manual", dependencies=[Depends(auth.require_token)])
    def manual() -> dict:
        return manual_answers.report()

    # ★ 단일 진입점. 오픈빌더에 스킬을 **하나만** 등록하면 되고,
    #   블록을 추가할 때 스킬 재등록·토큰 재입력이 필요 없다.
    @app.post("/skill", dependencies=[Depends(auth.require_token)])
    async def skill_single(request: Request) -> dict:
        payload = await request.json()
        return handle(app.state.db_path, None, payload)

    # 기존 경로 유지 — 이미 등록한 스킬이 계속 동작한다 (마이그레이션 불필요)
    @app.post("/skill/{block_name}", dependencies=[Depends(auth.require_token)])
    async def skill(block_name: str, request: Request) -> dict:
        payload = await request.json()
        return handle(app.state.db_path, block_name, payload)

    @app.get("/admin/blocks", dependencies=[Depends(auth.require_token)])
    def blocks() -> dict:
        """등록된 블록 매핑 + **매핑 안 된 블록**.

        새 블록을 만든 뒤 답이 폴백으로 나오면 여기를 보면 된다.
        어떤 이름으로 들어왔는지 그대로 나온다.
        """
        doc = routing.load()
        return {
            "handlers": doc.get("handlers") or {},
            "ids": doc.get("ids") or {},
            "unmapped": routing.unmapped_blocks(),
            "hint": "config/blocks.yaml 의 handlers 에 이름을 추가하고 배포하면 된다",
        }

    return app


# ═══════════════════════════════════════════════════════════════
# 처리 본체 (FastAPI 없이도 테스트할 수 있게 분리)
# ═══════════════════════════════════════════════════════════════

def handle(db_path: pathlib.Path, block_name: str | None, payload: dict,
           *, now: dt.datetime | None = None) -> dict:
    utterance = (payload.get("userRequest") or {}).get("utterance", "")

    # ── 1. 안전 분기. 인텐트 분류보다 먼저. 절대 뒤로 옮기지 말 것 ──
    #    라우팅보다도 먼저다. 어떤 블록으로 들어왔든 민감 발화면 여기서 끝난다.
    if safety.is_sensitive(utterance):
        return safety.response(utterance)

    # ── 2. 블록 라우팅 ──
    handler, via = routing.resolve(payload, path_block=block_name)
    log.info("[skill] block=%r via=%s utterance=%r",
             handler or "-", via, utterance[:40])

    # ── 3. 오픈빌더가 추출한 파라미터 ──
    params = (payload.get("action") or {}).get("params") or {}
    detail = (payload.get("action") or {}).get("detailParams") or {}

    if handler == "food.menu.today":
        return _handle_meal(db_path, params, detail, utterance, now=now)
    if handler == "deadline.upcoming":
        return _handle_upcoming(db_path, params, detail, utterance, now=now)
    # ★ 총학이 직접 확인한 답이 먼저다.
    #   홈페이지에 없는 것을 사람이 확인해 넣은 것이므로 크롤보다 근거가 세다.
    #   확인 안 됐거나 만료된 항목은 여기서 걸러져 검색으로 넘어간다.
    manual = manual_answers.find(utterance)
    if manual is not None:
        log.info("[skill] manual key=%s verified_at=%s", manual.key,
                 manual.verified_at)
        return templates.render_manual(manual, utterance=utterance)

    if handler == "info.search":
        return _handle_section(db_path, utterance)
    if handler == "notice.search":
        return _handle_notice(db_path, utterance)

    # ★ 매핑된 블록이 없어도 **먼저 찾아본다.**
    #   모아둔 안내가 있는데 폴백을 내보내면, 아는 것을 모른다고 하는 것이 된다.
    #   찾아도 안 나오면 그때 폴백이다 — 조회 여부를 답에 밝힌다.
    answered = _handle_section(db_path, utterance, only_confident=True)
    return answered if answered is not None else templates.render_fallback()


def _handle_notice(db_path: pathlib.Path, utterance: str) -> dict:
    """공지 검색 — 제목·게시일·링크만.

    본문을 안 읽었으므로 내용을 아는 척하지 않는다.
    '이런 공지가 있고 여기서 볼 수 있다' 까지가 우리가 아는 전부다.
    """
    conn = repo.connect(db_path)
    try:
        result = section_search.search_notices(conn, utterance, repo=repo)
    finally:
        conn.close()
    log.info("[skill] notice tokens=%s outcome=%s hits=%s pool=%s",
             result.query_tokens, result.outcome.value, len(result.hits),
             result.searched_total)
    return templates.render_notices(result, utterance=utterance)


def _handle_section(db_path: pathlib.Path, utterance: str, *,
                    only_confident: bool = False) -> dict | None:
    """안내 페이지 인용 — 잎으로 찾고 부모 블록을 그대로 옮긴다.

    only_confident 는 **폴백 경로**에서 쓴다. 매핑된 블록이 없어 여기까지 온
    발화에는 찾았을 때만 답하고, 못 찾으면 폴백에 넘긴다.
    못 찾은 걸 여기서 받아치면 인사말에도 '안내를 찾지 못했어요' 가 나간다.
    """
    conn = repo.connect(db_path)
    try:
        result = section_search.search(conn, utterance, repo=repo)
    finally:
        conn.close()

    log.info("[skill] section tokens=%s outcome=%s hits=%s pool=%s confident_only=%s",
             result.query_tokens, result.outcome.value, len(result.hits),
             result.searched_sections, only_confident)
    # ★ 폴백에서는 **확실할 때만** 답한다.
    #   애매한 결과까지 받아치면 "안녕하세요" 에 총장 연설문 목록이 나간다.
    #   실제로 그랬다. 인사말은 검색어가 아니다.
    #
    #   수집 전(NO_DATA)에도 폴백으로 넘긴다. 폴백이 이미
    #   "아직 준비되지 않았어요 + 지금 되는 것들" 을 알려주므로,
    #   여기서 가로채면 **대안 없는 답**으로 바꾸는 셈이 된다.
    if only_confident and result.outcome is not section_search.Outcome.FOUND:
        return None
    return templates.render_section(result, utterance=utterance)


SCHEDULE_URL = "https://www.jbnu.ac.kr/web/academic/schedule.do"
UPCOMING_DEFAULT_DAYS = 14


def _handle_upcoming(db_path: pathlib.Path, params: dict, detail: dict,
                     utterance: str = "", *,
                     now: dt.datetime | None = None) -> dict:
    now = now or now_kst()
    today = now.date().isoformat()

    # ★ 한 블록에 두 종류 발화가 섞여 들어온다.
    #   "마감 뭐 있어"(목록)와 "수강신청 언제야"(특정 조회)는 다른 질문이다.
    #   학생은 블록 경계를 모르므로 서버가 가른다.
    topic = calendar_search.find_topic(utterance)
    if topic is not None:
        return _handle_calendar_item(db_path, utterance, now=now)

    days = _resolve_days(params, utterance)
    until = (now.date() + dt.timedelta(days=days)).isoformat()

    conn = repo.connect(db_path)
    try:
        rows = repo.query_calendar(conn, since=today, until=until)
        # 신선도 — 학사일정은 자주 안 바뀌지만 크롤이 멈춘 걸 숨기면 안 된다.
        observed = max((r["observed_at"] for r in rows), default=None)
        stale = bool(rows) and repo.staleness_hours(observed, now) > \
            repo.MAX_STALENESS_HOURS["academic_calendar"]
        log.info("[skill] upcoming days=%s rows=%s stale=%s", days, len(rows), stale)
        return templates.render_upcoming(
            rows, today=today, days=days, source_url=SCHEDULE_URL,
            observed_at=observed, stale=stale)
    finally:
        conn.close()


# 항목 검색은 넓게 조회한다. 14일치만 보고 '없다'고 하면
# **있는 걸 없다고** 하는 오류가 된다.
ITEM_SEARCH_BACK_DAYS = 200
ITEM_SEARCH_AHEAD_DAYS = 400


def _handle_calendar_item(db_path: pathlib.Path, utterance: str, *,
                          now: dt.datetime) -> dict:
    today = now.date()
    since = (today - dt.timedelta(days=ITEM_SEARCH_BACK_DAYS)).isoformat()
    until = (today + dt.timedelta(days=ITEM_SEARCH_AHEAD_DAYS)).isoformat()

    conn = repo.connect(db_path)
    try:
        rows = repo.query_calendar(conn, since=since, until=until, limit=500)
    finally:
        conn.close()

    result = calendar_search.search(rows, utterance)
    result.entries = calendar_search.rank(result.entries, today.isoformat())

    observed = max((r["observed_at"] for r in rows), default=None)
    stale = bool(rows) and repo.staleness_hours(observed, now) > \
        repo.MAX_STALENESS_HOURS["academic_calendar"]

    log.info("[skill] calendar topic=%s outcome=%s hits=%s searched=%s stale=%s",
             result.topic.key if result.topic else "-", result.outcome.value,
             len(result.entries), result.searched_total, stale)

    return templates.render_calendar_item(
        result, today=today.isoformat(), source_url=SCHEDULE_URL,
        observed_at=observed, stale=stale)


_DAYS_RE = re.compile(r"(\d+)\s*일")


def _resolve_days(params: dict, utterance: str) -> int:
    raw = str(params.get("days") or "").strip()
    if raw.isdigit():
        return max(1, min(int(raw), 90))
    m = _DAYS_RE.search(utterance or "")
    if m:
        return max(1, min(int(m.group(1)), 90))
    if "이번 달" in (utterance or "") or "이번달" in (utterance or ""):
        return 31
    return UPCOMING_DEFAULT_DAYS


def _handle_meal(db_path: pathlib.Path, params: dict, detail: dict,
                 utterance: str = "", *, now: dt.datetime | None = None) -> dict:
    now = now or now_kst()

    # ★ params 가 비면 발화에서 보완한다.
    #   오픈빌더는 발화마다 태깅해야 params 를 주는데, 학생 자유 발화는
    #   태깅이 안 되므로 params 가 계속 빈다. 인텐트 분류는 여전히 오픈빌더가 하고
    #   우리는 **이미 매칭된 블록 안에서** 슬롯만 채운다.
    facility_id, source = aliases.resolve_facility(params, utterance)
    log.info("[skill] facility=%s via=%s utterance=%r",
             facility_id or "-", source, utterance[:40])

    date = _resolve_date(params, detail, now)
    meal_type = _resolve_meal_type(params, now, utterance)

    if facility_id is None:
        # "오늘 학식"처럼 식당을 안 말하는 발화. 폴백 대신 전체 식당을 보여준다.
        return _handle_overview(db_path, date=date, meal_type=meal_type, now=now)

    # ── 3. 온톨로지 조회 + 게이트 ──
    conn = repo.connect(db_path)
    try:
        answer = branch.resolve_meal(conn, facility_id=facility_id, date=date,
                                     meal_type=meal_type, now=now)
        has_prices = False
        if answer.branch is branch.Branch.A:
            repo.attach_prices(conn, answer.rows, facility_id=facility_id,
                               on_date=date)
            has_prices = repo.has_price_table(conn, facility_id)
        # ── 4. 템플릿 렌더 ──
        return templates.render_meal(
            answer, facility_name=FACILITY_NAME.get(facility_id, "식당"),
            date=date, meal_type=meal_type,
            source_url=SOURCE_URL.get(facility_id, ""),
            contact=CONTACT.get(facility_id),
            has_price_table=has_prices,
        )
    finally:
        conn.close()


def _handle_overview(db_path: pathlib.Path, *, date: str, meal_type: str,
                     now: dt.datetime) -> dict:
    """식당을 안 말한 발화 — 지금 운영 중인 곳을 한 장으로 보여준다.

    폴백("답변할 자료가 준비되지 않았어요")으로 보내면 안 된다.
    자료는 있고 어느 식당인지만 모르는 상태다.
    """
    conn = repo.connect(db_path)
    try:
        rows = []
        for fid in aliases.all_facility_ids():
            answer = branch.resolve_meal(conn, facility_id=fid, date=date,
                                         meal_type=meal_type, now=now)
            rows.append((fid, aliases.canonical_name(fid), answer))
        return templates.render_overview(rows, date=date, meal_type=meal_type)
    finally:
        conn.close()


def _resolve_facility(params: dict, utterance: str = "") -> str | None:
    fid, _ = aliases.resolve_facility(params, utterance)
    return fid


def _resolve_date(params: dict, detail: dict, now: dt.datetime) -> str:
    """`sys.date` 는 JSON 문자열로 온다 — json.loads 후 date 키를 쓴다."""
    node = detail.get("date") or detail.get("sys_date")
    if isinstance(node, dict):
        value = node.get("value")
        if isinstance(value, str) and value.startswith("{"):
            try:
                return json.loads(value)["date"]
            except (ValueError, KeyError):
                pass
        elif isinstance(value, str) and len(value) == 10:
            return value
    raw = params.get("date")
    if isinstance(raw, str):
        if raw.startswith("{"):
            try:
                return json.loads(raw)["date"]
            except (ValueError, KeyError):
                pass
        elif len(raw) == 10:
            return raw
    return now.date().isoformat()


def _resolve_meal_type(params: dict, now: dt.datetime, utterance: str = "") -> str:
    raw = str(params.get("meal_type") or "").strip()
    mapped = repo.MEAL_TYPE_FROM_SOURCE.get(raw)
    if mapped:
        return mapped
    if raw in ("breakfast", "lunch", "dinner"):
        return raw
    # params 가 비면 발화에서 찾는다 (식당과 같은 이유)
    from_utt = aliases.find_meal_type(utterance)
    if from_utt:
        return from_utt
    # 지정이 없으면 시각으로 정한다. 추측이 아니라 관례적 기본값이다.
    h = now.hour
    if h < 10:
        return "breakfast"
    return "lunch" if h < 15 else "dinner"


app = create_app()
