"""저장된 원문으로 다시 파싱한다 — 네트워크를 쓰지 않는다.

    원문이 바뀌었다  → 재수집 (pages_run)
    해석이 바뀌었다  → 재파싱 (여기)

파서를 고쳤을 때 6,985페이지를 다시 긁는 것은 네트워크를 캐시 대신 쓰는 일이다.
학교 서버에 부담이고, 두 시간이 걸리고, 돌고 있는 수집까지 죽인다.

    python -m crawler.pages_reparse --db data/jbnu.db
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import pathlib
import re
import sys
import urllib.parse as up

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler import boilerplate as bp  # noqa: E402
from crawler import pages_run, snapshots  # noqa: E402
from crawler.parsers import jbnu_subview as SV  # noqa: E402
from crawler.parsers import notice_list as NL  # noqa: E402
from store import repo  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
log = logging.getLogger(__name__)


def run(db_path: str, *, snapshot_root: pathlib.Path, limit: int | None = None,
        now: dt.datetime | None = None, verbose: bool = True) -> dict:
    now = now or dt.datetime.now(KST)
    stamp = now.isoformat()
    rows = pages_run.targets(limit)

    html: dict[str, str] = {}
    missing = 0
    for r in rows:
        h = snapshots.load(r["url"], snapshot_root)
        if h is None:
            missing += 1
            continue
        html[r["url"]] = h
    if verbose:
        print(f"원문 {len(html)}개 · 없음 {missing}개 "
              f"(없는 것은 다음 수집 때 저장된다)")
    if not html:
        # 원문이 하나도 없으면 재파싱할 게 없다. 조용히 성공한 척하지 않는다.
        print("★ 저장된 원문이 없다. 먼저 pages_run 을 한 번 돌려야 한다.")
        return {"reparsed": 0, "missing": missing}

    # 보일러플레이트는 CMS 마다 따로 센다 (수집 때와 같은 규칙)
    by_profile: dict[str, list[dict]] = {}
    page_profile: dict[str, str] = {}
    for u, h in html.items():
        try:
            key, frags = SV.fragments_with_profile(h, up.urlsplit(u).hostname or "")
            page_profile[u] = key
            by_profile.setdefault(key, []).append(frags)
        except Exception:  # noqa: BLE001
            pass
    reports = {k: bp.detect(v) for k, v in by_profile.items()}

    site_names = pages_run._site_names()
    tally = {k: 0 for k in repo.PARSE_STATUSES}
    kinds = {"empty_nocontent": 0, "empty_unparsed": 0}
    sections = notices = boards = 0
    conn = repo.connect(db_path)
    try:
        repo.ensure_columns(conn)
        for i, r in enumerate(rows, 1):
            u = r["url"]
            if u not in html:
                continue
            common = {"page_url": u, "host": r["host"], "path": r["path"],
                      "kind": r["kind"], "discovered_at": stamp,
                      "last_attempt_at": stamp}
            if NL.is_board_page(html[u]):
                nl = NL.parse(html[u], page_url=u)
                if nl.items:
                    notices += repo.replace_notices(
                        conn, board_url=u, items=nl.items, host=r["host"],
                        board_name=nl.board_name,
                        site_name=site_names.get(r["host"], ""),
                        observed_at=stamp)
                    boards += 1
            try:
                res = SV.parse(html[u], page_url=u,
                               boilerplate_report=reports.get(page_profile.get(u)))
            except Exception as e:  # noqa: BLE001
                repo.upsert_page(conn, parse_status="parse_error", http_status=200,
                                 error_message=f"{type(e).__name__}: {e}"[:200],
                                 **common)
                tally["parse_error"] += 1
                continue

            chars = pages_run._content_chars(res.sections)
            status = "ok" if chars >= pages_run.MIN_CONTENT_CHARS else "empty"
            note = None
            if status == "empty":
                raw = len(re.sub(r"\s+", "", res.raw_text or ""))
                if raw < pages_run.MIN_CONTENT_CHARS:
                    note = "empty_nocontent 원문에 본문이 없다 (이미지·JS·빈 페이지)"
                    kinds["empty_nocontent"] += 1
                else:
                    note = f"empty_unparsed 원문 {raw}자인데 섹션을 못 만들었다"
                    kinds["empty_unparsed"] += 1
            repo.upsert_page(
                conn, parse_status=status, http_status=200,
                last_success_at=stamp if status == "ok" else None,
                section_count=len(res.sections), leaf_count=len(res.leaves),
                table_count=sum(1 for x in res.sections if x.kind == "table"),
                empty_block_count=pages_run._empty_blocks(res.sections),
                content_chars=chars, pruned_nodes=res.pruned.get("pruned", 0),
                last_modified=res.last_modified, title=res.title,
                note=note, **common)
            sections += repo.replace_sections(
                conn, page_url=u, sections=res.sections, observed_at=stamp,
                page_last_modified=res.last_modified)
            tally[status] += 1
            if verbose and i % 1000 == 0:
                print(f"  …{i}/{len(rows)}")
        conn.commit()
        repo.rebuild_fts(conn)
        repo.mark_boards(conn)
        summary = repo.coverage_summary(conn)
    finally:
        conn.close()

    if verbose:
        print()
        for k, n in tally.items():
            if n:
                print(f"  {k:12}{n:>8}")
        # ★ empty 를 뭉쳐 세면 고칠 수 있는지 없는지 알 수가 없다
        print(f"  empty 갈래 — 학교가 안 올림 {kinds['empty_nocontent']} · "
              f"우리가 못 읽음 {kinds['empty_unparsed']}")
        print(f"  섹션 {sections} · 색인 잎 {summary['indexed_leaves']}")
        print(f"  커버 {summary['covered_ratio']:.1%}")
    return {"reparsed": sum(tally.values()), "missing": missing,
            "kinds": kinds, "summary": summary}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--snapshots", default="")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = pathlib.Path(args.snapshots) if args.snapshots else \
        pathlib.Path(args.db).resolve().parent / "snapshots"
    run(args.db, snapshot_root=root, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
