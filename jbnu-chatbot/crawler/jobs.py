"""스케줄러가 돌리는 **작업** — 원천 하나를 긁는 것이 아니라 여러 페이지를 도는 일.

식단·학사일정은 '원천 하나 → 파서 하나' 라 run.py 가 처리한다.
안내 페이지·공지는 수천 페이지를 도는 일이라 모양이 다르다.
스케줄러가 둘 다 다룰 수 있게 여기서 이름을 붙인다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from crawler import notices_run, pages_run

KST = dt.timezone(dt.timedelta(hours=9))


def run_pages(db_path: str, cfg: dict, now: dt.datetime) -> dict:
    """안내 페이지 전수. 받아온 원문으로 게시판도 함께 읽는다."""
    return pages_run.run(db_path, delay=cfg.get("delay", 0.45), now=now,
                         verbose=False)


def run_notices(db_path: str, cfg: dict, now: dt.datetime) -> dict:
    """이미 아는 게시판만 빠르게 갱신. 새 게시판은 전수 수집에서 발견된다."""
    return notices_run.run(db_path, delay=cfg.get("delay", 0.35), now=now,
                           verbose=False, known_only=True)


JOBS: dict[str, Callable[[str, dict, dt.datetime], Any]] = {
    "pages": run_pages,
    "notices": run_notices,
}
