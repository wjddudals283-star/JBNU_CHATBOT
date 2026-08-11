"""인프로세스 스케줄러 — 웹 서비스 안에서 도는 백그라운드 루프.

★ 왜 별도 Cron Job 이 아닌가
  Render 는 **Cron Job 에 디스크를 붙일 수 없다**(공식 문서). 디스크는 한 서비스
  인스턴스에만 붙고 다른 서비스에서 접근할 수 없다. SQLite 파일이 웹 서비스의
  디스크에 있으므로, 별도 Cron Job 은 그 DB 를 볼 수가 없다.
  → 웹 서비스 한 개 + 그 안의 스레드로 간다. 서비스도 하나, 디스크도 하나, 요금도 하나.

  나중에 Postgres 로 옮기면 Cron Job 분리가 가능해진다(3단계).

동작
  · 기동 즉시 한 번 틱, 그 뒤 15분마다
  · 창을 놓쳐도 그날 성공이 없으면 따라잡는다 (schedule.due_sources)
  · 예외가 나도 루프는 죽지 않는다. 죽으면 침묵이 되고, 침묵이 가장 위험하다

★ 로그
  전부 `[scheduler]` 로 시작하는 ASCII 라인이다. 한국어로 검색하면 안 걸리고,
  아무것도 안 도는 것과 로그가 안 보이는 것을 구분할 수 없기 때문이다.
  **할 일이 없는 틱도 로그를 남긴다** — 침묵과 정상을 구분해야 한다.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import traceback

from crawler import run as run_mod
from crawler import schedule as sched
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))


def heavy_jobs_enabled() -> bool:
    """수천 페이지를 도는 작업을 실제로 돌릴 것인가.

    프로덕션(RUN_SCHEDULER=1)에서만 켠다. 테스트·로컬에서 실수로 켜지면
    학교 서버를 수천 번 두드리게 된다 — 우리는 손님이다.
    """
    return os.environ.get("RUN_SCHEDULER", "").strip() in ("1", "true", "yes")
DEFAULT_INTERVAL_SEC = 15 * 60

log = logging.getLogger("jbnu.scheduler")


class SchedulerLoop:
    def __init__(self, *, interval_sec: int = DEFAULT_INTERVAL_SEC,
                 window_min: int = 30, smoke: bool = False):
        self.interval = interval_sec
        self.window = window_min
        # ★ 기본값 False — 실네트워크를 타는 동작은 명시적으로 켜야 한다.
        #   테스트가 무심코 외부 사이트를 두드리면 느려지고, 원천 사정으로
        #   우리 코드와 무관하게 빨간불이 된다. 프로덕션에서는 서버가 True 로 켠다.
        self.smoke_enabled = smoke
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.started_at: str | None = None
        self.last_tick: str | None = None
        self.last_targets: list[str] = []
        self.last_error: str | None = None
        self.ticks = 0
        self.runs = 0
        # 실사이트 스모크는 하루 한 번. 위치 의존 결함을 자동으로 잡는다.
        self.last_smoke: dict | None = None
        self._smoke_date: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            log.info("[scheduler] already running, skip start")
            return
        self.started_at = dt.datetime.now(KST).isoformat()
        log.info("[scheduler] START interval=%ss window=%smin db=%s tz=KST at=%s",
                 self.interval, self.window, run_mod.DB_PATH, self.started_at)
        self._thread = threading.Thread(target=self._run, name="jbnu-scheduler",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        log.info("[scheduler] STOP requested after %s ticks", self.ticks)
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

        self.ticks += 1
        # ★ 할 일이 없어도 남긴다. 이게 없으면 '안 돎'과 '할 일 없음'이 같은 모양이다.
        log.info("[scheduler] TICK #%s at=%s due=%s",
                 self.ticks, now.strftime("%Y-%m-%d %H:%M"), targets or "none")

        for key in targets:
            cfg = sources.get(key, {})
            log.info("[scheduler] RUN source=%s", key)
            try:
                job = cfg.get("job")
                if job and not heavy_jobs_enabled():
                    # ★ 수천 페이지를 도는 작업은 **환경변수**를 따른다.
                    #   플래그에 묶으면 테스트가 SchedulerLoop 를 만드는 순간
                    #   실사이트를 7,000번 두드린다 — 실제로 그렇게 멈췄다.
                    #   스모크에서 이미 겪은 것과 같은 결함이다.
                    #   건너뛴 사실은 반드시 남긴다. 침묵이 가장 위험하다.
                    log.info("[scheduler] SKIP job=%s (RUN_SCHEDULER 미설정)", job)
                    continue
                if job:
                    from crawler import jobs as jobs_mod
                    out = jobs_mod.JOBS[job](str(run_mod.DB_PATH), cfg, now)
                    log.info("[scheduler] JOB %s done %s", job, out)
                    self.runs += 1
                    continue
                run_mod.main(["--source", key])
                self.runs += 1
                log.info("[scheduler] DONE source=%s", key)
            except Exception:  # noqa: BLE001
                # 한 소스가 죽어도 나머지는 돌린다
                line = traceback.format_exc().strip().splitlines()[-1]
                log.error("[scheduler] FAIL source=%s %s", key, line)
                self.last_error = f"{key}: {line}"

        self.maybe_smoke(now)
        self.last_tick = now.isoformat()
        self.last_targets = targets
        return targets

    def maybe_smoke(self, now: dt.datetime) -> dict | None:
        """하루 한 번 실사이트 스모크.

        ★ 배포 서버의 네트워크 위치에서 돌아야 의미가 있다.
          '한국에서는 200, 해외에서는 403' 같은 위치 의존 결함은
          로컬 스모크가 원리적으로 못 잡는다.
        """
        if not self.smoke_enabled:
            return None
        today = now.date().isoformat()
        if self._smoke_date == today:
            return None
        try:
            from crawler import smoke as smoke_mod
            self.last_smoke = smoke_mod.run()
            self._smoke_date = today
            alerts = self.last_smoke.get("alerts") or []
            # ★ 알려진 차단은 경보로 올리지 않는다.
            #   실패가 정상 상태인 원천을 매일 ERROR 로 올리면 경고등이 상시 켜지고,
            #   그러면 진짜 문제가 묻힌다. 경보 판단은 smoke._alerts 가 한다.
            if alerts:
                log.warning("[scheduler] SMOKE alerts=%s",
                            [a["tag"] + ":" + a["source_key"] for a in alerts])
            else:
                log.info("[scheduler] SMOKE ok — 알려진 상태와 동일")
        except Exception:  # noqa: BLE001
            line = traceback.format_exc().strip().splitlines()[-1]
            log.error("[scheduler] SMOKE ERROR %s", line)
        return self.last_smoke

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                # ★ 루프를 죽이지 않는다. 죽으면 아무 기록도 안 남는 침묵이 된다.
                self.last_error = traceback.format_exc().strip().splitlines()[-1]
                log.error("[scheduler] TICK ERROR %s", self.last_error)
            self._stop.wait(self.interval)
        log.info("[scheduler] loop exited after %s ticks", self.ticks)

    def status(self) -> dict:
        return {
            "started_at": self.started_at,
            "ticks": self.ticks,
            "runs": self.runs,
            "last_tick": self.last_tick,
            "last_targets": self.last_targets,
            "last_error": self.last_error,
            "interval_sec": self.interval,
            "alive": bool(self._thread and self._thread.is_alive()),
            "smoke": None if self.last_smoke is None else {
                "at": self.last_smoke.get("at"),
                "verdict": self.last_smoke.get("verdict"),
                "coop_passing_variants": self.last_smoke.get("coop_passing_variants"),
                "alerts": self.last_smoke.get("alerts"),
            },
        }


def should_run() -> bool:
    return os.environ.get("RUN_SCHEDULER", "").strip() in ("1", "true", "yes")
