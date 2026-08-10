"""CMS 3종 구조 조사 — 본부·학과·부속기관에서 정말 같은가.

확인 항목
  a) subview.do 가 본부/학과/부속기관에서 같은 구조인가
  b) '최종수정일'이 모든 subview.do 에 있는가 (개정 앵커로 쓸 수 있는가)
  c) artclView.do 게시판 구조가 동일한가
  d) 학과 사이트가 www 와 다른 CMS 를 쓰는가
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
import time

import httpx
from selectolax.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from crawler import fetch as fetch_mod  # noqa: E402

OUT = ROOT / "docs" / "probe"
UA = ("Mozilla/5.0 (compatible; JBNU-StudentCouncil-Bot/1.0)")

TARGETS = [
    # (구분, URL)
    ("본부/학사안내", "https://www.jbnu.ac.kr/web/academic/curriculum/sub01.do"),
    ("본부/장학금", "https://www.jbnu.ac.kr/web/academic/scholarship/sub02.do"),
    ("본부/휴복학", "https://www.jbnu.ac.kr/web/academic/record/sub01.do"),
    ("본부/식단", "https://www.jbnu.ac.kr/web/unvrslife/campuslife/cafeteria.do"),
    ("본부/공지목록", "https://www.jbnu.ac.kr/web/news/notice/sub01.do"),
    ("부속/중앙도서관", "https://dl.jbnu.ac.kr/"),
    ("부속/생활관", "https://likehome.jbnu.ac.kr/home/main/inner.php?sMenu=B7100"),
    ("부속/취업", "https://career.jbnu.ac.kr/"),
    ("부속/대학원", "https://graduate.jbnu.ac.kr/"),
    ("학과/공대", "https://www.jbnu.ac.kr/web/unvr/university.do?unq=178"),
]


def probe(c: httpx.Client, label: str, url: str) -> dict:
    info: dict = {"label": label, "url": url}
    try:
        r = c.get(url)
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}: {e}"
        return info

    info["status"] = r.status_code
    info["bytes"] = len(r.content)
    info["final_url"] = str(r.url)
    if r.status_code != 200:
        return info

    html = r.text
    tree = HTMLParser(html)

    # a) 섹션 헤딩
    heads = [re.sub(r"\s+", " ", (n.text() or "")).strip()
             for n in tree.css("h3, h4")]
    heads = [h for h in heads if h][:8]
    info["headings"] = heads
    info["h3"] = len(tree.css("h3"))
    info["h4"] = len(tree.css("h4"))

    # b) 최종수정일 — 개정 앵커 후보
    m = re.search(r"최종\s*수정일[^0-9]{0,20}(\d{4}[-.]\d{1,2}[-.]\d{1,2})", html)
    info["last_modified"] = m.group(1) if m else None
    info["has_last_modified_label"] = "최종수정일" in html

    # c) CMS 지문
    info["cms"] = {
        "subview": "subview.do" in html,
        "artclView": "artclView" in html,
        "detailView": "detailView.do" in html,
        "dataAjax": "dataAjax.do" in html,
        "csrf_meta": bool(re.search(r'name=["\']_csrf["\']', html)),
        "jsessionid": "JSESSIONID" in (r.headers.get("set-cookie") or ""),
    }
    info["tables"] = len(tree.css("table"))
    # 본문 컨테이너 후보
    for sel in ("#sp-content", ".com-inner-1300", "#contents", "#content",
                ".sub-content", "#pageBody"):
        if tree.css_first(sel):
            info.setdefault("content_selectors", []).append(sel)
    return info


def main() -> None:
    rows = []
    with httpx.Client(timeout=30.0, verify=fetch_mod.lax_ssl(),
                      follow_redirects=True, headers={"User-Agent": UA}) as c:
        for label, url in TARGETS:
            r = probe(c, label, url)
            rows.append(r)
            if "error" in r:
                print(f"[{label:16}] 실패 {r['error'][:60]}")
            else:
                lm = r.get("last_modified") or ("라벨만" if r.get("has_last_modified_label") else "없음")
                print(f"[{label:16}] {r['status']} {r['bytes']:>8,}B  "
                      f"h3={r['h3']} h4={r['h4']} 표={r['tables']}  최종수정일={lm}")
                print(f"                   CMS={ {k: v for k, v in r['cms'].items() if v} }")
                if r.get("headings"):
                    print(f"                   헤딩: {r['headings'][:4]}")
                if r.get("content_selectors"):
                    print(f"                   본문 컨테이너: {r['content_selectors']}")
            time.sleep(0.6)

    (OUT / "cms_probe.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT / 'cms_probe.json'}")


if __name__ == "__main__":
    main()
