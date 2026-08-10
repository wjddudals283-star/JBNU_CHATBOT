"""인프로세스 스케줄러 — 웹 서비스 안에서 도는 백그라운드 루프.

★ 왜 별도 Cron Job 이 아닌가
  Render 는 **Cron Job 에 디스크를 붙일 수 없다**(공식 문서). 디스크는 한 서비스
  인스턴스에만 붙고 다른 서비스에서 접근할 수 없다. SQLite 파일이 웹 서비스의
  디스크에 있으므로, 별도 Cron Job 은 그 DB 를 볼 수가 없다.
  → 웹 서비스 한 개 + 그 안의 스레드로 간다. 서비스도 하나, 디스크도 하나, 요금도 하나.

  나중에 Postgres 로 옮기면 Cron Job 분리가 가능해진다(3단계).

동작
  · 15분마다 due_sources() 를 물어보고 차례인 것만 돈다
  · 창을 놓쳐도 그날 성공이 없으면 따라잡는다 (schedule.due_sources)
  · 예외가 나도 루프는 죽지 않는다. 죽으면 침묵이 되고, 침묵이 가장 위험하다
"""

from __future__ import annotations

import datetime as dt
import os
import threading
import traceback

from crawler import run as run_mod
from crawler import schedule as sched
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))
DEFAULT_INTERVAL_SEC = 15 * 60


class SchedulerLoop:
    def __init__(self, *, interval_sec: int = DEFAULT_INTERVAL_SEC,
                 window_min: int = 30):
        self.interval = interval_sec
        self.window = window_min
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_tick: str | None = None
        self.last_error: str | None = None
        self.ticks = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="jbnu-scheduler",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def tick(self, now: dt.datetime | None = None) -> list[str]:
        """한 번 돌린다. 테스트에서 직접 호출한다."""
        now = now or dt.datetime.now(KST)
        sources = sched.load_schedule()
        run_mod.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = repo.connect(run_mod.DB_PATH)
        try:
            repo.init_db(conn)
            targets = sched.due_sources(sources, now, window_min=self.window,
                                        conn=conn)
        finally:
            conn.close()
        for key in targets:
            run_mod.main(["--source", key])
        self.last_tick = now.isoformat()
        self.ticks += 1
        return targets

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
                self.last_error = None
            except Exception:  # noqa: BLE001
                # ★ 루프를 죽이지 않는다. 죽으면 아무 기록도 안 남는 침묵이 된다.
                self.last_error = traceback.format_exc().strip().splitlines()[-1]
            self._stop.wait(self.interval)


def should_run() -> bool:
    return os.environ.get("RUN_SCHEDULER", "").strip() in ("1", "true", "yes")
