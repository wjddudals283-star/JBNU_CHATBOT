"""정보원 인벤토리 — 우리 기술로 무엇까지 닿을 수 있는지 전수로 센다.

범위를 정하기 전에 잰다. 표본 두세 개로 종류를 세는 건 관측이 아니라 추측이다.

호스트마다 묻는 것:
  1. 살아 있나                     (연결·HTTP)
  2. 진짜 주소가 어디인가            ('site move' 스텁을 CMS 에 물어본다)
  3. 어떤 CMS 인가                  (본문 컨테이너 지문)
  4. 로그인 벽 뒤인가                (크롤로 못 넘는 벽)
  5. robots.txt 가 막고 있나         (우리는 손님이다)
  6. 게시판이 있나 / 규모는           (링크 수)

  파싱까지 가지 않는다. 판별 특징만 센다. 싸다.

    python tools/source_inventory.py --delay 0.35
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
import urllib.robotparser as rp

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

# 본문 컨테이너 지문 → CMS 이름. 관측으로 확인된 것만 넣는다.
CMS_MARKERS = (
    ("jbnu_www", "#sp-content"),                 # 본부 — 파서 있음
    ("dept_contentbuilder", "#_contentBuilder"),  # 학과·기관 통합 CMS
    ("board_artcl", ".artclView"),
)
# 로그인 벽 신호 — 크롤로는 영원히 못 넘는다
LOGIN_HINTS = ("sso.jbnu.ac.kr", "login.do", "type=\"password\"", "로그인이 필요",
               "아이디", "비밀번호를 입력")
BOARD_HINTS = ("artclList", "/bbs/", "subview.do", "detailView.do", "board")

# 학생 수요가 높은데 발견 목록에 없을 수 있는 곳 — 직접 확인한다.
EXTRA_HOSTS = [
    "www.jbnu.ac.kr", "dorm.jbnu.ac.kr", "dormitory.jbnu.ac.kr",
    "lib.jbnu.ac.kr", "dl.jbnu.ac.kr", "job.jbnu.ac.kr", "career.jbnu.ac.kr",
    "sugang.jbnu.ac.kr", "portal.jbnu.ac.kr", "my.jbnu.ac.kr",
    "counsel.jbnu.ac.kr", "health.jbnu.ac.kr", "gym.jbnu.ac.kr",
    "coop.jbnu.ac.kr", "startup.jbnu.ac.kr", "int.jbnu.ac.kr",
    "oia.jbnu.ac.kr", "museum.jbnu.ac.kr", "press.jbnu.ac.kr",
    "eduinfo.jbnu.ac.kr", "cse.jbnu.ac.kr",
]


def hosts_from_pages() -> list[str]:
    p = ROOT / "config" / "pages.yaml"
    if not p.exists():
        return []
    doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    return sorted({up.urlsplit(x["url"]).hostname for x in doc.get("pages", [])
                   if up.urlsplit(x["url"]).hostname})


def robots_allows(client: httpx.Client, host: str, path: str = "/") -> bool | None:
    """robots.txt 가 우리를 막는가. 못 읽으면 None (모른다)."""
    try:
        r = client.get(f"https://{host}/robots.txt")
        if r.status_code != 200 or "Disallow" not in r.text:
            return True
        parser = rp.RobotFileParser()
        parser.parse(r.text.splitlines())
        return parser.can_fetch(UA, f"https://{host}{path}")
    except Exception:  # noqa: BLE001
        return None


def resolve(client: httpx.Client, host: str) -> tuple[str, str]:
    """'site move' 스텁이면 CMS 에 진짜 주소를 물어본다."""
    base = f"https://{host}/"
    try:
        r = client.get(base)
    except Exception as e:  # noqa: BLE001
        return "", f"{type(e).__name__}"
    if "site move" not in r.text and len(r.text) > 8000:
        return str(r.url), ""
    try:
        j = client.post(f"https://{host}/subDomain/subDomainChk.do").json()
        u = j.get("siteUrl") or ""
        if u and u != "noSubDomain":
            return u, ""
    except Exception:  # noqa: BLE001
        pass
    m = re.search(r'http-equiv="refresh"[^>]*url=([^"\']+)', r.text, re.I)
    return (up.urljoin(base, m.group(1)) if m else str(r.url)), ""


def inspect(html: str, url: str) -> dict:
    t = HTMLParser(html)
    ti = t.css_first("title")
    cms = "unknown"
    for name, sel in CMS_MARKERS:
        if t.css_first(sel):
            cms = name
            break
    links = t.css("a")
    hrefs = [a.attributes.get("href") or "" for a in links]
    boards = sum(1 for h in hrefs if any(b in h for b in BOARD_HINTS))
    low = html.lower()
    return {
        "cms": cms,
        "title": re.sub(r"\s+", " ", ti.text() if ti else "").strip()[:40],
        "bytes": len(html),
        "links": len(links),
        "board_links": boards,
        "requires_login": any(h.lower() in low for h in LOGIN_HINTS),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--delay", type=float, default=0.35)
    ap.add_argument("--limit", type=int, default=400)
    args = ap.parse_args(argv)

    hosts = sorted(set(hosts_from_pages()) | set(EXTRA_HOSTS))[:args.limit]
    print(f"호스트 {len(hosts)}개 · 간격 {args.delay}s · 동시성 1\n")

    rows: list[dict] = []
    with httpx.Client(timeout=20.0, verify=fetch_mod.lax_ssl(),
                      follow_redirects=True, headers={"User-Agent": UA}) as c:
        for i, h in enumerate(hosts, 1):
            row: dict = {"host": h}
            row["robots_allows"] = robots_allows(c, h)
            time.sleep(args.delay)

            url, err = resolve(c, h)
            row["resolved_url"] = url
            if err:
                row.update(status="dead", error=err, cms="-")
                rows.append(row)
                if i % 25 == 0:
                    print(f"  …{i}/{len(hosts)}")
                continue
            time.sleep(args.delay)

            try:
                r = c.get(url)
                row["http"] = r.status_code
                if r.status_code == 200:
                    row.update(inspect(r.text, url))
                    row["status"] = ("login_wall" if row["requires_login"]
                                     and row["board_links"] == 0 else "open")
                else:
                    row.update(status="http_error", cms="-")
            except Exception as e:  # noqa: BLE001
                row.update(status="dead", error=f"{type(e).__name__}", cms="-")
            rows.append(row)
            if i % 25 == 0:
                seen = collections.Counter(x["status"] for x in rows)
                print(f"  …{i}/{len(hosts)}  {dict(seen)}")
            time.sleep(args.delay)

    print(f"\n{'='*70}\n=== 접근 상태 ===")
    for k, v in collections.Counter(x["status"] for x in rows).most_common():
        print(f"  {k:14} {v:4}")

    print("\n=== CMS 종류 (열린 것만) ===")
    live = [x for x in rows if x["status"] in ("open", "login_wall")]
    for k, v in collections.Counter(x.get("cms", "-") for x in live).most_common():
        print(f"  {k:24} {v:4}")

    print("\n=== 로그인 벽 뒤 ===")
    for x in [y for y in rows if y.get("requires_login")][:14]:
        print(f"  {x['host']:28} {x.get('title', '')[:30]}")

    print("\n=== robots.txt 가 막는 곳 ===")
    blocked = [x for x in rows if x.get("robots_allows") is False]
    print(f"  {len(blocked)}개  {[x['host'] for x in blocked][:10]}")

    print("\n=== 게시판이 많은 곳 (공지 크롤 후보) ===")
    for x in sorted(live, key=lambda r: -r.get("board_links", 0))[:12]:
        print(f"  링크{x.get('links',0):4} 게시판{x.get('board_links',0):4} "
              f"{x.get('cms','-'):22} {x['host']:28} {x.get('title','')[:24]}")

    print("\n=== 죽은 호스트 ===")
    dead = [x for x in rows if x["status"] == "dead"]
    print(f"  {len(dead)}개  {[x['host'] for x in dead][:12]}")

    (OUT / "source_inventory.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT / 'source_inventory.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
