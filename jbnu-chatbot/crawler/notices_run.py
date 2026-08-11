"""공지 게시판 수집 — 제목·게시일·링크·게시판만.

어느 페이지가 게시판인지는 **구조로** 안다 (table.artclTable / com-brd-list).
안내 페이지 수집에서 '본문이 없다(empty)' 로 남은 것 중 상당수가 게시판이었다.
그건 못 읽은 게 아니라 **아직 안 읽은 것**이다 — 여기서 읽는다.

    python -m crawler.notices_run --db data/jbnu.db
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import logging
import pathlib
import sys
import time
import urllib.parse as up

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler import fetch as fetch_mod  # noqa: E402
from crawler.parsers import notice_list as NL  # noqa: E402
from store import repo  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (compatible; JBNU-StudentCouncil-Bot/1.0)"
DEFAULT_DELAY = 0.4


def _site_names() -> dict[str, str]:
    try:
        import yaml
        doc = yaml.safe_load(
            (ROOT / "config" / "sites.yaml").read_text(encoding="utf-8"))
        return {h: v["name"] for h, v in (doc.get("sites") or {}).items()}
    except Exception:  # noqa: BLE001
        return {}


def candidates(conn) -> list[str]:
    """게시판일 수 있는 페이지. 본문이 비었던 곳이 1순위다.

    호스트를 번갈아 돌린다 — 한 사이트만 연달아 두드리지 않는다.
    """
    rows = conn.execute(
        """SELECT page_url, host FROM page_registry
            WHERE parse_status IN ('empty', 'ok')
            ORDER BY page_url""").fetchall()
    by_host: dict[str, list[str]] = {}
    for r in rows:
        by_host.setdefault(r["host"], []).append(r["page_url"])
    mixed, hosts = [], list(by_host)
    while any(by_host[h] for h in hosts):
        for h in hosts:
            if by_host[h]:
                mixed.append(by_host[h].pop(0))
    return mixed


def run(db_path: str, *, limit: int | None = None, delay: float = DEFAULT_DELAY,
        now: dt.datetime | None = None, verbose: bool = True) -> dict:
    now = now or dt.datetime.now(KST)
    stamp = now.isoformat()
    names = _site_names()

    conn = repo.connect(db_path)
    try:
        urls = candidates(conn)
    finally:
        conn.close()
    if limit:
        urls = urls[:limit]
    if verbose:
        print(f"후보 {len(urls)}페이지 · 간격 {delay}s · 동시성 1")

    boards = items_total = 0
    skipped_rows = 0
    per_host: collections.Counter = collections.Counter()
    conn = repo.connect(db_path)
    try:
        with httpx.Client(timeout=25.0, verify=fetch_mod.lax_ssl(),
                          follow_redirects=True,
                          headers={"User-Agent": UA}) as c:
            for i, u in enumerate(urls, 1):
                try:
                    r = c.get(u)
                    html = r.text if r.status_code == 200 else ""
                except Exception:  # noqa: BLE001
                    html = ""
                if html and NL.is_board_page(html):
                    res = NL.parse(html, page_url=u)
                    if res.items:
                        host = up.urlsplit(u).hostname or ""
                        items_total += repo.replace_notices(
                            conn, board_url=u, items=res.items, host=host,
                            board_name=res.board_name,
                            site_name=names.get(host, ""), observed_at=stamp)
                        boards += 1
                        per_host[host] += len(res.items)
                    skipped_rows += res.skipped
                if verbose and i % 100 == 0:
                    print(f"  …{i}/{len(urls)}  게시판 {boards} · 글 {items_total}")
                time.sleep(delay)
        conn.commit()
        total = repo.notice_total(conn)
    finally:
        conn.close()

    if verbose:
        print(f"\n게시판 {boards}곳 · 글 {items_total}건 (DB 총 {total})")
        # 링크나 제목이 없어 못 담은 행은 조용히 넘기지 않는다
        print(f"  담지 못한 행 {skipped_rows}건")
        print("  글이 많은 곳:", [f"{k}:{v}" for k, v in per_host.most_common(5)])
    return {"boards": boards, "items": items_total, "total": total,
            "skipped_rows": skipped_rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run(args.db, limit=args.limit, delay=args.delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
