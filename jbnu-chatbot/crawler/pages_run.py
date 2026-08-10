"""정적 안내 페이지 수집 파이프라인 — 2단계.

    1차  전 페이지 조각 수집 → 여러 페이지에 반복되는 템플릿을 관측으로 가려낸다
    2차  그 조각을 DOM 에서 잘라낸 뒤 파싱 → 섹션 + 커버리지 레지스트리 기록

  왜 2단계인가: 템플릿이 본문과 **같은 블록 안에** 있으면 파싱 후에는 못 가른다.
  자르는 시점이 파싱보다 앞서야 블록 텍스트까지 깨끗해진다.

  우리는 손님이다. 요청 간격을 지키고 동시성은 1이다.

    python -m crawler.pages_run --db data/jbnu.db --limit 200
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import pathlib
import sys
import time
import urllib.parse as up

import httpx
import yaml

from crawler import boilerplate as bp
from crawler import fetch as fetch_mod
from crawler.parsers import jbnu_subview as SV
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))
log = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGES_YAML = ROOT / "config" / "pages.yaml"
UA = "Mozilla/5.0 (compatible; JBNU-StudentCouncil-Bot/1.0)"
DEFAULT_DELAY = 0.7
# 1단계 범위 — 본부 한국어 안내. 영문 사이트·게시판 상세는 뺀다.
TARGET_PREFIXES = ("/web/academic", "/web/unvrslife", "/web/info", "/web/intro")
TARGET_HOST = "www.jbnu.ac.kr"
SKIP_KINDS = ("board_detail", "xhr")
# 이보다 본문이 짧으면 '내용 없음'으로 본다. 파싱은 됐지만 답할 것이 없는 상태다.
MIN_CONTENT_CHARS = 40
# 제목만 있고 알맹이가 없는 블록 (JS 로 그리는 영역) 판정 기준
EMPTY_BLOCK_CHARS = 20


def targets(limit: int | None = None) -> list[dict]:
    doc = yaml.safe_load(PAGES_YAML.read_text(encoding="utf-8"))
    out, seen = [], set()
    for p in doc.get("pages", []):
        u = p["url"]
        sp = up.urlsplit(u)
        if sp.hostname != TARGET_HOST or p.get("kind") in SKIP_KINDS:
            continue
        if not sp.path.startswith(TARGET_PREFIXES):
            continue
        if p.get("status") != 200 or sp.path in seen:
            continue
        seen.add(sp.path)
        out.append({"url": u, "path": sp.path, "host": sp.hostname,
                    "kind": p.get("kind", "static_page")})
    out.sort(key=lambda r: r["path"])
    return out[:limit] if limit else out


def _content_chars(sections) -> int:
    return sum(len(s.text) for s in sections if s.is_leaf or s.kind == "table")


def _empty_blocks(sections) -> int:
    """제목만 남은 블록 수 — 본문을 JS 로 그리는 페이지가 여기 걸린다."""
    return sum(1 for s in sections
               if s.kind == "block" and len(s.text) < EMPTY_BLOCK_CHARS)


def run(db_path: str, *, limit: int | None = None, delay: float = DEFAULT_DELAY,
        now: dt.datetime | None = None, verbose: bool = True) -> dict:
    now = now or dt.datetime.now(KST)
    stamp = now.isoformat()
    rows = targets(limit)
    if verbose:
        print(f"대상 {len(rows)}페이지 · 간격 {delay}s · 동시성 1")

    # ---- 1차: 가져오고 조각을 센다 -------------------------------------
    html: dict[str, str] = {}
    fetch_fail: dict[str, tuple[int | None, str]] = {}
    frag_pages: list[dict] = []
    frag_fail: dict[str, str] = {}

    with httpx.Client(timeout=30.0, verify=fetch_mod.lax_ssl(),
                      follow_redirects=True, headers={"User-Agent": UA}) as c:
        for i, r in enumerate(rows, 1):
            try:
                resp = c.get(r["url"])
                if resp.status_code != 200:
                    fetch_fail[r["url"]] = (resp.status_code,
                                            f"HTTP {resp.status_code}")
                else:
                    html[r["url"]] = resp.text
            except Exception as e:  # noqa: BLE001
                fetch_fail[r["url"]] = (None, f"{type(e).__name__}: {e}"[:200])
            if verbose and i % 25 == 0:
                print(f"  …{i}/{len(rows)} 수집")
            time.sleep(delay)

    for u, h in html.items():
        try:
            frag_pages.append(SV.page_fragments(h))
        except Exception as e:  # noqa: BLE001
            frag_fail[u] = f"{type(e).__name__}: {e}"[:200]

    report = bp.detect(frag_pages)
    if verbose:
        print(f"\n템플릿 조각 {len(report.hashes)}종 "
              f"(임계 {report.threshold_pages}/{report.total_pages}페이지) "
              f"{report.skipped_reason}")
        for d in report.detail[:5]:
            print(f"    {d['pages']:3} ({d['ratio']:.0%})  {d['sample'][:56]!r}")
        if report.borderline:
            print(f"  경계선 {len(report.borderline)}종 — 임계가 흔들릴 조짐이면 여기서 보인다")
            for d in report.borderline[:3]:
                print(f"    {d['pages']:3} ({d['ratio']:.0%})  {d['sample'][:56]!r}")

    # ---- 2차: 잘라내고 파싱해서 기록 -----------------------------------
    tally = {s: 0 for s in repo.PARSE_STATUSES}
    total_sections = 0
    conn = repo.connect(db_path)
    try:
        for r in rows:
            u = r["url"]
            common = {"page_url": u, "host": r["host"], "path": r["path"],
                      "kind": r["kind"], "discovered_at": stamp,
                      "last_attempt_at": stamp}

            if u in fetch_fail:
                code, msg = fetch_fail[u]
                repo.upsert_page(conn, parse_status="fetch_error",
                                 http_status=code, error_message=msg, **common)
                tally["fetch_error"] += 1
                continue

            try:
                res = SV.parse(html[u], page_url=u, boilerplate_report=report)
            except Exception as e:  # noqa: BLE001
                repo.upsert_page(conn, parse_status="parse_error", http_status=200,
                                 error_message=f"{type(e).__name__}: {e}"[:200],
                                 **common)
                tally["parse_error"] += 1
                continue

            chars = _content_chars(res.sections)
            status = "ok" if chars >= MIN_CONTENT_CHARS else "empty"
            note = None
            if status == "empty":
                # 파싱은 됐다. 구조가 깨진 게 아니라 원문에 알맹이가 없다.
                note = "HTML 에 본문이 없다 (스크립트로 그리는 페이지일 수 있음)"

            repo.upsert_page(
                conn, parse_status=status, http_status=200,
                last_success_at=stamp if status == "ok" else None,
                section_count=len(res.sections), leaf_count=len(res.leaves),
                table_count=sum(1 for s in res.sections if s.kind == "table"),
                empty_block_count=_empty_blocks(res.sections),
                content_chars=chars, pruned_nodes=res.pruned.get("pruned", 0),
                last_modified=res.last_modified, title=res.title,
                note=note, **common)
            total_sections += repo.replace_sections(
                conn, page_url=u, sections=res.sections, observed_at=stamp,
                page_last_modified=res.last_modified)
            tally[status] += 1
        conn.commit()
        summary = repo.coverage_summary(conn)
    finally:
        conn.close()

    if verbose:
        print(f"\n{'상태':14}{'페이지':>8}")
        for s, n in tally.items():
            if n:
                print(f"  {s:12}{n:>8}")
        print(f"  섹션 {total_sections} · 색인 잎 {summary['indexed_leaves']}")
        print(f"  답변 가능 비율 {summary['answerable_ratio']:.1%}")
    return {"tally": tally, "sections": total_sections, "summary": summary,
            "boilerplate": report.summary()}


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
    sys.exit(main())
