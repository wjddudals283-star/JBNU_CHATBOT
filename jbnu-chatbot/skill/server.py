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
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Request

from skill import (aliases, auth, branch, calendar_search, ingest_api, kakao,
                   manual_answers, section_search,
                   clarify, routing, safety, smalltalk, templates)
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

# 원문을 뭐라고 부를지. '낡았다' 고 말할 때 어디로 가라고 해야 하기 때문이다.
SOURCE_NAME = {
    "jbnu:facility/후생관-푸드코트": "생협 식단표",
    "jbnu:facility/진수원": "생협 식단표",
    "jbnu:facility/의대식당": "생협 식단표",
    "jbnu:facility/생활관-식당": "생활관 홈페이지",
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
    app.state.warm = None          # None=아직 · dict=끝남

    def conn() -> sqlite3.Connection:
        return repo.connect(app.state.db_path)

    def _ensure_schema() -> None:
        """새 표가 생겼을 때 **첫 학생이 발견하지 않게** 한다.

        ★ 실제로 터질 뻔했다 (2026-08-14)
          council_post 를 추가하고 배포하면, 서버 DB 에는 그 표가 없다.
          수집이 한 번 돌아야 생기는데 그 전에 학생이 검색하면
          `no such table` 로 **500** 이 난다.
          스키마는 코드와 함께 배포되지만 **DB 는 디스크에 남아 있다** —
          이 어긋남을 기동 때 메운다.

        CREATE TABLE IF NOT EXISTS 뿐이라 여러 번 돌아도 안전하다.
        실패해도 서버는 뜬다 — 답변 경로가 부드럽게 물러서게 해 뒀다.

        ★ **없는 DB 를 만들지는 않는다**
          경로가 틀렸을 때 빈 DB 를 만들어 버리면 /health 의 warm 이 True 가 되고,
          '떴다' 와 '답할 준비가 됐다' 를 가르던 계기판이 무뎌진다.
          그건 설정 문제고, 워밍업이 warm=False 로 말하게 둔다.
          여기서 고치려는 건 **있는 DB 와 새 코드의 어긋남**뿐이다.
        """
        if not pathlib.Path(app.state.db_path).exists():
            log.info("[schema] DB 파일이 없다 — 만들지 않는다 (%s)",
                     app.state.db_path)
            return
        try:
            c = repo.connect(app.state.db_path)
            try:
                out = repo.init_db(c)
                c.commit()
            finally:
                c.close()
            # ★ 무엇을 맞췄는지 남긴다. 안 남기면 '맞췄나' 를 다음 오류로 알게 된다.
            if out.get("added"):
                log.info("[schema] 컬럼 추가 %s", ", ".join(out["added"]))
            if out.get("skipped"):
                # 못 붙인 건 사람이 손으로 옮겨야 한다 — 조용히 넘기지 않는다
                log.error("[schema] ★ 못 붙인 컬럼 %s — 손으로 옮겨야 한다",
                          ", ".join(out["skipped"]))
        except Exception as e:  # noqa: BLE001
            log.error("[schema] 보장 실패 — %s: %s", type(e).__name__, e)

    def _warmup() -> dict:
        """첫 학생이 침묵을 겪지 않게 미리 한 번 돌려 본다.

        ★ 실측한 증상
          배포 직후 첫 요청 5.001초 무응답, 두 번째 0.26초.
          카카오 스킬 타임아웃이 5초라 **첫 요청은 반드시 죽는다.**

        ★ 두 가지가 겹쳐 있었다
          1. 락 대기 — 배포하면 스케줄러가 밀린 수집을 즉시 시작하고,
             그 쓰기를 첫 요청이 기다렸다. 5.000초는 sqlite 기본 busy timeout 이다.
             → 답변 경로를 읽기 전용 연결로 바꿔서 아예 안 기다리게 했다.
          2. 첫 질의 비용 — 모듈 로딩·FTS 인덱스 첫 접근.
             → 여기서 미리 치른다.

        실패해도 서버는 뜬다. 워밍업은 편의지 기동의 조건이 아니다.
        """
        t0 = time.monotonic()
        out: dict = {"ok": False}
        try:
            c = repo.connect(app.state.db_path, readonly=True)
            try:
                out["sections"] = repo.section_total(c)
                # 진짜 질의를 한 번 태운다. COUNT 만으로는 FTS 가 안 데워진다.
                section_search.search(c, "휴학", repo=repo)
                out["ok"] = True
            finally:
                c.close()
        except Exception as e:  # noqa: BLE001
            out["error"] = f"{type(e).__name__}: {e}"[:200]
        out["ms"] = round((time.monotonic() - t0) * 1000)
        app.state.warm = out
        log.info("[warmup] %s (%sms) sections=%s",
                 "ok" if out["ok"] else out.get("error"), out["ms"],
                 out.get("sections"))
        return out

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # ★ 스키마를 먼저 맞춘다. 새 표가 없으면 워밍업 질의부터 깨진다.
        _ensure_schema()
        # ★ 스케줄러보다 **먼저** 데운다.
        #   스케줄러가 기동 즉시 수집을 시작하므로, 순서가 반대면
        #   워밍업 자신이 락을 기다리게 된다.
        _warmup()
        s = app.state.scheduler
        if s is not None:
            s.start()
        try:
            yield
        finally:
            if s is not None:
                s.stop()

    app.router.lifespan_context = lifespan

    # ★ 스케줄러를 웹 서비스 안에서 돌린다.
    #   Render 는 Cron Job 에 디스크를 못 붙여서 별도 서비스가 같은 SQLite 를 못 본다.
    if with_scheduler if with_scheduler is not None else _scheduler_enabled():
        from crawler import loop as loop_mod
        # ★ 스모크는 with_scheduler 플래그가 아니라 **환경변수**를 따른다.
        #   테스트는 with_scheduler=True 를 명시적으로 주지만 RUN_SCHEDULER 는 없다.
        #   플래그에 묶으면 TestClient 를 열 때마다 실사이트를 두드리게 된다.
        #   프로덕션(RUN_SCHEDULER=1)에서만 켜져야 한다.
        app.state.scheduler = loop_mod.SchedulerLoop(smoke=_scheduler_enabled())

    @app.get("/health")
    def health() -> dict:
        """공개. Render 헬스체크가 부른다.

        ★ 여기에는 운영 정보를 담지 않는다. 살아 있다는 사실만 알린다.
          상세는 /admin/status (인증 필요).

        ★ warm 만 예외로 낸다 — 규모가 아니라 상태다
          '떴다' 와 '답할 준비가 됐다' 는 다르다. 배포 확인할 때
          이걸 못 보면 첫 학생이 대신 확인해 주게 된다.
        """
        w = app.state.warm
        return {"ok": True, "warm": bool(w and w.get("ok"))}

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
                "warmup": app.state.warm,
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

    # ★ 서버 코퍼스에 그 낱말이 몇 번 나오나.
    #
    #   2026-08-13: 학교가 OASIS → JUMP 로 갈아탔을 때 우리 사본이 낡았다고 봤는데
    #   그건 **로컬 사본 숫자**였다. 서버는 이미 최신이었고, 확인할 방법이
    #   로그밖에 없어서 하루를 잘못 진단했다.
    #   '로컬 숫자를 서버 상태로 말하지 않는다' 고 적어 놓고도 그랬다 —
    #   **볼 수단이 없으면 규율은 안 지켜진다.**
    @app.get("/admin/terms", dependencies=[Depends(auth.require_token)])
    def terms(q: str = "", hosts: int = 8) -> dict:
        """?q=OASIS,JUMP — 쉼표로 여러 낱말. 최대 5개."""
        words = [w.strip() for w in (q or "").split(",") if w.strip()][:5]
        if not words:
            return {"error": "q 가 필요하다. 예: /admin/terms?q=OASIS,JUMP"}
        c = repo.connect(app.state.db_path, readonly=True)
        try:
            return {"observed_at": now_kst().isoformat(),
                    "results": [repo.term_occurrences(c, w, top_hosts=hosts)
                                for w in words]}
        finally:
            c.close()

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

    @app.post("/admin/route", dependencies=[Depends(auth.require_token)])
    async def route(request: Request) -> dict:
        """이 발화를 **누가 받는가**. 답은 만들지 않는다.

        ★ 응답 모양으로 추측하지 않으려고 만들었다
          배포 서버에 46문항을 넣어 볼 때 '어느 블록이 받았나' 를
          카드 생김새로 짐작하면 그건 관측이 아니라 추측이다.
          handle() 이 쓰는 바로 그 함수를 그대로 부른다.

        ★ DB 를 안 본다 — 순수 판정이라 답변 경로에 락을 걸지 않는다.
        """
        payload = await request.json()
        r, why = route_of(payload, None)
        return {"route": r, "why": why}

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

# 총학을 가리키는 말 · 공지를 가리키는 말. 둘 다 있으면 총학 공지를 묻는 것이다.
_COUNCIL_WHO = re.compile(r"총학|총학생회|학생회")
_COUNCIL_WHAT = re.compile(r"공지|행사|소식|안내문")


def _asks_council(utterance: str) -> bool:
    """'총학' 과 '공지' 가 **떨어져 있어도** 총학 공지를 묻는 것이다.

    ★ 배포본 실측 (2026-08-14)
      '총학생회 공지' 가 국제협력과 비자 안내로 갔다. 별칭 '공지' 가 걸려서다.
      별칭은 **붙어 있는 말**만 잡는다 — '총학 장학금 공지' 는
      '총학 공지' 를 품고 있지 않아서 '장학금' 이 이겼다.

    ★ 없는 걸 학교 공지로 채우면 총학이 낸 것처럼 보인다
      학식에서 '자료 없음' 을 '휴무' 로 말한 것과 같은 종류다.
      총학을 물었으면 총학 자리에서 답하고, 없으면 없다고 한다.
    """
    u = utterance or ""
    return bool(_COUNCIL_WHO.search(u) and _COUNCIL_WHAT.search(u))


# 시간을 묻는 말. ★ 언어 표면이지 학교 관측이 아니다 — 조사·인사말과 같은 칸이다.
_ASKS_WHEN = re.compile(r"언제|며칠|날짜|기간|마감")


def _asks_when(utterance: str) -> bool:
    """'언제' 를 버리고 있었다 (2026-08-14 배포본 실측).

    ★ '수강신청 언제야' 가 안내 검색으로 갔다
      커스텀 메뉴 1번 칸, 학생이 제일 먼저 누르는 자리다.
      검색은 '언제' 를 토큰에서 떨어뜨리고 '수강신청' 만 남겨서
      수강신청 **안내 페이지**의 두 갈래(학점 하한선 · 변경 추가)를 되물었다.
      둘 다 날짜가 아니다. **답이 없는 문서에서 갈래를 만든 것이다.**

      날짜는 학사일정에 있다. 주제 목록은 config 가 갖고 있고
      (_handle_upcoming 이 이미 find_topic 으로 가른다) 우리는 문을 열어주기만 한다.

    ★ 시간어가 없으면 안 바꾼다
      '수강신청 공지' 는 공지를 묻는 말이고 '수강신청 학점 상한' 은 규정을 묻는 말이다.
      45문항 전수로 대조해서 바뀌는 건 두 개뿐인 걸 확인했다.
    """
    return bool(_ASKS_WHEN.search(utterance or ""))


def route_of(payload: dict, block_name: str | None = None) -> tuple[str, str]:
    """이 발화를 **누가 받는가**. (route, why)

    ★ DB 를 안 본다 — 순수 판정이다.
      답을 만들지 않고 '누가 받는지' 만 정한다. 그래서 배포 서버에
      질문을 넣어 볼 때 답변과 별개로 물어볼 수 있다.

    ★ handle() 이 이 함수를 **쓴다**. 복사본이 아니다.
      두 벌로 두면 순서가 갈라지고, 그러면 측정이 실제 동작과 다른 걸 잰다.
      실제로 우리가 제일 자주 틀린 자리가 '재는 것과 도는 것이 다르다' 였다.

    ★ 안전 분기가 맨 앞이라는 순서는 여기서도 그대로다 (T13).
    """
    utterance = (payload.get("userRequest") or {}).get("utterance", "")

    if safety.is_sensitive(utterance):
        return "safety", "민감 발화"
    kind = smalltalk.classify(utterance)
    if kind:
        return "smalltalk", kind
    if routing.is_welcome(payload, path_block=block_name):
        return "welcome", "빈 발화 또는 웰컴 블록"

    handler, via = routing.resolve(payload, path_block=block_name)
    if handler in ("food.menu.today", "deadline.upcoming"):
        return handler, via

    # ★ 총학이 직접 확인한 답이 검색보다 먼저다 — 순서를 여기서도 지킨다.
    manual = manual_answers.find(utterance)
    if manual is not None:
        return "manual", manual.key

    if handler in ("welcome", "info.search", "notice.search",
                   "council.notice", "career.list", "council.event"):
        return handler, via

    if routing.is_fallback(payload, path_block=block_name):
        # ★ 총학을 물었으면 **다른 별칭보다 먼저** 총학 자리로 보낸다.
        #   '총학 장학금 공지' 가 학교 장학금 안내로 가면, 학생은 그걸
        #   총학이 낸 것으로 읽는다. 없으면 없다고 말하는 게 맞다.
        if _asks_council(utterance):
            return "council.notice", "총학+공지"
        guess, why = routing.by_utterance(utterance)
        # ★ 별칭이 아무것도 못 잡았을 때만 본다. 별칭이 이겨야 한다 —
        #   '학사일정' 은 이미 별칭으로 가고, '수강신청 공지' 는 공지로 가야 한다.
        if guess is None and _asks_when(utterance)                 and calendar_search.find_topic(utterance) is not None:
            return "deadline.upcoming", "시간질문+학사일정주제"
        return (guess or "info.search"), f"폴백→{why}"

    # 매핑 안 된 **총학이 만든** 블록. 확신할 때만 답한다.
    return "unmapped", via


def handle(db_path: pathlib.Path, block_name: str | None, payload: dict,
           *, now: dt.datetime | None = None) -> dict:
    utterance = (payload.get("userRequest") or {}).get("utterance", "")
    route, why = route_of(payload, block_name)

    # ── 1. 안전 분기. 인텐트 분류보다 먼저. 절대 뒤로 옮기지 말 것 ──
    #    라우팅보다도 먼저다. 어떤 블록으로 들어왔든 민감 발화면 여기서 끝난다.
    if route == "safety":
        return safety.response(utterance)

    # ── 1-b. 인사·잡담. 질문이 아닌 말을 검색에 태우지 않는다 ──
    #    폴백을 검색으로 연결하면 학생이 아무 말이나 보낼 수 있게 된다.
    #    그게 목적이지만, '오늘 뭐해' 에 '연설문' 을 보여주는 건 아는 척이다.
    #    문장 전체가 일치할 때만 걸린다 — '밥 뭐야' 는 진짜 질문이라 통과한다.
    if route == "smalltalk":
        log.info("[skill] smalltalk=%s utterance=%r", why, utterance[:40])
        return smalltalk.response(why)

    # ── 1-c. 첫 인사. 검색보다 먼저 —— 할 말이 없으면 찾을 것도 없다 ──
    #    ★ 폴백과 헷갈리면 안 된다. 가르는 것은 **발화가 비었는지**다.
    #      오픈빌더 정적 카드가 두 번 저장에 실패해서 스킬로 가져왔다.
    if route == "welcome":
        block = (payload.get("userRequest") or {}).get("block") or {}
        # ★ 카카오가 웰컴 블록에 무슨 이름을 붙이는지 모른다. 실제 값을 남긴다 —
        #   폴백 때 'block 이 아예 안 온다' 를 이렇게 알아냈다.
        log.info("[welcome] block=%r shape=%s utterance=%r",
                 block.get("name"), routing._shape(payload), utterance[:20])
        return templates.render_welcome()

    # ── 2. 블록 라우팅 (route_of 가 이미 정했다) ──
    log.info("[skill] route=%r why=%s utterance=%r", route, why, utterance[:40])

    # ── 3. 오픈빌더가 추출한 파라미터 ──
    params = (payload.get("action") or {}).get("params") or {}
    detail = (payload.get("action") or {}).get("detailParams") or {}

    if route == "food.menu.today":
        return _handle_meal(db_path, params, detail, utterance, now=now)
    if route == "deadline.upcoming":
        return _handle_upcoming(db_path, params, detail, utterance, now=now)
    # ★ 총학이 직접 확인한 답이 먼저다.
    #   홈페이지에 없는 것을 사람이 확인해 넣은 것이므로 크롤보다 근거가 세다.
    #   확인 안 됐거나 만료된 항목은 여기서 걸러져 검색으로 넘어간다.
    if route == "manual":
        manual = manual_answers.find(utterance)
        if manual is not None:
            log.info("[skill] manual key=%s verified_at=%s", manual.key,
                     manual.verified_at)
            return templates.render_manual(manual, utterance=utterance)

    if route == "career.list":
        return _handle_career(db_path, now=now)
    if route == "council.event":
        return _handle_council_category(db_path, "교내행사", "교내 행사", now=now)
    # ★ 총학 공지가 학교 공지 검색보다 **먼저**다.
    #   총학이 직접 넣은 것이라 크롤 결과보다 근거가 세다 —
    #   '학점포기 없음' 과 같은 자리다 (T4).
    if route == "council.notice":
        return _handle_council(db_path, utterance, now=now)
    if route == "notice.search":
        return _handle_notice(db_path, utterance)

    # ★ 카카오가 분류에 실패한 말(폴백)은 검색이 정확히 할 일이다.
    #   오픈빌더 블록의 발화 목록은 몇 개뿐이고 학생은 그대로 말하지 않는다.
    #   '복학 신청' 한 마디가 폴백으로 가서 스킬을 부르지도 못했다 —
    #   6,985페이지를 모아도 이 문이 닫혀 있으면 안 쓰인다.
    #   여기서는 info.search 와 **똑같이** 답한다. 애매하면 애매하다고,
    #   못 찾으면 못 찾았다고 말한다 — 그게 밋밋한 폴백보다 낫다.
    # ★ 카카오가 분류에 실패한 말(폴백)도 route_of 가 이미 갈라 놨다.
    #   '처음으로' 는 우리가 모든 답변에 붙이는 버튼이라 웰컴으로 가야 하고,
    #   식단·공지·일정은 검색이 아니라 각자의 자료를 봐야 한다.
    if route == "welcome":
        return templates.render_welcome()
    if route == "info.search":
        # ★ T4 가 크롤보다 먼저다 — 등급을 말로만 두지 않는다.
        #   학생은 '총학 공지' 라고 안 친다. '댄스제' 라고 친다.
        #   별칭이 걸릴 때만 T4 를 쓰면 등급이 실제로는 안 지켜진다.
        #   ★ 제목이 걸릴 때만이다. 본문까지 보면 흔한 낱말에 끌려간다.
        promoted = _council_by_title(db_path, utterance, now=now)
        if promoted is not None:
            return promoted
        return _handle_section(db_path, utterance)

    # ★ 매핑 안 된 **총학이 만든** 블록은 다르다.
    #   상담 신청 블록이 검색 결과를 뱉으면 조용히 엉뚱한 답이 된다.
    #   확신할 때만 답하고, 아니면 폴백 + 이름을 기록한다.
    answered = _handle_section(db_path, utterance, only_confident=True)
    return answered if answered is not None else templates.render_fallback()


def _handle_council_category(db_path: pathlib.Path, category: str,
                             label: str, *,
                             now: dt.datetime | None = None) -> dict:
    """시트 분류로 뽑는다 — **사람이 적은 것만.**

    ★ 학교 공지를 섞지 않는다
      '교내행사' 낱말 목록을 우리가 지어내면 그게 추측이다
      (laws.jbnu.ac.kr · 교내공지에서 이미 틀렸다).
      총학이 분류를 적은 글만 낸다. 없으면 없다고 한다.
    """
    now = now or now_kst()
    today = now.date().isoformat()
    conn = repo.connect(db_path, readonly=True)
    try:
        posts = repo.council_by_category(conn, category, today=today)
        have_any = repo.council_total(conn)
        row = conn.execute(
            """SELECT MAX(started_at) FROM crawl_run
                WHERE source_key = 'council_sheet' AND outcome = 'success'"""
        ).fetchone()
        last_ok = row[0] if row else None
    finally:
        conn.close()
    stale = (last_ok is None
             or repo.staleness_hours(last_ok, now) > COUNCIL_STALE_HOURS)
    log.info("[council] 분류=%s %s건 stale=%s", category, len(posts), stale)
    if posts:
        return templates.render_council(posts, label=label)
    if have_any and not stale:
        return templates.render_council_none_active(label)
    return templates.render_council_empty(stale=stale)


CAREER_DAYS = 30


def _handle_career(db_path: pathlib.Path, *,
                   now: dt.datetime | None = None) -> dict:
    """취업·비교과 — 최근에 올라온 것 목록.

    ★ 원천 셋을 합치려 했는데 하나가 못 쓴다 (2026-08-14 전수 확인)
      career.jbnu.ac.kr 게시판 43개 중 25개가 **로그인 뒤**에 있다.
      안 긁은 게 아니라 못 긁는다 — 우회할 선이 아니다.
      남은 둘로 간다: 학교 공지(학과·본부) + 총학 시트(분류=취업·비교과).

    ★ 총학 시트가 먼저다 (T4). 그리고 **마감을 아는 건 시트뿐**이다.
    """
    now = now or now_kst()
    today = now.date()
    since = (today - dt.timedelta(days=CAREER_DAYS)).isoformat()
    conn = repo.connect(db_path, readonly=True)
    try:
        council = repo.council_by_category(
            conn, "취업·비교과", today=today.isoformat())
        notices = repo.recent_career_notices(conn, since=since)
    finally:
        conn.close()
    log.info("[career] 총학 %s건 · 학교공지 %s건 (최근 %s일)",
             len(council), len(notices), CAREER_DAYS)
    return templates.render_career(notices, council, days=CAREER_DAYS)


COUNCIL_STALE_HOURS = 24.0
# 총학 자신을 가리키는 말은 제목 검색에서 뺀다 — 모든 글에 걸린다.
COUNCIL_STOP = {"총학", "총학생회", "공지", "행사", "소식", "안내"}


def _council_tokens(utterance: str) -> list[str]:
    return [t for t in re.split(r"[^\w가-힣]+", utterance or "")
            if len(t) >= 2 and t not in COUNCIL_STOP]


def _council_by_title(db_path: pathlib.Path, utterance: str, *,
                      now: dt.datetime | None = None) -> dict | None:
    """제목이 걸리는 총학 공지가 있으면 그걸 답한다. 없으면 None."""
    toks = _council_tokens(utterance)
    if not toks:
        return None
    now = now or now_kst()
    conn = repo.connect(db_path, readonly=True)
    try:
        posts = repo.search_council_titles(
            conn, toks, today=now.date().isoformat())
    finally:
        conn.close()
    if not posts:
        return None
    log.info("[council] 제목 일치로 T4 승격 q=%r n=%s", utterance[:30], len(posts))
    return templates.render_council(posts, utterance=utterance)


def _handle_council(db_path: pathlib.Path, utterance: str, *,
                    now: dt.datetime | None = None) -> dict:
    """총학 공지·행사 (T4).

    ★ 마감이 지난 것은 조회에서 빠진다 (repo.query_council_posts).
      9월에 "8월 25일까지 신청하세요" 가 나가면 크롤 오답보다 나쁘다 —
      총학이 직접 넣은 것이라 학생이 더 믿기 때문이다.

    ★ 비었으면 '없다' 가 아니라 '못 가져왔다' 고 말한다.
      진짜 공지가 없는 건지 시트를 못 읽은 건지 우리는 구별 못 한다.
      구별 못 하는 걸 구별한 척하면 그게 지어내기다. 학식 stale 과 같은 구조.
    """
    now = now or now_kst()
    today = now.date().isoformat()
    conn = repo.connect(db_path, readonly=True)
    try:
        tokens = _council_tokens(utterance)
        posts = (repo.search_council_posts(conn, tokens, today=today)
                 if tokens else [])
        # ★ 구체적으로 물었는데 못 찾았으면 **최근 글로 채우지 않는다.**
        #   '장학금 공지' 에 '댄스제 모집' 을 보여주면 총학이 장학금 공지를
        #   낸 것처럼 읽힌다 — 학교 공지로 대체하지 않는 것과 같은 이유다.
        asked_specific = bool(tokens)
        have_any = repo.council_total(conn)
        if not posts and not asked_specific:
            posts = repo.query_council_posts(conn, today=today)
        # 시트를 마지막으로 읽은 때. 비었을 때 '왜' 를 가르는 데 쓴다.
        row = conn.execute(
            """SELECT MAX(started_at) FROM crawl_run
                WHERE source_key = 'council_sheet' AND outcome = 'success'"""
        ).fetchone()
        last_ok = row[0] if row else None
    finally:
        conn.close()

    stale = (last_ok is None
             or repo.staleness_hours(last_ok, now) > COUNCIL_STALE_HOURS)
    log.info("[council] posts=%s last_ok=%s stale=%s tokens=%s",
             len(posts), last_ok, stale, tokens)
    if not posts:
        if have_any and not stale and not asked_specific:
            # 읽었고 글도 있는데 전부 마감이 지났다 — '못 가져왔다' 가 아니다
            return templates.render_council_none_active()
        if asked_specific and have_any:
            # 시트는 읽었고 글도 있다. 다만 물은 게 없다 — 그 사실을 그대로 말한다.
            return templates.render_council_missing(
                " ".join(tokens)[:20] or utterance[:20])
        return templates.render_council_empty(stale=stale)
    return templates.render_council(posts, utterance=utterance)


def _handle_notice(db_path: pathlib.Path, utterance: str) -> dict:
    """공지 검색 — 제목·게시일·링크만.

    본문을 안 읽었으므로 내용을 아는 척하지 않는다.
    '이런 공지가 있고 여기서 볼 수 있다' 까지가 우리가 아는 전부다.
    """
    conn = repo.connect(db_path, readonly=True)
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
    conn = repo.connect(db_path, readonly=True)
    try:
        result = section_search.search(conn, utterance, repo=repo)
    finally:
        conn.close()

    log.info("[skill] section tokens=%s outcome=%s hits=%s pool=%s confident_only=%s",
             result.query_tokens, result.outcome.value, len(result.hits),
             result.searched_sections, only_confident)
    # ★ 천장에 닿은 횟수를 세면 상한을 얼마로 할지 추측할 필요가 없다.
    if result.candidates_truncated:
        log.warning("[skill] candidates_truncated: %s -> %s tokens=%s",
                    result.candidates_matched, result.candidates_returned,
                    result.query_tokens)
    # ★ 폴백에서는 **확실할 때만** 답한다.
    #   애매한 결과까지 받아치면 "안녕하세요" 에 총장 연설문 목록이 나간다.
    #   실제로 그랬다. 인사말은 검색어가 아니다.
    #
    #   수집 전(NO_DATA)에도 폴백으로 넘긴다. 폴백이 이미
    #   "아직 준비되지 않았어요 + 지금 되는 것들" 을 알려주므로,
    #   여기서 가로채면 **대안 없는 답**으로 바꾸는 셈이 된다.
    if only_confident and result.outcome is not section_search.Outcome.FOUND:
        return None
    # ★ 형식 안내 되묻기 — 답이 학과에 달려 있을 때.
    #   버튼으로는 안 된다. 학과가 60곳이 넘어서 10개 상한에 안 들어가고,
    #   5곳만 보여주면 나머지 학생에게는 틀린 목록이다.
    if getattr(result, "needs_attribute", ""):
        log.info("[clarify] 형식안내 attr=%s q=%r",
                 result.needs_attribute, utterance[:30])
        # ★ 예시 학과를 지어내지 않는다 — 실제로 후보에 오른 학과를 쓴다.
        #   '경영학과' 라고 썼더니 그 이름의 사이트가 없어서 예시가 안 통했다.
        #   우리가 못 찾는 이름을 예시로 주면 학생을 헛걸음시킨다.
        return templates.render_attribute_hint(
            result.subject or utterance, result.needs_attribute,
            example_site=getattr(result.top, "site_name", ""))

    # ★ 되묻기 — 문서가 갈래를 갖고 있고 질문이 아무것도 안 골랐을 때만.
    #   섹션을 확신할 때는 건드리지 않는다. 페이지 단위로 내려간 자리만 대체한다.
    #   링크 주고 알아서 찾으라는 것보다 버튼 한 번이 적은 노력이다.
    if result.outcome is section_search.Outcome.FOUND and result.page_level \
            and result.top is not None:
        # ★ `with repo.connect(...)` 를 쓰면 안 된다 — sqlite3 의 with 는
        #   트랜잭션 컨텍스트지 close 가 아니다. 연결이 샌다.
        _c = repo.connect(db_path, readonly=True)
        try:
            opts = clarify.options(_c, result.top.page_url,
                                   result.query_tokens or [])
        finally:
            _c.close()
        # ★ 순서가 중요하다. 발화가 선택지와 **똑같으면** 누른 것이다 —
        #   already_narrowed 는 한정어 없는 라벨을 일부러 건너뛰므로
        #   ('시험 언제' 오판을 막느라) 그 앞에 둬야 루프가 안 생긴다.
        chosen = clarify.chosen_option(utterance, opts) or \
            clarify.already_narrowed(
                utterance, opts, result.query_tokens or []) or \
            clarify.narrowed_by_qualifier(utterance, opts,
                                          result.query_tokens or [])
        if opts and chosen:
            # ★ 베타의 진짜 데이터 — 어느 선택지가 눌렸나.
            #   등급으로는 원리상 안 잡힌다. 등급은 '얼마나 정확한 답인가' 를 재고
            #   되묻기가 주는 건 '학생이 원한 답인가' 다. 다른 축이다.
            log.info("[clarify] 선택 label=%r", chosen)
            # ★ 고른 제목의 블록을 그대로 준다 — 검색을 한 번 더 돌리지 않는다.
            #   최상위 블록은 is_leaf=0 이라 색인에 없다. 순위로는 영영 못 올라온다.
            #   학생이 우리가 보여준 제목을 고른 것이므로 추론이 없다.
            _c2 = repo.connect(db_path, readonly=True)
            try:
                blk = clarify.exact_block(_c2, result.top.page_url, utterance)
            finally:
                _c2.close()
            if blk:
                log.info("[clarify] 제목 일치 블록 %s자", len(blk["text"]))
                where = " · ".join(x for x in (result.top.site_name,
                                               result.top.page_title) if x)
                return templates.render_chosen(
                    blk["path"].split(">")[-1].strip(), blk["text"],
                    where=where, page_url=result.top.page_url,
                    observed=templates.observed_label(result.top.observed_at))
        elif opts:
            log.info("[clarify] 발동 q=%r opts=%s", utterance[:30], opts)
            where = " · ".join(x for x in (result.top.site_name,
                                           result.top.page_title) if x)
            return templates.render_clarify(
                result.subject or utterance, opts, where=where,
                page_url=result.top.page_url,
                observed=templates.observed_label(result.top.observed_at))

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

    conn = repo.connect(db_path, readonly=True)
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

    conn = repo.connect(db_path, readonly=True)
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
    # ★ 상한은 templates 와 **같은 수**를 쓴다. 버튼 문구가 '앞으로 90일' 인데
    #   서버가 90 을 못 받으면 우리가 붙인 버튼이 거짓말을 한다.
    cap = templates.MAX_UPCOMING_DAYS
    raw = str(params.get("days") or "").strip()
    if raw.isdigit():
        return max(1, min(int(raw), cap))
    m = _DAYS_RE.search(utterance or "")
    if m:
        return max(1, min(int(m.group(1)), cap))
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

    date = _resolve_date(params, detail, now, utterance)
    specified = _meal_specified(params, utterance)
    meal_type = _resolve_meal_type(params, now, utterance)

    if facility_id is None:
        # ★ 식당도 끼니도 안 밝혔으면 **되묻는다.**
        #   네 식당 × 세 끼니를 한 화면에 쏟으면 읽을 수가 없고,
        #   하나를 골라 주면 그건 추측이다.
        if not specified:
            return templates.render_meal_ask(
                [aliases.canonical_name(f) for f in aliases.all_facility_ids()],
                date=date)
        # 끼니는 밝혔다 — 그 끼니로 전체 식당을 보여준다
        return _handle_overview(db_path, date=date, meal_type=meal_type, now=now)

    if not specified:
        # ★ 식당은 정해졌고 끼니를 안 밝혔다 → 조·중·석 **전부**
        conn = repo.connect(db_path, readonly=True)
        try:
            answers = [(m, branch.resolve_meal(conn, facility_id=facility_id,
                                               date=date, meal_type=m, now=now))
                       for m in MEALS_IN_ORDER]
            for _m, a in answers:
                if a.branch is branch.Branch.A:
                    repo.attach_prices(conn, a.rows, facility_id=facility_id,
                                       on_date=date)
        finally:
            conn.close()
        return templates.render_meal_day(
            FACILITY_NAME.get(facility_id, aliases.canonical_name(facility_id)),
            answers, date=date,
            source_url=SOURCE_URL.get(facility_id, ""),
            source_name=SOURCE_NAME.get(facility_id, "원문"))

    # ── 3. 온톨로지 조회 + 게이트 ──
    conn = repo.connect(db_path, readonly=True)
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
            source_name=SOURCE_NAME.get(facility_id, "원문"),
            today=now.date().isoformat(),
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
    conn = repo.connect(db_path, readonly=True)
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


# ★ 언어 표면이지 학교 관측이 아니다 — 하드코딩 금지의 예외다 (조사·인사말과 같은 칸).
_RELATIVE_DAYS = {"그저께": -2, "어제": -1, "오늘": 0, "내일": 1, "낼": 1,
                  "모레": 2, "글피": 3}


def _relative_date(utterance: str, now: dt.datetime) -> str | None:
    """발화에 든 '내일' 같은 말. 없으면 None.

    ★ 우리가 붙인 버튼이 거짓말을 하고 있었다 (2026-08-14, 버튼 전수)
      '내일 메뉴' 를 누르면 messageText 로 '후생관 내일 점심' 이 오는데
      **오늘** 메뉴가 나왔다. 날짜를 발화에서 안 읽었기 때문이다.
      오픈빌더가 sys.date 를 채워 주는 경로만 보고 있었는데,
      버튼은 폴백(블록 없음)으로 들어와서 params 가 비어 있다.

      끼니는 발화에서 보완하면서 날짜는 안 했다 — 같은 자리에 둔다.
    """
    for word, delta in _RELATIVE_DAYS.items():
        if word in utterance:
            return (now.date() + dt.timedelta(days=delta)).isoformat()
    return None


def _resolve_date(params: dict, detail: dict, now: dt.datetime,
                  utterance: str = "") -> str:
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
    return _relative_date(utterance, now) or now.date().isoformat()


MEALS_IN_ORDER = ("breakfast", "lunch", "dinner")


def _meal_specified(params: dict, utterance: str = "") -> bool:
    """학생이 끼니를 **밝혔는가**.

    ★ 안 밝혔으면 우리가 고르지 않는다
      전에는 시각으로 하나를 골랐다 — 새벽에 물으면 조식만 나갔고,
      정작 그날 점심에는 세 식당 다 메뉴가 있었다.
      '지금 시각' 은 학생이 무엇을 궁금해하는지에 대한 근거가 아니다.
    """
    raw = str(params.get("meal_type") or "").strip()
    if raw and (repo.MEAL_TYPE_FROM_SOURCE.get(raw)
                or raw in ("breakfast", "lunch", "dinner")):
        return True
    return bool(aliases.find_meal_type(utterance))


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
