"""학과·기관 사이트 페이지 발견.

주소 규칙 (전수 조사에서 확인 — docs/source_inventory.md)

    https://<host>.jbnu.ac.kr/            → 'site move' 스텁
      POST /subDomain/subDomainChk.do     → {"siteUrl": ".../<host>/index.do"}
    콘텐츠                                  /<host>/<id>/subview.do

  안내 페이지 링크는 **첫 화면 네비게이션에 거의 다 들어 있다**.
  4개 사이트 표본에서 한 단계 더 들어가도 늘어난 것은 평균 6% 뿐이었다.
  그래서 사이트당 요청 2번이면 된다 — 우리는 손님이다.

    python -m crawler.discover_dept --delay 0.5
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import pathlib
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

KST = dt.timezone(dt.timedelta(hours=9))
INVENTORY = ROOT / "docs" / "probe" / "source_inventory.json"
OUT_YAML = ROOT / "config" / "pages_dept.yaml"
UA = "Mozilla/5.0 (compatible; JBNU-StudentCouncil-Bot/1.0)"
# 게시판 상세·첨부는 안내 페이지가 아니다. 공지는 별도 크롤러가 담당한다.
SKIP_PATTERNS = ("/artclView.do", "/bbs/", "download.do", "/fileDownload",
                 "javascript:", "mailto:", "#")


def dept_hosts() -> list[dict]:
    if not INVENTORY.exists():
        raise SystemExit("source_inventory.json 이 없다 — tools/source_inventory.py 먼저")
    rows = json.loads(INVENTORY.read_text(encoding="utf-8"))
    return [r for r in rows
            if r.get("cms") == "dept_contentbuilder" and r.get("resolved_url")]


def subview_links(html: str, base: str, host: str) -> list[str]:
    out, seen = [], set()
    for a in HTMLParser(html).css("a"):
        href = a.attributes.get("href") or ""
        if any(p in href for p in SKIP_PATTERNS):
            continue
        u = up.urljoin(base, href)
        sp = up.urlsplit(u)
        if sp.hostname != host or "/subview.do" not in sp.path:
            continue
        clean = up.urlunsplit((sp.scheme, sp.netloc, sp.path, "", ""))
        if clean not in seen:
            seen.add(clean)
            out.append(clean)
    return out


def run(*, delay: float = 0.5, limit: int | None = None,
        verbose: bool = True) -> dict:
    hosts = dept_hosts()[:limit]
    if verbose:
        print(f"학과·기관 {len(hosts)}곳 · 간격 {delay}s · 동시성 1")

    pages: list[dict] = []
    per_host: collections.Counter = collections.Counter()
    failed: list[tuple[str, str]] = []

    with httpx.Client(timeout=25.0, verify=fetch_mod.lax_ssl(),
                      follow_redirects=True, headers={"User-Agent": UA}) as c:
        for i, h in enumerate(hosts, 1):
            host = h["host"]
            base = h["resolved_url"]
            try:
                r = c.get(base)
                links = subview_links(r.text, str(r.url), host)
            except Exception as e:  # noqa: BLE001
                failed.append((host, f"{type(e).__name__}"))
                time.sleep(delay)
                continue
            for u in links:
                pages.append({"url": u, "host": host, "kind": "dept_subview",
                              "site_title": h.get("title", "")})
            per_host[host] = len(links)
            if verbose and i % 25 == 0:
                print(f"  …{i}/{len(hosts)}  누적 페이지 {len(pages)}")
            time.sleep(delay)

    OUT_YAML.write_text(yaml.safe_dump(
        {"generated_at": dt.datetime.now(KST).isoformat(),
         "hosts": len(per_host), "count": len(pages), "pages": pages},
        allow_unicode=True, sort_keys=False), encoding="utf-8")

    if verbose:
        print(f"\n발견 {len(pages)}페이지 / 사이트 {len(per_host)}곳 "
              f"(실패 {len(failed)})")
        if per_host:
            vals = sorted(per_host.values())
            print(f"  사이트당 페이지  중앙값 {vals[len(vals)//2]} · "
                  f"최소 {vals[0]} · 최대 {vals[-1]}")
        print("  많은 곳:", [f"{k}:{v}" for k, v in per_host.most_common(5)])
        empty = [k for k, v in per_host.items() if v == 0]
        # 0개인 곳을 조용히 넘기지 않는다 — 발견 실패인지 정말 없는 것인지 갈려야 한다
        print(f"  링크 0개인 사이트 {len(empty)}곳 {empty[:8]}")
        for host, err in failed[:8]:
            print(f"  실패 {host}: {err}")
        print(f"저장: {OUT_YAML}")
    return {"pages": len(pages), "hosts": len(per_host), "failed": len(failed)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)
    run(delay=args.delay, limit=args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
