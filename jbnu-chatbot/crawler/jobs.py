"""스케줄러가 돌리는 **작업** — 원천 하나를 긁는 것이 아니라 여러 페이지를 도는 일.

식단·학사일정은 '원천 하나 → 파서 하나' 라 run.py 가 처리한다.
안내 페이지·공지는 수천 페이지를 도는 일이라 모양이 다르다.
스케줄러가 둘 다 다룰 수 있게 여기서 이름을 붙인다.
"""

from __future__ import annotations

import datetime as dt
import pathlib
from typing import Any, Callable

from crawler import council_run, notices_run, pages_run, vocab
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))


def _record(db_path: str, source_key: str, now: dt.datetime, fn):
    """작업의 성공을 crawl_run 에 남긴다.

    ★ 이게 없어서 공지가 **매 틱(15분)마다** 다시 돌았다
      due_sources 의 따라잡기는 '예정 시각이 지났는데 **오늘 성공 기록이 없으면**'
      실행한다. notices 는 crawl_run 을 안 남겨서 succeeded_today 가 영원히
      False 였고, 07:00 이 지난 뒤로는 매 틱 재실행됐다.
      15분 걸리는 전량 재수집을 15분마다 — 거의 쉬지 않았다.
      학교 서버 부담이고, 그 시간에 다른 원천이 차례를 못 받는다.

      pages_run 은 자기가 crawl_run 을 남겨서 하루 한 번만 돈다.
      **같은 스케줄러가 도는데 한쪽만 기록을 남기고 있었다.**
    """
    run_id = f"{source_key}-{now.strftime('%Y%m%dT%H%M%S')}"
    conn = repo.connect(db_path)
    try:
        repo.start_crawl(conn, run_id=run_id, source_key=source_key,
                         started_at=now.isoformat())
        conn.commit()
    finally:
        conn.close()
    try:
        out = fn()
    except Exception:
        conn = repo.connect(db_path)
        try:
            repo.finish_crawl(conn, run_id, outcome="fetch_error",
                              finished_at=dt.datetime.now(KST).isoformat())
            conn.commit()
        finally:
            conn.close()
        raise
    conn = repo.connect(db_path)
    try:
        repo.finish_crawl(conn, run_id, outcome="success",
                          finished_at=dt.datetime.now(KST).isoformat())
        conn.commit()
    finally:
        conn.close()
    return out


def run_pages(db_path: str, cfg: dict, now: dt.datetime) -> dict:
    """안내 페이지 전수. 받아온 원문으로 게시판도 함께 읽는다."""
    return pages_run.run(db_path, delay=cfg.get("delay", 0.45), now=now,
                         verbose=False)


def run_notices(db_path: str, cfg: dict, now: dt.datetime) -> dict:
    """이미 아는 게시판만 빠르게 갱신. 새 게시판은 전수 수집에서 발견된다."""
    return _record(db_path, "jbnu_notices", now, lambda: notices_run.run(
        db_path, delay=cfg.get("delay", 0.35), now=now,
        verbose=False, known_only=True))


def run_vocab(db_path: str, cfg: dict, now: dt.datetime) -> Any:
    """어휘 사전을 다시 만든다.

    ★ 코퍼스가 바뀌면 사전도 바뀌어야 한다
      새 페이지가 들어왔는데 사전이 옛 판이면, 그 페이지의 낱말로는
      붙여 쓴 질문을 못 쪼갠다. 조용한 어긋남이다.

    ★ _record 로 감싼다 — 안 감싸서 **하루 78번** 돌았다 (2026-10-13)
      위 _record 주석에 적힌 것과 **같은 일**이 여기서 또 났다.
      기록이 없으면 succeeded_today 가 영원히 False 라, 04:30 이 지난 뒤
      자정까지 매 틱(15분) 사전 전체(71,215 잎)를 다시 만든다.
      전수 흉내로 쟀다: 예정 1회 → 실제 78회.
      공지(notices)가 먼저 그랬고 이게 세 번째다 — 그래서 고치는 것만으로는
      네 번째를 못 막는다. 자를 붙였다: tests/test_job_records.py 가
      config/sources.yaml 의 job 전부를 돌려 기록이 남는지 본다.

    ★ 그리고 멈춤과 정상이 **같은 모양**이었다
      기록이 없으니 /health 의 crawl_stale 에 영구히 떠서 거짓 경보가 되고,
      반대로 진짜 멈춰도 구별이 안 된다. 침묵이 가장 위험하다.
    """
    def _build():
        conn = repo.connect(db_path)
        try:
            return vocab.build(conn, now=now)
        finally:
            conn.close()
    return _record(db_path, "vocab_build", now, _build)


def run_council(db_path: str, cfg: dict, now: dt.datetime) -> Any:
    """총학 공지 시트. ★ 자기가 crawl_run 을 남기므로 _record 로 감싸지 않는다."""
    return council_run.run(pathlib.Path(db_path), now=now)


JOBS: dict[str, Callable[[str, dict, dt.datetime], Any]] = {
    "pages": run_pages,
    "notices": run_notices,
    "council": run_council,
    "vocab": run_vocab,
}
