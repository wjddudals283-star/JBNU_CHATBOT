"""POST /admin/ingest — 노트북(한국 IP)이 받아온 원문을 서버 DB 로 밀어넣는다.

배경
  생협 API 가 Render(해외 IP)에서 403 이다. 노트북에서 크롤해도 **로컬 DB 에만**
  쌓이면 학생 답변은 하나도 안 좋아진다. 주 2회 노트북을 켜는 대가가
  '나중을 위한 보험'뿐이면 노동 대비 가치가 너무 낮다.
  → 원문을 서버로 밀어넣어 즉시 반영되게 하고, 교차검증도 서버에서 성립시킨다.

★ 설계 — **파싱된 레코드가 아니라 원문 바이트**를 받는다.
  그래야 서버가 파서·검증 게이트·정렬 앵커·교차검증을 **처음부터 다시** 통과시킨다.
  밖에서 들어온 데이터를 그대로 믿지 않는다. 노트북은 네트워크 위치를 빌려주는
  역할만 한다.

★ 출처 메타는 노트북 것을 유지한다.
  observed_at 은 **노트북이 받아온 시각**이다. 서버가 받은 시각으로 바꾸면
  신선도가 실제보다 좋아 보인다.

★ 해시는 서버가 다시 계산한다. 클라이언트가 준 값을 믿지 않는다.
"""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import logging
import pathlib

from fastapi import HTTPException
from pydantic import BaseModel, Field

from crawler import fetch as fetch_mod
from crawler import ingest as ingest_mod
from store import repo

log = logging.getLogger("jbnu.ingest_api")

KST = dt.timezone(dt.timedelta(hours=9))
MAX_BYTES = 5 * 1024 * 1024          # 5MB. 정상 응답은 수십 KB 다
MAX_AGE_DAYS = 30                    # 이보다 오래된 관측은 받지 않는다
MAX_FUTURE_MIN = 60                  # 시계 오차 허용치


class IngestPayload(BaseModel):
    source_key: str
    url: str
    final_url: str | None = None
    http_status: int = 200
    fetched_at: str                  # ★ 노트북이 받아온 시각. 서버 시각이 아니다
    media_type: str = "html"
    content_b64: str = Field(..., description="원문 바이트를 base64 로")
    note: str | None = None


def handle_ingest(db_path: pathlib.Path, payload: IngestPayload,
                  *, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(KST)

    from crawler import run as run_mod
    sources = run_mod.load_sources()
    cfg = sources.get(payload.source_key)
    if cfg is None:
        raise HTTPException(400, f"모르는 source_key: {payload.source_key}")
    parser = run_mod.PARSERS.get(cfg.get("parser"))
    if parser is None:
        raise HTTPException(400, f"파서가 없다: {cfg.get('parser')}")

    try:
        content = base64.b64decode(payload.content_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "content_b64 를 디코드하지 못했다") from None
    if not content:
        raise HTTPException(400, "본문이 비어 있다")
    if len(content) > MAX_BYTES:
        raise HTTPException(413, f"본문이 너무 크다: {len(content):,}B > {MAX_BYTES:,}B")

    observed = _check_time(payload.fetched_at, now)

    # ★ FetchResult 를 서버에서 다시 만든다. 해시는 서버가 계산한다.
    result = fetch_mod.make_result(
        payload.source_key, payload.url, payload.final_url or payload.url,
        payload.http_status, content, observed.isoformat(), payload.media_type)

    conn = repo.connect(db_path)
    try:
        # ★ 평소 크롤과 **완전히 같은 경로**로 흘려보낸다.
        #   파싱 → 검증 게이트 → 정렬 앵커 → upsert 가 전부 다시 걸린다.
        report = ingest_mod.ingest(
            conn, result, parser=parser, snapshot_dir=run_mod.SNAPSHOT_DIR,
            tier=cfg.get("tier", "T1"),
            extraction_method=cfg.get("extraction_method", "html_selector"),
            confidence=float(cfg.get("confidence", 0.95)),
            run_id=f"push/{payload.source_key}/{observed.isoformat()}",
        )
    finally:
        conn.close()

    log.info("[ingest] source=%s outcome=%s parsed=%s quarantined=%s bytes=%s",
             payload.source_key, report.outcome, report.parsed,
             report.quarantined, len(content))

    return {
        "ok": report.outcome in ("success", "unchanged"),
        "source_key": payload.source_key,
        "outcome": report.outcome,
        "observed_at": observed.isoformat(),
        "parsed": report.parsed,
        "quarantined": report.quarantined,
        "hours": report.hours,
        "prices": report.prices,
        "calendar": report.calendar,
        "reasons": report.reasons[:5],
        "error": report.error,
    }


def _check_time(fetched_at: str, now: dt.datetime) -> dt.datetime:
    """관측 시각 검증.

    ★ 미래 시각을 받으면 신선도가 영원히 통과한다. 오래된 값을 받으면
      이미 지난 자료가 최신인 척한다. 둘 다 막는다.
    """
    try:
        d = dt.datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, f"fetched_at 형식 오류: {fetched_at!r}") from None
    if d.tzinfo is None:
        d = d.replace(tzinfo=KST)

    ahead = (d - now).total_seconds() / 60
    if ahead > MAX_FUTURE_MIN:
        raise HTTPException(400, f"fetched_at 이 미래다 ({ahead:.0f}분 후)")
    behind = (now - d).days
    if behind > MAX_AGE_DAYS:
        raise HTTPException(400, f"fetched_at 이 너무 오래됐다 ({behind}일 전)")
    return d
