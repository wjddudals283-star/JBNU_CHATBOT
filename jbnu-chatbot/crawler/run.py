"""크롤러 CLI 진입점.

    python -m crawler.run --source likehome_week_menu
    python -m crawler.run --source likehome_week_menu --date 2026-06-01
    python -m crawler.run --source likehome_week_menu --dry-run
    python -m crawler.run --list
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler import fetch as fetch_mod          # noqa: E402
from crawler import ingest as ingest_mod        # noqa: E402
from crawler.parsers import coop_week_menu       # noqa: E402
from crawler.parsers import jbnu_cafeteria_day   # noqa: E402
from crawler.parsers import likehome_week_menu   # noqa: E402
from store import repo                           # noqa: E402

SOURCES_PATH = ROOT / "config" / "sources.yaml"
# 배포 환경에서는 영구 디스크 경로를 쓴다 (Render: /var/data).
_DATA = pathlib.Path(os.environ.get("JBNU_DATA_DIR", str(ROOT / "data")))
DB_PATH = pathlib.Path(os.environ.get("JBNU_DB_PATH", str(_DATA / "jbnu.db")))
SNAPSHOT_DIR = _DATA / "snapshots"

PARSERS = {
    "likehome_week_menu": likehome_week_menu.parse,
    "jbnu_cafeteria_day": jbnu_cafeteria_day.parse,
    "coop_week_menu": coop_week_menu.parse,
}

# 이 원천들이 시설을 스스로 만든다 (facility_id 가 config 에 하나로 안 잡힌다)
MULTI_FACILITY = {
    "jbnu_cafeteria_day": jbnu_cafeteria_day.FACILITY_BY_NAME,
    "coop_week_menu": coop_week_menu.FACILITY_BY_REST,
}


def load_sources() -> dict:
    return yaml.safe_load(SOURCES_PATH.read_text(encoding="utf-8"))


def ensure_facility(conn, source_key: str, cfg: dict) -> None:
    pairs = []
    if source_key in MULTI_FACILITY:
        pairs = [(fid, name) for name, fid in MULTI_FACILITY[source_key].items()]
    elif cfg.get("facility_id"):
        pairs = [(cfg["facility_id"], cfg.get("facility_name", cfg["facility_id"]))]
    for fid, name in pairs:
        conn.execute(
            """INSERT OR IGNORE INTO facility
                 (id, name, facility_type, source_url, source_type)
               VALUES (?,?,?,?,?)""",
            (fid, name, "식당", cfg["url"], cfg.get("source_type", "official")),
        )
    conn.commit()


def run_source(conn, source_key: str, cfg: dict, *, date: str | None,
               dry_run: bool, force: bool) -> None:
    parser = PARSERS.get(cfg.get("parser"))
    if parser is None:
        print(f"  [{source_key}] 파서 미구현 — 건너뜀")
        return

    params = dict(cfg.get("params") or {})
    if date and cfg.get("date_param"):
        fmt = cfg.get("date_format")
        d = dt.date.fromisoformat(date)
        params[cfg["date_param"]] = d.strftime(fmt) if fmt else d.isoformat()

    csrf = cfg.get("csrf")
    if csrf:
        result = fetch_mod.fetch_with_csrf(
            source_key, cfg["url"], page_url=csrf["page_url"],
            meta_name=csrf.get("meta_name", "_csrf"),
            header=csrf.get("header", "X-CSRF-Token"),
            params=params, media_type=cfg.get("media_type", "html"))
    else:
        result = fetch_mod.fetch(source_key, cfg["url"], params=params,
                                 method=cfg.get("method", "GET"),
                                 media_type=cfg.get("media_type", "html"))
    print(f"  fetch {result.http_status}  {len(result.content):,}B  "
          f"hash={result.content_hash[:12]} stable={result.stable_hash[:12]}")
    print(f"  final_url = {result.final_url}")

    if dry_run:
        parsed = parser(result.text)
        ok, bad = parsed.counts
        print(f"  [dry-run] week_start={parsed.week_start}  "
              f"앵커 {len(parsed.anchors)}열 통과  파싱 {ok}건 / 격리 {bad}건")
        if getattr(parsed, "hours", None):
            print(f"  운영시간 {len(parsed.hours)}행  "
                  f"coverage=complete → {sorted(parsed.complete_hours_facilities)}")
        if getattr(parsed, "prices", None):
            print(f"  단가 {len(parsed.prices)}행")
        _print_summary(parsed)
        return

    ensure_facility(conn, source_key, cfg)
    report = ingest_mod.ingest(
        conn, result, parser=parser, snapshot_dir=SNAPSHOT_DIR,
        tier=cfg.get("tier", "T1"),
        extraction_method=cfg.get("extraction_method", "html_selector"),
        confidence=float(cfg.get("confidence", 0.95)),
        force=force,
    )
    print(f"  outcome={report.outcome}  파싱 {report.parsed}건 / "
          f"격리 {report.quarantined}건  파서호출={report.parser_called}")
    for r in report.reasons[:5]:
        print(f"    · {r}")
    if report.error:
        print(f"    ! {report.error}")


def _print_summary(parsed) -> None:
    by_status: dict[str, int] = {}
    for m in parsed.meals:
        by_status[m.service_status] = by_status.get(m.service_status, 0) + 1
    print(f"  service_status 분포: {by_status}")
    for m in parsed.meals:
        if m.items:
            names = ", ".join(i.name for i in m.items[:5])
            more = f" …(+{len(m.items)-5})" if len(m.items) > 5 else ""
            print(f"    {m.date} {m.meal_type:9} {names}{more}")
        elif m.note:
            print(f"    {m.date} {m.meal_type:9} [{m.service_status}] {m.note}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="전북대 총학 챗봇 크롤러")
    ap.add_argument("--source")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--date")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="해시가 같아도 다시 파싱한다")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    sources = load_sources()

    if args.list:
        for k, v in sources.items():
            impl = "구현됨" if v.get("parser") in PARSERS else "미구현"
            print(f"  {k:24} {v.get('tier'):3} {impl:6} {v.get('label','')}")
        return 0

    targets = list(sources) if args.all else ([args.source] if args.source else [])
    if not targets:
        ap.error("--source 또는 --all 이 필요하다")

    conn = None
    if not args.dry_run:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = repo.connect(DB_PATH)
        repo.init_db(conn)

    for key in targets:
        cfg = sources.get(key)
        if cfg is None:
            print(f"[{key}] 알 수 없는 source")
            continue
        print(f"\n[{key}] {cfg.get('label','')}")
        try:
            run_source(conn, key, cfg, date=args.date,
                       dry_run=args.dry_run, force=args.force)
        except Exception as e:  # noqa: BLE001
            print(f"  실패: {type(e).__name__}: {e}")
    if conn:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
