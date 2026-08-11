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
import re
import logging
import pathlib
import sys
import time
import urllib.parse as up

import httpx
import yaml

from crawler import boilerplate as bp
from crawler import fetch as fetch_mod
from crawler import snapshots
from crawler.parsers import jbnu_subview as SV
from crawler.parsers import notice_list as NL
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))
log = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGES_YAML = ROOT / "config" / "pages.yaml"
DEPT_YAML = ROOT / "config" / "pages_dept.yaml"
SITES_YAML = ROOT / "config" / "pages_sites.yaml"
UA = "Mozilla/5.0 (compatible; JBNU-StudentCouncil-Bot/1.0)"
DEFAULT_DELAY = 0.7
# 본부는 /web/ 한국어 전체. 영문 사이트(/en/)·게시판 상세는 뺀다.
TARGET_PREFIXES = ("/web/",)
TARGET_HOST = "www.jbnu.ac.kr"
SKIP_KINDS = ("board_detail", "xhr")
# 이보다 본문이 짧으면 '내용 없음'으로 본다. 파싱은 됐지만 답할 것이 없는 상태다.
MIN_CONTENT_CHARS = 40
# 제목만 있고 알맹이가 없는 블록 (JS 로 그리는 영역) 판정 기준
EMPTY_BLOCK_CHARS = 20


def targets(limit: int | None = None, *, include_dept: bool = True) -> list[dict]:
    """본부 /web/ + 학과·기관 subview. 발견 결과에 있는 것만 간다."""
    out, seen = [], set()

    doc = yaml.safe_load(PAGES_YAML.read_text(encoding="utf-8"))
    for p in doc.get("pages", []):
        u = p["url"]
        sp = up.urlsplit(u)
        if sp.hostname != TARGET_HOST or p.get("kind") in SKIP_KINDS:
            continue
        if not sp.path.startswith(TARGET_PREFIXES) or p.get("status") != 200:
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "path": sp.path, "host": sp.hostname,
                    "kind": p.get("kind", "static_page")})

    if include_dept and DEPT_YAML.exists():
        ddoc = yaml.safe_load(DEPT_YAML.read_text(encoding="utf-8"))
        for p in ddoc.get("pages", []):
            u = p["url"]
            if u in seen:
                continue
            seen.add(u)
            out.append({"url": u, "path": up.urlsplit(u).path,
                        "host": p.get("host") or up.urlsplit(u).hostname,
                        "kind": "dept_subview"})

    if include_dept and SITES_YAML.exists():
        sdoc = yaml.safe_load(SITES_YAML.read_text(encoding="utf-8"))
        for p in sdoc.get("pages", []):
            u = p["url"]
            if u in seen:
                continue
            seen.add(u)
            out.append({"url": u, "path": up.urlsplit(u).path or "/",
                        "host": p.get("host") or up.urlsplit(u).hostname,
                        "kind": "site_page"})

    # 호스트를 번갈아 가며 돈다 — 한 사이트를 연달아 때리지 않기 위해서다.
    # 우리는 손님이고, 손님은 한 집 문만 두드리지 않는다.
    by_host: dict[str, list[dict]] = {}
    for r in out:
        by_host.setdefault(r["host"], []).append(r)
    for rows in by_host.values():
        rows.sort(key=lambda r: r["path"])
    mixed, hosts = [], list(by_host)
    while any(by_host[h] for h in hosts):
        for h in hosts:
            if by_host[h]:
                mixed.append(by_host[h].pop(0))
    return mixed[:limit] if limit else mixed


def _site_names() -> dict[str, str]:
    p = ROOT / "config" / "sites.yaml"
    if not p.exists():
        return {}
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    return {h: v.get("name", "") for h, v in (doc.get("sites") or {}).items()}


def _content_chars(sections) -> int:
    return sum(len(s.text) for s in sections if s.is_leaf or s.kind == "table")


def _empty_blocks(sections) -> int:
    """제목만 남은 블록 수 — 본문을 JS 로 그리는 페이지가 여기 걸린다."""
    return sum(1 for s in sections
               if s.kind == "block" and len(s.text) < EMPTY_BLOCK_CHARS)


CHUNK_SIZE = 400
# ★ 진행은 **묶음 경계가 아니라 일정 간격**으로 찍는다.
#   묶음 끝에서만 찍으면 묶음이 느려질 때 그만큼 침묵한다 —
#   실제로 4000/6985 이후 59분간 진행 로그가 0건이었고,
#   살아 있는지 멈췄는지 구별할 수 없었다.
#   스케줄러에서 고쳤던 것과 같은 문제가 다른 자리에서 났다.
PROGRESS_EVERY = 50
SLOW_FETCH_SEC = 5.0
# 재시작했을 때 이 시간 안에 이미 다녀온 페이지는 건너뛴다.
# 하루 1회 도는 작업이라 20시간이면 '이번 회차에 이미 했다' 는 뜻이 된다.
RESUME_WITHIN_HOURS = 20.0


def _process_chunk(rows, *, conn, stamp, delay, site_names, snapshot_root=None,
                   base: int = 0, grand_total: int = 0):
    """묶음 하나를 **끝까지** 처리한다 — 받고, 조각 세고, 파싱하고, 저장까지.

    ★ 전부 받은 뒤에 저장하면 중간에 죽었을 때 아무것도 안 남는다.
      배포가 잦은 날 세 번 다 처음부터 다시 시작했고, 50분짜리 작업이
      영영 안 끝났다. 묶음마다 커밋한다.
    """
    html: dict[str, str] = {}
    fetch_fail: dict[str, tuple] = {}
    slow = 0
    t_start = time.monotonic()
    with httpx.Client(timeout=30.0, verify=fetch_mod.lax_ssl(),
                      follow_redirects=True, headers={"User-Agent": UA}) as c:
        for j, r in enumerate(rows, 1):
            t0 = time.monotonic()
            try:
                resp = c.get(r["url"])
                if resp.status_code != 200:
                    fetch_fail[r["url"]] = (resp.status_code,
                                            f"HTTP {resp.status_code}")
                else:
                    html[r["url"]] = resp.text
                    # ★ 원문을 남긴다. 파서를 고쳤을 때 다시 긁지 않기 위해서다.
                    #   '원문이 바뀌었다 → 재수집 / 해석이 바뀌었다 → 재파싱'
                    if snapshot_root is not None:
                        snapshots.save(r["url"], resp.text, snapshot_root)
            except Exception as e:  # noqa: BLE001
                fetch_fail[r["url"]] = (None, f"{type(e).__name__}: {e}"[:200])
            took = time.monotonic() - t0
            if took >= SLOW_FETCH_SEC:
                slow += 1
            if j % PROGRESS_EVERY == 0:
                el = time.monotonic() - t_start
                # ★ 초/page 를 같이 찍는다. 느려지면 숫자가 먼저 말해준다.
                log.info("[pages] 진행 %s/%s · %.2fs/page · 느린요청 %s",
                         base + j, grand_total or len(rows), el / j, slow)
            time.sleep(delay)

    # CMS 마다 따로 센다. 섞으면 한쪽 템플릿이 다른 쪽 페이지 수에 희석된다.
    by_profile: dict[str, list] = {}
    page_profile: dict[str, str] = {}
    for u, h in html.items():
        try:
            key, frags = SV.fragments_with_profile(
                h, up.urlsplit(u).hostname or "")
            page_profile[u] = key
            by_profile.setdefault(key, []).append(frags)
        except Exception:  # noqa: BLE001
            pass
    reports = {k: bp.detect(v) for k, v in by_profile.items()}

    tally = {k: 0 for k in repo.PARSE_STATUSES}
    sections = notices = boards = 0
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

        # 이미 받아온 원문으로 게시판도 같이 읽는다 — 두 번 두드릴 이유가 없다.
        if NL.is_board_page(html[u]):
            nl = NL.parse(html[u], page_url=u)
            if nl.items:
                notices += repo.replace_notices(
                    conn, board_url=u, items=nl.items, host=r["host"],
                    board_name=nl.board_name,
                    site_name=site_names.get(r["host"], ""), observed_at=stamp)
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

        chars = _content_chars(res.sections)
        status = "ok" if chars >= MIN_CONTENT_CHARS else "empty"
        note = None
        if status == "empty":
            # ★ 'empty' 를 뭉쳐 세면 고칠 수 있는지 없는지 알 수가 없다.
            #   원문에 글자가 없으면 학교가 안 올린 것이고 (우리가 할 게 없음),
            #   글자는 있는데 섹션이 안 나오면 우리 파서가 못 읽은 것이다 (고칠 수 있음).
            raw_chars = len(re.sub(r"\s+", "", res.raw_text or ""))
            note = ("empty_nocontent 원문에 본문이 없다 (이미지·JS·빈 페이지)"
                    if raw_chars < MIN_CONTENT_CHARS
                    else f"empty_unparsed 원문 {raw_chars}자인데 섹션을 못 만들었다")
        repo.upsert_page(
            conn, parse_status=status, http_status=200,
            last_success_at=stamp if status == "ok" else None,
            section_count=len(res.sections), leaf_count=len(res.leaves),
            table_count=sum(1 for x in res.sections if x.kind == "table"),
            empty_block_count=_empty_blocks(res.sections),
            content_chars=chars, pruned_nodes=res.pruned.get("pruned", 0),
            last_modified=res.last_modified, title=res.title,
            note=note, **common)
        sections += repo.replace_sections(
            conn, page_url=u, sections=res.sections, observed_at=stamp,
            page_last_modified=res.last_modified)
        tally[status] += 1

    conn.commit()          # ★ 묶음이 끝날 때마다 남긴다
    if slow:
        log.info("[pages] 이 묶음에서 %ss 이상 걸린 요청 %s개 — 느려지면 여기가 원인이다",
                 SLOW_FETCH_SEC, slow)
    return tally, sections, boards, notices


def run(db_path: str, *, limit: int | None = None, delay: float = DEFAULT_DELAY,
        now: dt.datetime | None = None, verbose: bool = True,
        only_status: tuple[str, ...] | None = None,
        resume: bool = True) -> dict:
    """only_status 를 주면 그 상태인 페이지만 다시 돈다.

    파서를 고쳤다고 5,764곳을 또 두드리지 않는다. 우리는 손님이다.
    """
    now = now or dt.datetime.now(KST)
    stamp = now.isoformat()
    rows = targets(limit)
    if only_status:
        c0 = repo.connect(db_path)
        try:
            keep = {r[0] for r in c0.execute(
                "SELECT page_url FROM page_registry WHERE parse_status IN "
                f"({','.join('?' * len(only_status))})", tuple(only_status))}
        finally:
            c0.close()
        rows = [r for r in rows if r["url"] in keep]
    if verbose:
        print(f"대상 {len(rows)}페이지 · 간격 {delay}s · 동시성 1")

    if verbose:
        print(f"대상 {len(rows)}페이지 · 간격 {delay}s · 묶음 {CHUNK_SIZE}")

    snap_root = pathlib.Path(db_path).resolve().parent / "snapshots"
    conn = repo.connect(db_path)
    site_names = _site_names()
    tally = {k: 0 for k in repo.PARSE_STATUSES}
    total_sections = notices = boards = 0
    run_id = f"pages-{stamp}"
    try:
        repo.ensure_columns(conn)
        # ★ 재시작이면 이미 다녀온 곳은 건너뛴다.
        #   배포가 잦으면 매번 처음부터라 영영 안 끝난다 — 실제로 그랬다.
        if resume:
            cutoff = (now - dt.timedelta(hours=RESUME_WITHIN_HOURS)).isoformat()
            done = {r[0] for r in conn.execute(
                "SELECT page_url FROM page_registry WHERE last_attempt_at >= ?",
                (cutoff,))}
            before = len(rows)
            rows = [r for r in rows if r["url"] not in done]
            if before != len(rows):
                log.info("[pages] 이어서: %s페이지 건너뜀, 남은 %s",
                         before - len(rows), len(rows))
                if verbose:
                    print(f"  이어서: {before - len(rows)}페이지는 최근 "
                          f"{RESUME_WITHIN_HOURS:.0f}h 안에 다녀왔다 → 건너뜀. "
                          f"남은 {len(rows)}")

        repo.start_crawl(conn, run_id=run_id, source_key="jbnu_pages",
                         started_at=stamp)
        conn.commit()

        total = len(rows)
        for start in range(0, total, CHUNK_SIZE):
            chunk = rows[start:start + CHUNK_SIZE]
            t, sec, brd, ntc = _process_chunk(
                chunk, conn=conn, stamp=stamp, delay=delay,
                site_names=site_names, snapshot_root=snap_root,
                base=start, grand_total=total)
            for k, v in t.items():
                tally[k] += v
            total_sections += sec
            boards += brd
            notices += ntc
            done_n = min(start + CHUNK_SIZE, total)
            # ★ 완주했는지 중단됐는지 알 수 있어야 한다. 침묵이 가장 위험하다.
            conn.execute("UPDATE crawl_run SET items_parsed = ? WHERE id = ?",
                         (done_n, run_id))
            conn.commit()
            log.info("[pages] 진행 %s/%s (ok %s, empty %s, error %s)",
                     done_n, total, tally["ok"], tally["empty"],
                     tally["parse_error"] + tally["fetch_error"])
            if verbose:
                print(f"  [pages] 진행 {done_n}/{total}")

        repo.mark_boards(conn)
        repo.finish_crawl(conn, run_id, outcome="success",
                          finished_at=dt.datetime.now(KST).isoformat(),
                          items_parsed=total)
        conn.commit()
        summary = repo.coverage_summary(conn)
        log.info("[pages] DONE %s pages, indexed %s",
                 total, summary["indexed_leaves"])
    finally:
        conn.close()

    if verbose:
        print()
        for k, n in tally.items():
            if n:
                print(f"  {k:12}{n:>8}")
        print(f"  섹션 {total_sections} · 색인 잎 {summary['indexed_leaves']}")
        print(f"  게시판 {boards}곳 · 공지 {notices}건")
        print(f"  답변 가능 비율 {summary['answerable_ratio']:.1%}")
        u = snapshots.usage(snap_root)
        print(f"  원문 보관 {u['files']}개 · {u['mb']}MB "
              f"(재파싱은 crawler.pages_reparse — 네트워크 안 씀)")
    return {"tally": tally, "sections": total_sections, "summary": summary,
            "boards": boards, "notices": notices, "pages": total}
