"""공지 게시판 원천 조사 — 작업 2 (최대 레버).

확인 항목
  a) 목록이 서버렌더 HTML 인가 / XHR 인가 / 이미지인가
  b) CSRF 가 필요한가 (식단·학사일정과 같은 패턴인가)
  c) 게시판이 몇 개인가 (교내공지 / 학생공지 / 장학 / 학사 …)
  d) 한 행에서 뽑을 수 있는 것 — 제목·게시일·링크·게시판·조회수
  e) 페이지네이션 파라미터
  f) 검색 파라미터가 있는가 (있으면 notice.search 가 훨씬 싸진다)

★ 구조화는 하지 않는다. 제목·게시일·링크·게시판만 긁는다.
  마감일 추출(T3)은 나중이다. 지금 필요한 건 "장학금 물으면 관련 공지 3건 + 링크"고,
  그게 0보다 훨씬 낫다.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

import httpx
from selectolax.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from crawler import fetch as fetch_mod  # noqa: E402

OUT = ROOT / "docs" / "probe"
OUT.mkdir(parents=True, exist_ok=True)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# 처음 조사에서 본 후보들 + 흔한 경로
BASE = "https://www.jbnu.ac.kr"
CANDIDATES = [
    ("교내공지", f"{BASE}/web/news/notice/sub01.do"),
    ("학생공지", f"{BASE}/web/news/notice/sub02.do"),
    ("교내채용", f"{BASE}/web/news/notice/sub03.do"),
    ("공지sub05", f"{BASE}/web/news/notice/sub05.do"),
    # ★ 사이트 검색. 있으면 notice.search 가 훨씬 싸진다
    ("검색_장학금", f"{BASE}/web/search.do?searchKeyword=장학금"),
]


def cells(row):
    return [n for n in row.iter(include_text=False) if n.tag in ("td", "th")]


def analyze(html: str) -> dict:
    tree = HTMLParser(html)
    tables = tree.css("table")
    info: dict = {
        "tables": len(tables),
        "has_loading": bool(re.search(r"Loading|로딩|불러오는", html)),
        "board_links": [],
        "xhr": [],
        "date_like": len(re.findall(r"20\d{2}[-.]\d{2}[-.]\d{2}", html)),
    }
    for s in tree.css("script"):
        for m in re.findall(r"""["']([^"']*\.(?:do|json)(?:\?[^"']*)?)["']""",
                            s.text() or ""):
            if re.search(r"board|notice|list|공지", m, re.I):
                info["xhr"].append(m)
    info["xhr"] = sorted(set(info["xhr"]))[:8]

    # 게시글 링크 후보
    hrefs = [a.attributes.get("href", "") for a in tree.css("a")]
    info["view_links"] = sorted({h for h in hrefs
                                 if re.search(r"view|detail|artclView|no=", h or "")})[:8]

    if tables:
        best, filled = None, 0
        for t in tables:
            rows = [[re.sub(r"\s+", " ", (c.text() or "")).strip() for c in cells(r)]
                    for r in t.css("tr")]
            f = sum(1 for r in rows for c in r if c)
            if f > filled:
                best, filled = rows, f
        info["biggest_table_rows"] = len(best or [])
        info["sample_rows"] = (best or [])[:6]
    return info


def main() -> None:
    report = {}
    with httpx.Client(timeout=30.0, verify=fetch_mod.lax_ssl(),
                      follow_redirects=True, headers={"User-Agent": UA}) as c:
        for name, url in CANDIDATES:
            print(f"\n{'='*70}\n[{name}] {url}")
            try:
                r = c.get(url)
            except Exception as e:  # noqa: BLE001
                print(f"  실패 {type(e).__name__}: {e}")
                report[name] = {"error": str(e)}
                continue
            print(f"  {r.status_code}  {len(r.content):,}B  final={r.url}")
            if r.status_code != 200:
                report[name] = {"status": r.status_code}
                continue
            (OUT / f"notice_{name}.html").write_bytes(r.content)
            info = analyze(r.text)
            report[name] = {"status": 200, "bytes": len(r.content),
                            "final_url": str(r.url), **info}
            print(f"  테이블 {info['tables']} · 날짜형 {info['date_like']}건 "
                  f"· 로딩표시 {info['has_loading']}")
            if info["xhr"]:
                print(f"  ★ XHR 후보: {info['xhr']}")
            if info["view_links"]:
                print(f"  글 링크 예: {info['view_links'][:3]}")
            for row in info.get("sample_rows", [])[:5]:
                print(f"    {[x[:30] for x in row]}")

    (OUT / "notice_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {OUT / 'notice_probe.json'}")


if __name__ == "__main__":
    main()
