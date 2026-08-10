"""크롤 파이프라인 — fetch → hash 비교 → parse → validate → upsert.

동작 순서 (02_핸드오프.md §3)
  1. fetch → content_hash 계산
  2. 직전과 동일 → observed_at 만 갱신, outcome='unchanged', **파서 미호출**
  3. 변경 → parse → validate → upsert
  4. 실패 → crawl_run 에 기록. **기존 데이터는 건드리지 않는다**
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sqlite3
from dataclasses import dataclass, field
from typing import Callable

from crawler import fetch as fetch_mod
from crawler import validate
from crawler.validate import ParseError
from store import repo


@dataclass
class IngestReport:
    run_id: str
    outcome: str
    parsed: int = 0
    quarantined: int = 0
    hours: int = 0
    prices: int = 0
    calendar: int = 0
    parser_called: bool = False
    error: str | None = None
    # 차단 사유가 본문에 적혀 있는 경우가 많다. 진단의 출발점이라 버리지 않는다.
    response_body: str | None = None
    reasons: list[str] = field(default_factory=list)


def ingest(conn: sqlite3.Connection, result: fetch_mod.FetchResult, *,
           parser: Callable[[str], object],
           snapshot_dir: pathlib.Path,
           tier: str = "T1",
           extraction_method: str = "html_selector",
           confidence: float = 0.95,
           run_id: str | None = None,
           previous_count: int | None = None,
           force: bool = False) -> IngestReport:
    rid = run_id or _next_run_id(conn, result)
    repo.start_crawl(conn, run_id=rid, source_key=result.source_key,
                     started_at=result.fetched_at)

    # ★ 비교는 stable_hash 로 한다. 원문 해시로 하면 캐시버스터(?ver=<유닉스타임>)
    #   때문에 매번 달라져서 이 분기가 영영 성립하지 않는다.
    current = result.stable_hash or result.content_hash
    prev_hash = None if force else _last_good_hash(conn, result.source_key)
    if prev_hash == current:
        # 내용이 그대로다. 파서를 부르지 않는다 (T5).
        conn.execute(
            "UPDATE source_snapshot SET fetched_at = ? WHERE stable_hash = ? AND source_key = ?",
            (result.fetched_at, current, result.source_key),
        )
        repo.finish_crawl(conn, rid, outcome="unchanged", finished_at=result.fetched_at)
        conn.commit()
        return IngestReport(run_id=rid, outcome="unchanged", parser_called=False)

    # ★ 파서에 넘기기 전에 HTTP 상태를 본다.
    #   403/500 본문을 그대로 파서에 주면 JSONDecodeError 같은 엉뚱한 예외가 나고,
    #   **차단 사유가 어디에도 안 남는다.** 원인 진단이 불가능해진다.
    if not (200 <= result.http_status < 300):
        body = result.text[:600].strip()
        repo.finish_crawl(
            conn, rid, outcome="fetch_error", finished_at=result.fetched_at,
            error_message=f"HTTP {result.http_status} · {len(result.content)}B · {body}",
        )
        conn.commit()
        return IngestReport(run_id=rid, outcome="fetch_error", parser_called=False,
                            error=f"HTTP {result.http_status}", response_body=body)

    sid = fetch_mod.snapshot_id(result)
    path = fetch_mod.save_snapshot_file(result, snapshot_dir)

    report = IngestReport(run_id=rid, outcome="parse_error", parser_called=True)
    try:
        parsed = parser(result.text)
    except ParseError as e:
        # ★ 기존 데이터를 건드리지 않는다 (T3).
        #   스냅샷 행도 남기지 않는다 — 남기면 다음 회차가 같은 내용을
        #   'unchanged' 로 스킵해서, 셀렉터를 고쳐도 복구가 안 된다.
        #   조용히 멈추는 유형의 버그다.
        path.unlink(missing_ok=True)
        repo.finish_crawl(conn, rid, outcome="parse_error",
                          finished_at=result.fetched_at, error_message=str(e))
        conn.commit()
        report.outcome = "parse_error"
        report.error = str(e)
        return report

    # 이상 탐지 (게이트 3)
    anomaly = validate.detect_anomaly(len(parsed.meals), previous_count)

    repo.insert_snapshot(
        conn, id=sid, source_key=result.source_key, url=result.final_url,
        fetched_at=result.fetched_at, http_status=result.http_status,
        content_hash=result.content_hash, stable_hash=current,
        content_path=str(path), media_type=result.media_type,
    )
    meta_base = dict(source_id=sid, source_url=result.final_url,
                     observed_at=result.fetched_at, confidence=confidence,
                     extraction_method=extraction_method, tier=tier)

    for meal in parsed.meals:
        meta = repo.SourceMeta(valid_from=meal.date, **meta_base)
        repo.upsert_meal(conn, meal, meta)
        report.parsed += 1

    # 운영시간 (T2) — 있으면 같이 저장한다.
    for h in getattr(parsed, "hours", []):
        repo.upsert_hours(
            conn, facility_id=h.facility_id, term=h.term, weekday=h.weekday,
            meal_type=h.meal_type, is_closed=h.is_closed,
            open_time=h.open_time, close_time=h.close_time, note=h.note,
            meta=repo.SourceMeta(valid_from=result.fetched_at[:10],
                                 **{**meta_base, "tier": "T2"}),
        )
    # ★ 시간표를 통째로 파싱한 시설만 폐쇄세계 가정을 켠다.
    for fid in getattr(parsed, "complete_hours_facilities", ()):
        repo.set_hours_coverage(conn, fid, "complete")

    # 학사일정 (T1) — 있으면 같이 저장한다.
    cal_ok = 0
    for e in getattr(parsed, "calendar_entries", []):
        repo.upsert_calendar(
            conn, e,
            repo.SourceMeta(valid_from=result.fetched_at[:10], **meta_base))
        cal_ok += 1
    report.calendar = cal_ok

    # 단가표 (T2)
    price_ok = 0
    for p in getattr(parsed, "prices", []):
        reason = validate.validate_price_row(p.price_text, p.price_min, p.price_max)
        meta = repo.SourceMeta(valid_from=result.fetched_at[:10],
                               status="quarantine" if reason else "verified",
                               **{**meta_base, "tier": "T2"})
        repo.upsert_price(
            conn, facility_id=p.facility_id, name=p.name, price_text=p.price_text,
            price_min=p.price_min, price_max=p.price_max, audience=p.audience,
            category=p.category, corner=p.corner, note=p.note, meta=meta,
        )
        if reason:
            report.reasons.append(f"가격 격리: {p.name} — {reason}")
        else:
            price_ok += 1
    report.prices = price_ok
    report.hours = len(getattr(parsed, "hours", []))

    for meal, reason in parsed.quarantined:
        meta = repo.SourceMeta(valid_from=meal.date, status="quarantine", **meta_base)
        repo.upsert_meal(conn, meal, meta)
        report.quarantined += 1
        report.reasons.append(reason)

    outcome = "quarantined" if (report.quarantined and not report.parsed) else "success"
    if anomaly:
        outcome = "quarantined"
        report.reasons.append(anomaly)

    repo.finish_crawl(conn, rid, outcome=outcome, finished_at=result.fetched_at,
                      items_parsed=report.parsed, items_quarantined=report.quarantined,
                      error_message=anomaly)
    repo.record_metric(conn, rid, "items_parsed", float(report.parsed))
    repo.record_metric(conn, rid, "anchor_check", float(len(parsed.anchors)),
                       note="정렬 앵커 통과 열 수")
    conn.commit()
    report.outcome = outcome
    return report


def _next_run_id(conn: sqlite3.Connection, result: fetch_mod.FetchResult) -> str:
    """실행 ID. 같은 초에 두 번 돌아도 충돌하지 않게 일련번호를 붙인다.

    crawl_run 은 이력이므로 덮어쓰지 않는다 — 하트비트와 지표 추세가 여기서 나온다.
    """
    n = conn.execute("SELECT COUNT(*) c FROM crawl_run WHERE source_key = ?",
                     (result.source_key,)).fetchone()["c"]
    return f"run/{result.source_key}/{result.fetched_at}#{n:04d}"


def _last_good_hash(conn: sqlite3.Connection, source_key: str) -> str | None:
    """마지막으로 성공(또는 unchanged)한 회차가 본 해시.

    파싱 실패 회차는 스냅샷을 남기지 않으므로, source_snapshot 에 있는
    최신 해시가 곧 '마지막으로 파싱까지 성공한 내용'이다.
    """
    row = conn.execute(
        """SELECT stable_hash, content_hash FROM source_snapshot
            WHERE source_key = ? ORDER BY fetched_at DESC LIMIT 1""",
        (source_key,),
    ).fetchone()
    if not row:
        return None
    return row["stable_hash"] or row["content_hash"]
