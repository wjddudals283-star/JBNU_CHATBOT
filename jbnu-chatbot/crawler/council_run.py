"""총학 공지·행사 시트를 읽어 온다 (T4).

    python -m crawler.council_run
    python -m crawler.council_run --dry-run

★ 인스타 API 를 접고 시트로 왔다
  Graph API 는 앱 심사 · 60일 토큰 만료 · 페이스북 페이지 연결이 따라온다.
  총학이 관리할 비밀이 하나 늘고, 60일마다 사람이 기억해야 한다.
  학식 백필에서 배운 게 그거다 — **사람이 기억하는 구조는 학기 중에 진다.**

★ 시트는 '웹에 게시' 된 CSV 로 읽는다
  API 키도, OAuth 도, 서비스 계정도 필요 없다. 비밀이 안 늘어난다.
  총학이 시트 주인이고 자기 자료를 스스로 공개하는 것이라
  스크래핑 금지 선에도 안 걸린다.

  ★ 대신 그 URL 을 아는 사람은 누구나 읽을 수 있다.
    시트에 든 것은 인스타에 이미 올라간 공지와 작성국뿐이어야 한다.
    학생 개인정보·내부 논의는 이 시트에 넣지 않는다 (docs/COUNCIL_SHEET.md).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler import fetch as fetch_mod              # noqa: E402
from crawler.parsers import council_sheet           # noqa: E402
from crawler.run import DB_PATH, _record_fetch_failure  # noqa: E402
from store import repo                              # noqa: E402

log = logging.getLogger("jbnu.crawler.council")
KST = dt.timezone(dt.timedelta(hours=9))

SOURCE_KEY = "council_sheet"
URL_ENV = "COUNCIL_SHEET_CSV"


def sheet_url() -> str | None:
    """★ 주소를 코드에 박지 않는다.

    시트를 새로 만들거나 주소가 바뀌면 배포 없이 고칠 수 있어야 한다
    (안전 연락처를 config 로 뺀 것과 같은 이유).
    """
    return (os.environ.get(URL_ENV) or "").strip() or None


def run(db_path: pathlib.Path | None = None, *,
        now: dt.datetime | None = None, dry: bool = False) -> dict:
    now = now or dt.datetime.now(KST)
    url = sheet_url()
    out: dict = {"ok": False, "source": SOURCE_KEY}
    if not url:
        # ★ 설정이 없는 것과 실패한 것을 구별한다.
        #   아직 안 켠 기능을 '고장' 으로 세면 진짜 고장이 묻힌다.
        out["skipped"] = f"{URL_ENV} 환경변수가 없다 (아직 안 켠 상태)"
        log.info("[council] SKIP %s", out["skipped"])
        return out

    db = db_path or DB_PATH
    conn = repo.connect(db) if not dry else None
    try:
        try:
            res = fetch_mod.fetch(SOURCE_KEY, url, media_type="html")
        except Exception as e:  # noqa: BLE001
            # ★ 가져오다 죽으면 반드시 남긴다 (8/14 에 겪은 것)
            _record_fetch_failure(conn, SOURCE_KEY, e)
            out["error"] = f"{type(e).__name__}: {e}"
            log.error("[council] fetch 실패 %s", out["error"])
            return out

        out["http_status"] = res.http_status
        if res.http_status != 200:
            if conn is not None:
                rid = f"{SOURCE_KEY}/{now.isoformat()}"
                repo.start_crawl(conn, run_id=rid, source_key=SOURCE_KEY,
                                 started_at=now.isoformat())
                repo.finish_crawl(conn, rid, outcome="fetch_error",
                                  finished_at=now.isoformat(),
                                  error_message=f"HTTP {res.http_status}")
                conn.commit()
            out["error"] = f"HTTP {res.http_status}"
            return out

        try:
            parsed = council_sheet.parse(res.text, today=now.date())
        except council_sheet.ParseError as e:
            # ★ 머리글이 바뀌면 기존 데이터를 건드리지 않는다 (T3).
            if conn is not None:
                rid = f"{SOURCE_KEY}/{now.isoformat()}"
                repo.start_crawl(conn, run_id=rid, source_key=SOURCE_KEY,
                                 started_at=now.isoformat())
                repo.finish_crawl(conn, rid, outcome="parse_error",
                                  finished_at=now.isoformat(),
                                  error_message=str(e))
                conn.commit()
            out["error"] = f"parse_error: {e}"
            log.error("[council] %s", out["error"])
            return out

        ok, bad = parsed.counts
        out.update(parsed=ok, quarantined=bad,
                   reasons=[f"{n}행: {why}" for n, why in parsed.quarantined[:5]])
        if dry:
            out["ok"] = True
            return out

        sid = f"{SOURCE_KEY}/{now.isoformat()}"
        conn.execute(
            """INSERT OR REPLACE INTO source_snapshot
                 (id, source_key, url, fetched_at, http_status, content_hash,
                  stable_hash, content_path, media_type)
               VALUES (?,?,?,?,?,?,?,?,'html')""",
            (sid, SOURCE_KEY, url, res.fetched_at, res.http_status,
             res.content_hash, res.stable_hash, ""))
        n = repo.upsert_council_posts(
            conn, parsed.rows, source_id=sid, source_url=url,
            observed_at=res.fetched_at)
        rid = f"{SOURCE_KEY}/{now.isoformat()}"
        repo.start_crawl(conn, run_id=rid, source_key=SOURCE_KEY,
                         started_at=now.isoformat())
        repo.finish_crawl(conn, rid, outcome="success",
                          finished_at=now.isoformat(),
                          items_parsed=n, items_quarantined=bad)
        conn.commit()
        out["ok"] = True
        out["expired"] = repo.council_expired_count(
            conn, today=now.date().isoformat())
        log.info("[council] %s건 반영 · 격리 %s · 마감지남 %s",
                 n, bad, out["expired"])
        return out
    finally:
        if conn is not None:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    r = run(dry=args.dry_run)
    for k, v in r.items():
        print(f"  {k}: {v}")
    if r.get("skipped"):
        return 0
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
