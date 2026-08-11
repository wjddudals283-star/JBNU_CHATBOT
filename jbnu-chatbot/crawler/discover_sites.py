"""개별 구조 사이트 발견 — 생활관·도서관·대학원 등.

학과 CMS(207곳)와 본부는 구조가 하나씩이라 전용 발견기가 있다.
나머지는 사이트마다 구조가 다르다. 그래도 **링크를 따라가는 방식은 같다** —
같은 호스트 안에서 두 단계까지 훑고, 본문이 아닌 것이 뻔한 주소는 뺀다.

우리는 손님이다. 호스트를 번갈아 돌고 간격을 지킨다.

    python -m crawler.discover_sites --delay 0.6
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import pathlib
import sys
import urllib.parse as up
import time

import httpx
import yaml
from selectolax.parser import HTMLParser

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from crawler import fetch as fetch_mod  # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
CONFIG = ROOT / "config" / "sites_extra.yaml"
OUT_YAML = ROOT / "config" / "pages_sites.yaml"
UA = "Mozilla/5.0 (compatible; JBNU-StudentCouncil-Bot/1.0)"

# 본문이 아닌 것이 주소에서 이미 드러나는 것들
SKIP_TOKENS = ("login", "logout", "join", "download", "fileDown", "print",
               "javascript:", "mailto:", "#", ".pdf", ".hwp", ".zip", ".jpg",
               ".png", "sMenu=login", "backUrl=", "search", "rss")
MAX_PER_HOST = 200
MAX_DEPTH = 2


def load_targets() -> list[dict]:
    doc = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    return [s for s in doc.get("sites", []) if s.get("start")]


def _same_host_links(html: str, base: str, host: str) -> list[str]:
    out, seen = [], set()
    for a in HTMLParser(html).css("a"):
        href = a.attributes.get("href") or ""
        if not href or any(t in href for t in SKIP_TOKENS):
            continue
        u = up.urljoin(base, href)
        sp = up.urlsplit(u)
        if sp.hostname != host:
            continue
        clean = up.urlunsplit((sp.scheme, sp.netloc, sp.path, sp.query, ""))
        if clean in seen or any(t in clean for t in SKIP_TOKENS):
            continue
        seen.add(clean)
        out.append(clean)
    return out


def run(*, delay: float = 0.6, verbose: bool = True) -> dict:
    targets = load_targets()
    if verbose:
        print(f"사이트 {len(targets)}곳 · 간격 {delay}s · 깊이 {MAX_DEPTH}")

    pages: list[dict] = []
    per_host: collections.Counter = collections.Counter()
    failed: list[tuple[str, str]] = []

    with httpx.Client(timeout=25.0, verify=fetch_mod.lax_ssl(),
                      follow_redirects=True, headers={"User-Agent": UA}) as c:
        for site in targets:
            host = site["host"]
            frontier = [(site["start"], 0)]
            seen = {site["start"]}
            found: list[str] = []
            while frontier and len(found) < MAX_PER_HOST:
                url, depth = frontier.pop(0)
                try:
                    r = c.get(url)
                except Exception as e:  # noqa: BLE001
                    failed.append((url, type(e).__name__))
                    time.sleep(delay)
                    continue
                if r.status_code == 200:
                    found.append(url)
                    if depth < MAX_DEPTH:
                        for u in _same_host_links(r.text, str(r.url), host):
                            if u not in seen and len(seen) < MAX_PER_HOST * 2:
                                seen.add(u)
                                frontier.append((u, depth + 1))
                time.sleep(delay)
            for u in found:
                pages.append({"url": u, "host": host, "kind": "site_page",
                              "site_title": site.get("name", "")})
            per_host[host] = len(found)
            if verbose:
                print(f"  {host:26} {len(found):4}페이지")

    OUT_YAML.write_text(yaml.safe_dump(
        {"generated_at": dt.datetime.now(KST).isoformat(),
         "hosts": len(per_host), "count": len(pages), "pages": pages},
        allow_unicode=True, sort_keys=False), encoding="utf-8")
    if verbose:
        print(f"\n발견 {len(pages)}페이지 / {len(per_host)}곳 (실패 {len(failed)})")
        # 0개인 곳을 조용히 넘기지 않는다 — 못 찾은 것과 없는 것은 다르다
        empty = [h for h, v in per_host.items() if v == 0]
        if empty:
            print(f"  0페이지인 곳 {len(empty)}곳: {empty}")
        print(f"저장: {OUT_YAML}")
    return {"pages": len(pages), "hosts": len(per_host)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.6)
    args = ap.parse_args(argv)
    run(delay=args.delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
