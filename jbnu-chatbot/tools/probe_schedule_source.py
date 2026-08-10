"""작업 11 원천 조사 — 학사일정.

후보: https://www.jbnu.ac.kr/mobile/academic/schedule.do
확인: 이미지인가 / HTML 테이블인가 / XHR 인가. 파서 전략이 갈린다.
"""

from __future__ import annotations

import json
import pathlib
import re
import ssl
import sys

import httpx
from selectolax.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "probe"
OUT.mkdir(parents=True, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

CANDIDATES = [
    ("mobile_schedule", "https://www.jbnu.ac.kr/mobile/academic/schedule.do"),
    ("web_schedule", "https://www.jbnu.ac.kr/web/academic/schedule.do"),
    ("kr_schedule", "https://www.jbnu.ac.kr/kr/academic/schedule.do"),
]


def ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    c.set_ciphers("DEFAULT@SECLEVEL=1")
    return c


def row_cells(row):
    return [n for n in row.iter(include_text=False) if n.tag in ("td", "th")]


def analyze(name: str, html: str) -> dict:
    tree = HTMLParser(html)
    tables = tree.css("table")
    imgs = [n.attributes.get("src", "") for n in tree.css("img")]
    sched_imgs = [s for s in imgs if re.search(r"schedule|calendar|일정", s, re.I)]

    # 학사일정 특유의 키워드
    kw = {k: html.count(k) for k in
          ["수강신청", "개강", "종강", "휴학", "복학", "등록금", "학사일정", "방학"]}

    # XHR 후보
    eps = set()
    for s in tree.css("script"):
        for m in re.findall(r"""["']([^"']*\.(?:do|json|php)(?:\?[^"']*)?)["']""",
                            s.text() or ""):
            if re.search(r"schedule|calendar|academ", m, re.I):
                eps.add(m)

    info = {
        "tables": len(tables), "imgs": len(imgs),
        "schedule_like_imgs": sched_imgs[:6],
        "keyword_hits": kw,
        "xhr_candidates": sorted(eps),
        "has_loading": bool(re.search(r"Loading|로딩|불러오는", html)),
    }

    if tables:
        best, best_rows = None, 0
        for t in tables:
            rows = [[re.sub(r"\s+", " ", (c.text() or "")).strip()
                     for c in row_cells(r)] for r in t.css("tr")]
            filled = sum(1 for r in rows for c in r if c)
            if filled > best_rows:
                best, best_rows = rows, filled
        info["biggest_table_rows"] = len(best or [])
        info["biggest_table_filled_cells"] = best_rows
        info["sample_rows"] = (best or [])[:8]
    return info


def main() -> None:
    report = {}
    with httpx.Client(timeout=30.0, verify=ctx(), follow_redirects=True,
                      headers={"User-Agent": UA}) as c:
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

            (OUT / f"schedule_{name}.html").write_bytes(r.content)
            info = analyze(name, r.text)
            report[name] = {"status": 200, "bytes": len(r.content), **info}

            print(f"  테이블 {info['tables']}개 · 이미지 {info['imgs']}개 "
                  f"· 로딩표시 {info['has_loading']}")
            print(f"  키워드: { {k: v for k, v in info['keyword_hits'].items() if v} }")
            if info["schedule_like_imgs"]:
                print(f"  ★ 일정 관련 이미지: {info['schedule_like_imgs']}")
            if info["xhr_candidates"]:
                print(f"  ★ XHR 후보: {info['xhr_candidates']}")
            if info.get("sample_rows"):
                print(f"  가장 큰 표: {info['biggest_table_rows']}행 "
                      f"/ 채워진 칸 {info['biggest_table_filled_cells']}")
                for row in info["sample_rows"]:
                    print(f"    {[x[:26] for x in row]}")

    (OUT / "schedule_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {OUT / 'schedule_probe.json'}")


if __name__ == "__main__":
    main()
