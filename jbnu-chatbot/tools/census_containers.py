"""컨테이너 전수 조사 — CMS 템플릿이 몇 종인가.

표본 두세 개로 종류를 세는 건 관측이 아니라 추측이다.
파싱까지 가지 않고 **판별 특징만** 센다. 싸다.

    python tools/census_containers.py --limit 400
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys
import time
import urllib.parse as up

import httpx
import yaml
from selectolax.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from crawler import fetch as fetch_mod  # noqa: E402

OUT = ROOT / "docs" / "probe"
UA = "Mozilla/5.0 (compatible; JBNU-StudentCouncil-Bot/1.0)"


def fingerprint(html: str) -> dict:
    tree = HTMLParser(html)
    fp: dict = {}
    body = tree.css_first("#sp-content")
    fp["sp_content"] = body is not None
    if body is None:
        for alt in ("#content", "#contents", ".sub-content", "#pageBody",
                    ".contentsArea", "#container"):
            if tree.css_first(alt):
                fp["alt_container"] = alt
                break
    else:
        # #sp-content 안에서 본문을 담은 컨테이너 클래스 분포
        inner = body.css_first(".com-inner-1300") or body
        classes = []
        for n in inner.iter(include_text=False):
            c = (n.attributes.get("class") or "").strip()
            if c:
                classes.append(c.split()[0])
        fp["child_classes"] = classes[:12]

    fp["com_box_04"] = len(tree.css("div.com-box-04"))
    fp["tables"] = len(tree.css("table"))
    fp["ul"] = len(tree.css("ul"))
    fp["h2_title"] = len(tree.css("h2 .title"))
    fp["last_modified"] = bool(re.search(r"최종수정일.{0,200}?\d{4}-\d{2}-\d{2}",
                                         html, re.S))
    fp["is_error"] = len(html) < 6000 and "죄송" in html
    t = tree.css_first("title")
    fp["title"] = re.sub(r"\s+", " ", (t.text() if t else "")).strip()[:50]
    return fp


def kind_of(fp: dict) -> str:
    """판별 결과를 한 단어로."""
    if fp.get("is_error"):
        return "error_page"
    if not fp.get("sp_content"):
        return f"no_sp_content({fp.get('alt_container', '?')})"
    if fp.get("com_box_04", 0) > 0:
        return "sp_content+com_box_04"
    if fp.get("tables", 0) > 0:
        return "sp_content+table_only"
    return "sp_content+other"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=400)
    ap.add_argument("--delay", type=float, default=0.4)
    ap.add_argument("--host", default="www.jbnu.ac.kr",
                    help="이 호스트만. 'others' 면 www 를 뺀 나머지 전부")
    ap.add_argument("--out", default="container_census.json")
    ap.add_argument("--any-status", action="store_true",
                    help="발견만 되고 아직 안 가져온 URL(status=None)도 포함")
    ap.add_argument("--per-host", action="store_true",
                    help="호스트마다 1개만 — CMS 종류를 세는 데는 이걸로 충분하다")
    args = ap.parse_args(argv)

    doc = yaml.safe_load((ROOT / "config" / "pages.yaml").read_text(encoding="utf-8"))

    def want(u: str) -> bool:
        h = up.urlsplit(u).hostname
        return h != "www.jbnu.ac.kr" if args.host == "others" else h == args.host

    urls = [p["url"] for p in doc["pages"]
            if (want(p["url"])
                and (args.any_status or p.get("status") == 200)
                and p.get("kind") != "board_detail")]
    urls = sorted(set(urls))
    if args.per_host:
        # 호스트마다 하나만. 경로가 있는 쪽(index.do 등)을 루트보다 먼저 고른다.
        best: dict[str, str] = {}
        for u in urls:
            sp = up.urlsplit(u)
            cur = best.get(sp.hostname)
            if cur is None or (len(up.urlsplit(cur).path) <= 1 and len(sp.path) > 1):
                best[sp.hostname] = u
        urls = sorted(best.values())
    urls = urls[:args.limit]
    print(f"대상 {len(urls)}개 · 간격 {args.delay}s\n")

    rows = []
    kinds: collections.Counter = collections.Counter()
    with httpx.Client(timeout=30.0, verify=fetch_mod.lax_ssl(),
                      follow_redirects=True, headers={"User-Agent": UA}) as c:
        for i, u in enumerate(urls, 1):
            try:
                r = c.get(u)
                fp = fingerprint(r.text) if r.status_code == 200 else {"http": r.status_code}
            except Exception as e:  # noqa: BLE001
                fp = {"error": f"{type(e).__name__}"}
            k = kind_of(fp) if "error" not in fp and "http" not in fp else "fetch_fail"
            kinds[k] += 1
            path = up.urlsplit(u).path
            rows.append({"url": u, "path": path, "kind": k, **fp})
            if i % 40 == 0:
                print(f"  …{i}/{len(urls)}  누적 {dict(kinds.most_common(4))}")
            time.sleep(args.delay)

    print(f"\n{'='*66}\n컨테이너 종류 분포 (총 {len(rows)})")
    for k, v in kinds.most_common():
        print(f"  {k:34} {v:4}")

    print("\n한국어 학생 도메인만 (/web/academic, /web/unvrslife, /web/info)")
    sub = collections.Counter(
        r["kind"] for r in rows
        if r["path"].startswith(("/web/academic", "/web/unvrslife", "/web/info")))
    for k, v in sub.most_common():
        print(f"  {k:34} {v:4}")

    print("\n표가 있는 페이지 (본문 유실 위험)")
    with_table = [r for r in rows if r.get("tables", 0) > 0
                  and r["kind"].startswith("sp_content")]
    print(f"  {len(with_table)}개")
    for r in with_table[:8]:
        print(f"    표{r['tables']:2} box{r.get('com_box_04',0):2}  "
              f"{r['title'][:26]:28} {r['path']}")

    print("\nsp_content 가 없는 페이지 (별도 템플릿)")
    for r in [x for x in rows if x["kind"].startswith("no_sp_content")][:12]:
        print(f"    {r['title'][:26]:28} {r['path']}")

    (OUT / args.out).write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT / 'container_census.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
