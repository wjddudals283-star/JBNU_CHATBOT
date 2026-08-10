"""조사 1 보조 — jbnu.ac.kr 식단안내 페이지의 본문 영역만 떼어내 확인.

전역 GNB 가 214KB 대부분을 차지하므로 본문만 분리해서
(a) 식단 데이터가 서버렌더로 들어 있는지
(b) 없다면 어떤 스크립트/컨테이너가 채우는지
를 본다.

사용: python tools/probe_jbnu_content.py
"""

from __future__ import annotations

import pathlib
import re
import sys

from selectolax.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
SNAP = ROOT / "docs" / "probe" / "jbnu_cafeteria__browser_ua.html"

html = SNAP.read_text(encoding="utf-8", errors="replace")
tree = HTMLParser(html)

# GNB / 푸터를 제거해 본문만 남긴다
for sel in ["#gnb", "#header", "#foot", "#footer", "nav", ".menu-wrap",
            ".dep-list-01", ".dep-list-02", ".dep-list-03", "script", "style"]:
    for n in tree.css(sel):
        n.decompose()

body_text = re.sub(r"\n{3,}", "\n\n", (tree.body.text() if tree.body else "")).strip()
print("=== 본문 텍스트 (GNB 제거 후) ===")
print(body_text[:2500])
print(f"\n[본문 길이 {len(body_text)}자]")

print("\n=== 본문 내 컨테이너 후보 (id/class 에 menu/diet/food/cafeteria) ===")
tree2 = HTMLParser(html)
for n in tree2.css("div,section,table,ul,iframe"):
    a = n.attributes
    ident = f"{a.get('id','')} {a.get('class','')}"
    if re.search(r"menu|diet|food|cafeteria|siksa|meal", ident, re.I):
        inner = (n.text() or "").strip()[:80].replace("\n", " ")
        print(f"  <{n.tag} id={a.get('id')!r} class={a.get('class')!r}>  text={inner!r}")

print("\n=== 페이지 내 iframe / 외부 임베드 ===")
for n in tree2.css("iframe,embed,object"):
    print(f"  <{n.tag}> {dict(n.attributes)}")

print("\n=== 인라인 스크립트에서 .do / .php / .json 엔드포인트 추출 ===")
eps = set()
for s in tree2.css("script"):
    txt = s.text() or ""
    for m in re.findall(r"""["']([^"']*\.(?:do|php|json)(?:\?[^"']*)?)["']""", txt):
        if re.search(r"menu|diet|food|cafeteria|meal", m, re.I):
            eps.add(m)
for e in sorted(eps):
    print(f"  {e}")
if not eps:
    print("  (식단 관련 엔드포인트 없음)")
