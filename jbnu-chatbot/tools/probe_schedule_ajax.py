"""학사일정 XHR 직접 호출 — 파라미터·응답 형태 확인."""

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
PAGE = "https://www.jbnu.ac.kr/web/academic/schedule.do"
AJAX = "https://www.jbnu.ac.kr/web/academic/schedule/dataAjax.do"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def row_cells(row):
    return [n for n in row.iter(include_text=False) if n.tag in ("td", "th")]


def show(label: str, r: httpx.Response) -> None:
    print(f"\n[{label}] {r.status_code}  {len(r.content):,}B  "
          f"ct={r.headers.get('content-type')}")
    body = r.text
    if r.status_code != 200:
        print(f"  본문: {re.sub(r'<[^>]+>', ' ', body)[:200].strip()}")
        return
    if body.lstrip().startswith(("{", "[")):
        print(f"  JSON: {body[:400]}")
        return
    tree = HTMLParser(body)
    tables = tree.css("table")
    print(f"  HTML 조각 · 테이블 {len(tables)}개")
    for ti, t in enumerate(tables[:2]):
        rows = [[re.sub(r"\s+", " ", (c.text() or "")).strip()
                 for c in row_cells(rr)] for rr in t.css("tr")]
        print(f"  table[{ti}] {len(rows)}행")
        for row in rows[:10]:
            print(f"    {[x[:34] for x in row]}")


def main() -> None:
    with httpx.Client(timeout=30.0, verify=fetch_mod.lax_ssl(),
                      follow_redirects=True, headers={"User-Agent": UA}) as c:
        page = c.get(PAGE)
        m = re.search(r'<meta[^>]+name=["\']_csrf["\'][^>]+content=["\']([^"\']+)',
                      page.text)
        token = m.group(1) if m else None
        print(f"CSRF 토큰 {'있음' if token else '없음'} · 쿠키 {list(c.cookies.keys())}")

        # 페이지 인라인 스크립트에서 호출 파라미터 찾기
        idx = page.text.find("schedule/dataAjax.do")
        if idx > 0:
            snippet = re.sub(r"\n\s*\n", "\n", page.text[idx:idx + 400])
            print(f"\n=== 호출 파라미터 ===\n{snippet[:300]}")

        h = {"Referer": PAGE, "AJAX": "true", "X-Requested-With": "XMLHttpRequest"}
        if token:
            h["X-CSRF-Token"] = token

        show("토큰없이", c.post(AJAX, data={}, headers={"Referer": PAGE}))
        # 인라인 스크립트에서 확인한 실제 파라미터: type / acSemester / acYear
        for params in ({"type": "yearly", "acYear": "2026", "acSemester": "2"},
                       {"type": "monthly", "acYear": "2026", "acSemester": "2"},
                       {"type": "yearly", "acYear": "2027", "acSemester": "1"}):
            r = c.post(AJAX, data=params, headers=h)
            show(f"POST {params}", r)
            if r.status_code == 200:
                tag = f"{params['type']}_{params['acYear']}_{params['acSemester']}"
                (OUT / f"schedule_ajax_{tag}.html").write_bytes(r.content)


if __name__ == "__main__":
    main()
