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
import sqlite3
from typing import Any

from fastapi import Depends, FastAPI, Request

from skill import auth, branch, kakao, safety, templates
from store import repo

ROOT = pathlib.Path(__file__).resolve().parents[1]
_DATA = pathlib.Path(os.environ.get("JBNU_DATA_DIR", str(ROOT / "data")))
DB_PATH = pathlib.Path(os.environ.get("JBNU_DB_PATH", str(_DATA / "jbnu.db")))

KST = dt.timezone(dt.timedelta(hours=9))

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
    app = FastAPI(title="전북대 총학 챗봇 스킬서버")
    app.state.db_path = db_path or DB_PATH
    app.state.scheduler = None

    def conn() -> sqlite3.Connection:
        return repo.connect(app.state.db_path)

    # ★ 스케줄러를 웹 서비스 안에서 돌린다.
    #   Render 는 Cron Job 에 디스크를 못 붙여서 별도 서비스가 같은 SQLite 를 못 본다.
    if with_scheduler if with_scheduler is not None else _scheduler_enabled():
        from contextlib import asynccontextmanager

        from crawler import loop as loop_mod
        app.state.scheduler = loop_mod.SchedulerLoop()

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
            return {
                "ok": True, "meal_service": n,
                "now_kst": dt.datetime.now(KST).isoformat(),
                "scheduler": None if s is None else s.status(),
            }
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

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
        now = dt.datetime.now(KST)
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

    @app.post("/skill/{block_name}", dependencies=[Depends(auth.require_token)])
    async def skill(block_name: str, request: Request) -> dict:
        payload = await request.json()
        return handle(app.state.db_path, block_name, payload)

    return app


# ═══════════════════════════════════════════════════════════════
# 처리 본체 (FastAPI 없이도 테스트할 수 있게 분리)
# ═══════════════════════════════════════════════════════════════

def handle(db_path: pathlib.Path, block_name: str, payload: dict,
           *, now: dt.datetime | None = None) -> dict:
    utterance = (payload.get("userRequest") or {}).get("utterance", "")

    # ── 1. 안전 분기. 인텐트 분류보다 먼저. 절대 뒤로 옮기지 말 것 ──
    if safety.is_sensitive(utterance):
        return safety.response(utterance)

    # ── 2. 오픈빌더가 추출한 파라미터 ──
    params = (payload.get("action") or {}).get("params") or {}
    detail = (payload.get("action") or {}).get("detailParams") or {}

    if block_name in ("food.menu.today", "food.menu"):
        return _handle_meal(db_path, params, detail, now=now)
    return templates.render_fallback()


def _handle_meal(db_path: pathlib.Path, params: dict, detail: dict,
                 *, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(KST)

    facility_id = _resolve_facility(params)
    if facility_id is None:
        return templates.render_fallback()

    date = _resolve_date(params, detail, now)
    meal_type = _resolve_meal_type(params, now)

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


def _resolve_facility(params: dict) -> str | None:
    raw = (params.get("outlet") or params.get("facility")
           or params.get("outlet_name") or "")
    raw = str(raw).strip()
    if raw in FACILITY_BY_ALIAS:
        return FACILITY_BY_ALIAS[raw]
    if raw in FACILITY_NAME:
        return raw
    return None


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


def _resolve_meal_type(params: dict, now: dt.datetime) -> str:
    raw = str(params.get("meal_type") or "").strip()
    mapped = repo.MEAL_TYPE_FROM_SOURCE.get(raw)
    if mapped:
        return mapped
    if raw in ("breakfast", "lunch", "dinner"):
        return raw
    # 지정이 없으면 시각으로 정한다. 추측이 아니라 관례적 기본값이다.
    h = now.hour
    if h < 10:
        return "breakfast"
    return "lunch" if h < 15 else "dinner"


app = create_app()
