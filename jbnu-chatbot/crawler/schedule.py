"""스케줄러 + 하트비트.

    python -m crawler.schedule --due            # 지금 돌 차례인 소스만
    python -m crawler.schedule --due --dry-run
    python -m crawler.schedule --heartbeat      # 침묵 감지. 이상 있으면 exit 1
    python -m crawler.schedule --hours-drift    # 운영시간 변화 관측 리포트

무인 운영 3원칙 (01_설계.md §10)
  1. 하트비트 — 24시간 성공 크롤이 없으면 알린다. **침묵이 가장 위험하다**
  2. 셀렉터 외부화 — selectors.yaml
  3. 폴백 — 실패 시 기존 데이터 유지 + 거절 응답

★ 운영시간 주간 크롤을 반드시 포함한다.
  개강(9/1) 전후로 시간표가 바뀌는지 관측해두면 `term='unspecified'` 를
  추론이 아니라 **관측으로** 해소할 수 있다. 이 창을 놓치면 다음 학기 경계까지
  기다려야 한다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler import run as run_mod          # noqa: E402
from store import repo                      # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
HEARTBEAT_HOURS = 24


def load_schedule() -> dict:
    return yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))


def max_gap_hours(cfg: dict) -> float | None:
    """이 소스의 **최대 크롤 간격**(시간). 예정이 없으면 None.

    ★ 야간 간격을 빼먹으면 안 된다. 06:00/11:00/16:00 은 낮 간격이 5시간이라
      촘촘해 보이지만 16:00 → 다음날 06:00 이 **14시간**이고, 그게 최대다.
      한 번 실패하면 28시간이 되어 24시간 임계를 넘는다.
    """
    times = cfg.get("schedule") or []
    if not times:
        return None
    mins = sorted(int(h) * 60 + int(m) for h, m in (t.split(":") for t in times))
    if cfg.get("weekly_on") is not None:
        # 주 1회 — 마지막 실행에서 다음 주 첫 실행까지
        return (7 * 24 * 60 - (mins[-1] - mins[0])) / 60
    gaps = [b - a for a, b in zip(mins, mins[1:])]
    gaps.append(24 * 60 - mins[-1] + mins[0])       # 자정을 넘는 간격
    return max(gaps) / 60


def cadence_audit(sources: dict) -> list[dict]:
    """크롤 주기 ≤ 신선도 임계 ÷ 2 를 지키는지 점검한다.

    ★ 주기가 임계와 같으면 여유가 0이다. 한 번만 실패해도 즉시 stale 이 된다.
      절반으로 잡아야 1회 실패를 흡수한다.
    """
    out = []
    for key, cfg in sources.items():
        gap = max_gap_hours(cfg)
        # ★ 차단된 원천은 예정표가 실제 주기를 나타내지 않는다.
        #   생협은 Render 에서 403 이라 예정표대로 안 돌고, 실제로는 노트북 백필이
        #   유일한 경로다. 그 주기를 명시해야 점검이 거짓말을 안 한다.
        if cfg.get("known_blocked") and cfg.get("effective_cadence_hours"):
            gap = float(cfg["effective_cadence_hours"])
        if gap is None:
            continue
        limit = float(cfg.get("stale_after_hours") or HEARTBEAT_HOURS)
        budget = limit / 2
        out.append({
            "source_key": key,
            "max_gap_hours": round(gap, 1),
            "stale_after_hours": limit,
            "budget_hours": budget,
            "ok": gap <= budget,
            "survives_one_failure": gap * 2 <= limit,
        })
    return out


def _scheduled_today(cfg: dict, now: dt.datetime) -> list[dt.datetime]:
    weekly = cfg.get("weekly_on")               # 0=월 … 6=일
    if weekly is not None and now.weekday() != int(weekly):
        return []
    out = []
    for hhmm in cfg.get("schedule") or []:
        h, m = (int(x) for x in hhmm.split(":"))
        out.append(now.replace(hour=h, minute=m, second=0, microsecond=0))
    return out


def succeeded_today(conn, source_key: str, now: dt.datetime) -> bool:
    row = conn.execute(
        """SELECT 1 FROM crawl_run
            WHERE source_key = ? AND outcome IN ('success','unchanged')
              AND substr(started_at, 1, 10) = ? LIMIT 1""",
        (source_key, now.date().isoformat()),
    ).fetchone()
    return row is not None


def due_sources(sources: dict, now: dt.datetime, *, window_min: int = 30,
                conn=None) -> list[str]:
    """지금 돌려야 할 소스.

    두 가지 경로가 있다.

      1) **창 안** — 예정 시각 이후 window_min 분 이내. 정상 경로.
      2) **따라잡기** ★ — 예정 시각이 이미 지났는데 **그날 성공 기록이 없으면**
         창 밖이어도 실행한다.

    2번이 없으면 노트북을 09시에 켠 날은 07:00 창을 영영 놓친다.
    개강 전후 3주는 다시 오지 않는 관측 창이라 하루도 비우면 안 된다.
    (conn 이 없으면 1번만 판단한다 — 순수 함수로 쓸 때를 위한 것)
    """
    out = []
    for key, cfg in sources.items():
        if cfg.get("parser") not in run_mod.PARSERS:
            continue
        targets = _scheduled_today(cfg, now)
        if not targets:
            continue

        in_window = any(0 <= (now - t).total_seconds() / 60 < window_min
                        for t in targets)
        if in_window:
            out.append(key)
            continue

        # 따라잡기: 예정 시각이 지났는데 오늘 성공이 없다
        if conn is not None and any(now >= t for t in targets):
            if not succeeded_today(conn, key, now):
                out.append(key)
    return out


# ═══════════════════════════════════════════════════════════════
# 하트비트 — 침묵 감지
# ═══════════════════════════════════════════════════════════════

def source_freshness(conn, sources: dict, now: dt.datetime) -> list[dict]:
    """소스별 마지막 성공 크롤 + 임계 초과 여부.

    ★ 임계는 소스마다 다르다. 생협은 노트북 주 1회 백필이라 8일(192h)이 정상이고,
      24시간 기준을 그대로 적용하면 매일 경보가 뜬다.
      반대로 임계를 아예 안 두면 **백필이 조용히 멈춘 걸 못 잡는다.**
    """
    out = []
    for key, cfg in sources.items():
        if cfg.get("parser") not in run_mod.PARSERS:
            continue
        row = conn.execute(
            """SELECT MAX(started_at) AS last_ok FROM crawl_run
                WHERE source_key = ? AND outcome IN ('success','unchanged')""",
            (key,),
        ).fetchone()
        last_ok = row["last_ok"] if row else None
        age = repo.staleness_hours(last_ok, now) if last_ok else None
        limit = float(cfg.get("stale_after_hours") or HEARTBEAT_HOURS)
        blocked = cfg.get("known_blocked")

        out.append({
            "source_key": key,
            "label": cfg.get("label", key),
            "last_success": last_ok,
            "age_hours": round(age, 1) if age is not None else None,
            "age_days": round(age / 24, 1) if age is not None else None,
            "stale_after_hours": limit,
            "stale": age is None or age > limit,
            "known_blocked": bool(blocked),
            "blocked_since": (blocked or {}).get("since"),
            "workaround": (blocked or {}).get("workaround"),
            "reason": ("성공 크롤 기록 없음" if age is None
                       else f"{age:.1f}시간째 성공 없음 (임계 {limit:.0f}h)"
                       if age > limit else "정상"),
        })
    return out


def heartbeat(conn, sources: dict, now: dt.datetime) -> list[dict]:
    """침묵 감지 — 임계를 넘긴 소스만 돌려준다.

    ★ 실패보다 침묵이 위험하다. 실패는 crawl_run 에 남지만,
      스케줄러 자체가 안 돌면 아무 기록도 안 남는다. 그 상태를 잡는 게 이 함수다.

    ★ 알려진 차단(known_blocked)이라도 **면제하지 않는다.**
      우회 경로(노트북 백필)가 도는 게 전제이므로, 임계만 늘리고 감시는 유지한다.
      면제해 버리면 백필이 멈춘 걸 영영 모른다.
    """
    return [f for f in source_freshness(conn, sources, now) if f["stale"]]


# ═══════════════════════════════════════════════════════════════
# 운영시간 변화 관측
# ═══════════════════════════════════════════════════════════════

def hours_drift(conn) -> list[dict]:
    """시설별 운영시간 변화 이력.

    개강 경계를 지나며 시간표가 바뀌면 그 시간표가 학기 의존적이라는 게
    **관측된다.** 안 바뀌면 학기 무관인 게 관측된다. 둘 다 unspecified 해소에 쓴다.
    """
    out = []
    rows = conn.execute("SELECT id, name FROM facility ORDER BY id").fetchall()
    for f in rows:
        dates = repo.hours_observation_dates(conn, f["id"])
        if not dates:
            continue
        changes = repo.hours_changes(conn, f["id"])
        out.append({
            "facility_id": f["id"], "name": f["name"],
            "observations": len(dates),
            "first": dates[0], "last": dates[-1],
            "changes": changes,
        })
    return out


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="크롤 스케줄러 · 하트비트")
    ap.add_argument("--due", action="store_true", help="지금 차례인 소스 실행")
    ap.add_argument("--all", action="store_true", help="예정과 무관하게 전부 실행")
    ap.add_argument("--heartbeat", action="store_true")
    ap.add_argument("--hours-drift", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--window", type=int, default=30, help="예정 시각 허용 창(분)")
    args = ap.parse_args(argv)

    sources = load_schedule()
    now = dt.datetime.now(KST)

    if args.heartbeat or args.hours_drift:
        run_mod.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = repo.connect(run_mod.DB_PATH)
        repo.init_db(conn)
        code = 0
        if args.heartbeat:
            alerts = heartbeat(conn, sources, now)
            if alerts:
                print(f"⚠ 하트비트 경보 {len(alerts)}건 ({now.isoformat()})")
                for a in alerts:
                    print(f"  · {a['source_key']:22} {a['reason']}"
                          f"  (마지막 성공 {a['last_success'] or '없음'})")
                code = 1
            else:
                print(f"하트비트 정상 — 모든 소스가 {HEARTBEAT_HOURS}시간 내 성공")
        if args.hours_drift:
            print(f"\n=== 운영시간 관측 이력 ===")
            for f in hours_drift(conn):
                print(f"\n  {f['name']} ({f['observations']}회 관측, "
                      f"{f['first']} ~ {f['last']})")
                if not f["changes"]:
                    print("    변화 없음 — 아직 학기 의존성을 판단할 근거가 없다")
                for ch in f["changes"]:
                    print(f"    ★ {ch['from_date']} → {ch['to_date']} 변화")
                    for x in ch["removed"][:6]:
                        print(f"        - {x}")
                    for x in ch["added"][:6]:
                        print(f"        + {x}")
        conn.close()
        return code

    run_mod.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = repo.connect(run_mod.DB_PATH)
    repo.init_db(conn)
    try:
        targets = (list(sources) if args.all
                   else due_sources(sources, now, window_min=args.window, conn=conn))
        caught_up = [k for k in targets
                     if not any(0 <= (now - t).total_seconds() / 60 < args.window
                                for t in _scheduled_today(sources[k], now))]
    finally:
        conn.close()

    if not targets:
        print(f"지금({now:%H:%M}) 차례인 소스 없음 (오늘 몫은 이미 끝남)")
        return 0

    print(f"[{now.isoformat()}] 실행 대상: {targets}")
    if caught_up:
        print(f"  ↳ 따라잡기(창 밖, 오늘 성공 없음): {caught_up}")
    return _run_many(targets, args)


def _run_many(targets: list[str], args) -> int:
    code = 0
    for key in targets:
        code |= run_mod.main(["--source", key] + (["--dry-run"] if args.dry_run else []))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
