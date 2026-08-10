"""사이트 자동 발견 — BFS 로 URL 지도를 만든다.

    python -m crawler.discover --max-pages 300
    python -m crawler.discover --max-pages 1000 --out config/pages.yaml

배경
  sitemap.xml 이 없지만 모든 페이지가 전체 네비게이션을 렌더한다.
  아무 페이지에서 시작해 BFS 하면 전체 URL 이 나온다.

★ 우리는 손님이다
  robots.txt 는 `Allow: /` 이고 Crawl-delay 도 없지만, 우리 쪽에서 제한을 건다.
  · 요청 간격 기본 0.7초, 동시성 1 (순차)
  · 페이지 수 상한 필수 — 무한 크롤을 막는다
  · 게시글 상세는 **수집만 하고 펼치지 않는다.** 수천 건이라 BFS 가 거기서 익사한다.
    구조를 찾는 게 목적이지 글을 다 모으는 게 아니다.

★ '발견'과 '수집'은 다르다
  여기서 하는 건 URL 목록을 만드는 것뿐이다. 내용 파싱은 CMS 별 파서가 한다.
"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime as dt
import pathlib
import re
import sys
import time
import urllib.parse as up

import httpx
import yaml
from selectolax.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler import fetch as fetch_mod  # noqa: E402

UA = ("Mozilla/5.0 (compatible; JBNU-StudentCouncil-Bot/1.0; "
      "+https://github.com/wjddudals283-star/JBNU_CHATBOT)")

SEEDS = [
    "https://www.jbnu.ac.kr/web/index.do",
    "https://www.jbnu.ac.kr/web/news/notice/sub01.do",
    "https://www.jbnu.ac.kr/web/academic/schedule.do",
]

HOST_SUFFIX = ".jbnu.ac.kr"
EXPAND_HOSTS = {"www.jbnu.ac.kr"}          # 펼칠 호스트. 나머지는 수집만

SKIP_SCHEMES = ("javascript:", "mailto:", "tel:", "#")
SKIP_EXT = re.compile(
    r"\.(pdf|hwp|hwpx|docx?|xlsx?|pptx?|zip|jpe?g|png|gif|svg|webp|mp4|mp3|ico|css|js)(\?|$)",
    re.I)
# 로그인·다운로드·삭제 등 부작용이 있거나 의미 없는 경로
SKIP_PATH = re.compile(
    r"(logout|login|download|delete|update|write|checkPwd|actionView|"
    r"link\.do|search\.do|print|excel)", re.I)

# CMS 패턴 분류
PATTERNS = [
    ("board_detail", re.compile(r"(artclView|detailView)\.do", re.I)),
    ("board_list", re.compile(r"(lists?|bbs|board)\.do|/Board/", re.I)),
    ("subview", re.compile(r"subview\.do", re.I)),
    ("xhr", re.compile(r"(dataAjax|Ajax)\.do", re.I)),
    ("static_page", re.compile(r"\.do(\?|$)", re.I)),
]
# 펼치지 않는(수집만) 유형 — 여기서 BFS 가 익사한다
LEAF_KINDS = {"board_detail", "xhr"}


def classify(url: str) -> str:
    for name, pat in PATTERNS:
        if pat.search(url):
            return name
    return "other"


def normalize(url: str, base: str) -> str | None:
    if not url:
        return None
    u = url.strip()
    if u.lower().startswith(SKIP_SCHEMES):
        return None
    full = up.urljoin(base, u)
    p = up.urlsplit(full)
    if p.scheme not in ("http", "https"):
        return None
    host = p.hostname or ""
    if not (host == "jbnu.ac.kr" or host.endswith(HOST_SUFFIX)):
        return None
    if SKIP_EXT.search(p.path) or SKIP_PATH.search(p.path + "?" + p.query):
        return None
    # 조각 제거, 빈 쿼리 정리
    return up.urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


@dataclasses.dataclass
class Page:
    url: str
    kind: str
    depth: int
    status: int | None = None
    bytes: int = 0
    title: str = ""
    out_links: int = 0
    error: str | None = None


def discover(seeds: list[str] | None = None, *, max_pages: int = 300,
             delay: float = 0.7, max_depth: int = 6,
             expand_hosts: set[str] | None = None,
             on_progress=None) -> dict:
    seeds = seeds or SEEDS
    expand_hosts = expand_hosts or EXPAND_HOSTS

    queue: collections.deque[tuple[str, int]] = collections.deque()
    seen: set[str] = set()
    pages: dict[str, Page] = {}
    collected: dict[str, str] = {}      # 발견만 하고 안 펼친 URL → kind

    for s in seeds:
        n = normalize(s, s)
        if n and n not in seen:
            seen.add(n)
            queue.append((n, 0))

    fetched = 0
    started = time.perf_counter()
    with httpx.Client(timeout=30.0, verify=fetch_mod.lax_ssl(),
                      follow_redirects=True, headers={"User-Agent": UA}) as c:
        while queue and fetched < max_pages:
            url, depth = queue.popleft()
            kind = classify(url)
            host = up.urlsplit(url).hostname or ""

            # 펼치지 않는 유형·호스트는 목록에만 남긴다
            if kind in LEAF_KINDS or host not in expand_hosts or depth > max_depth:
                collected[url] = kind
                continue

            page = Page(url=url, kind=kind, depth=depth)
            try:
                r = c.get(url)
                page.status = r.status_code
                page.bytes = len(r.content)
                if r.status_code == 200 and "html" in (
                        r.headers.get("content-type") or ""):
                    tree = HTMLParser(r.text)
                    t = tree.css_first("title")
                    page.title = re.sub(r"\s+", " ", (t.text() if t else "")).strip()[:80]
                    found = 0
                    for a in tree.css("a"):
                        n = normalize(a.attributes.get("href", ""), str(r.url))
                        if not n or n in seen:
                            continue
                        seen.add(n)
                        found += 1
                        queue.append((n, depth + 1))
                    page.out_links = found
            except Exception as e:  # noqa: BLE001
                page.error = f"{type(e).__name__}: {e}"

            pages[url] = page
            fetched += 1
            if on_progress and fetched % 25 == 0:
                on_progress(fetched, len(queue), len(seen))
            time.sleep(delay)          # ★ 손님이다

    # 큐에 남은 것도 '발견됨'이다
    for url, _d in queue:
        collected.setdefault(url, classify(url))

    return {
        "fetched": fetched,
        "elapsed_sec": round(time.perf_counter() - started, 1),
        "discovered_total": len(seen),
        "queue_remaining": len(queue),
        "pages": pages,
        "collected": collected,
        "exhausted": not queue,        # ★ 큐가 비었으면 전수 발견이다
    }


def summarize(result: dict) -> dict:
    by_kind: collections.Counter = collections.Counter()
    by_host: collections.Counter = collections.Counter()
    for url in list(result["pages"]) + list(result["collected"]):
        by_kind[classify(url)] += 1
        by_host[up.urlsplit(url).hostname or "?"] += 1
    status = collections.Counter(
        p.status if p.status else "error" for p in result["pages"].values())
    return {"by_kind": dict(by_kind.most_common()),
            "by_host": dict(by_host.most_common(15)),
            "status": dict(status)}


def write_pages_yaml(result: dict, path: pathlib.Path) -> None:
    rows = []
    for url, p in sorted(result["pages"].items()):
        rows.append({"url": url, "kind": p.kind, "depth": p.depth,
                     "status": p.status, "title": p.title})
    for url, kind in sorted(result["collected"].items()):
        rows.append({"url": url, "kind": kind, "depth": None, "status": None,
                     "title": ""})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(
        {"generated_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(),
         "count": len(rows), "pages": rows},
        allow_unicode=True, sort_keys=False), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="사이트 URL 자동 발견 (BFS)")
    ap.add_argument("--max-pages", type=int, default=300)
    ap.add_argument("--delay", type=float, default=0.7)
    ap.add_argument("--max-depth", type=int, default=6)
    ap.add_argument("--out", default="config/pages.yaml")
    args = ap.parse_args(argv)

    print(f"robots.txt: Allow: / (제한 없음). 우리 쪽 간격 {args.delay}s · 동시성 1")
    print(f"상한 {args.max_pages}페이지 · 깊이 {args.max_depth}\n")

    def prog(fetched, queued, seen):
        print(f"  …{fetched}페이지 수집 · 큐 {queued} · 발견 누적 {seen}")

    result = discover(max_pages=args.max_pages, delay=args.delay,
                      max_depth=args.max_depth, on_progress=prog)
    s = summarize(result)

    print(f"\n{'='*66}")
    print(f"수집 {result['fetched']}페이지 / {result['elapsed_sec']}초")
    print(f"발견 URL 총 {result['discovered_total']}개")
    print(f"큐 잔여 {result['queue_remaining']}  "
          f"({'전수 발견 완료' if result['exhausted'] else '★ 상한에 걸림 — 더 있다'})")
    print(f"\nCMS 패턴별:")
    for k, v in s["by_kind"].items():
        print(f"  {k:16} {v:5}")
    print(f"\n호스트별 (상위 15):")
    for k, v in s["by_host"].items():
        print(f"  {k:32} {v:5}")
    print(f"\n응답 상태: {s['status']}")

    out = ROOT / args.out
    write_pages_yaml(result, out)
    print(f"\n저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
