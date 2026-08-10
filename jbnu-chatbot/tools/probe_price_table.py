"""조사 3 마무리 — 후생관 메뉴 단가표 전수 추출.

가격이 어디에 있는지 확정하고, rowspan 처리가 필요한지 본다.
이 표가 menu_item.price 의 유일한 공식 원천이다.

사용: python tools/probe_price_table.py
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

from selectolax.parser import HTMLParser

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "probe"

html = (OUT / "jbnu_dataAjax_day.html").read_text(encoding="utf-8", errors="replace")
tree = HTMLParser(html)
tables = tree.css("table")


def row_cells(row):
    out = []
    for n in row.iter(include_text=False):
        if n.tag in ("td", "th"):
            out.append({
                "tag": n.tag,
                "text": re.sub(r"\s+", " ", (n.text() or "")).strip(),
                "rowspan": int(n.attributes.get("rowspan") or 1),
                "colspan": int(n.attributes.get("colspan") or 1),
            })
    return out


print("=== table[9] 후생관 메뉴 단가표 — 전수 ===")
t = tables[9]
grid = [row_cells(r) for r in t.css("tr")]
has_span = any(c["rowspan"] > 1 or c["colspan"] > 1 for r in grid for c in r)
print(f"rowspan/colspan 사용: {has_span}\n")
for i, r in enumerate(grid):
    parts = []
    for c in r:
        sp = ""
        if c["rowspan"] > 1:
            sp += f"↕{c['rowspan']}"
        if c["colspan"] > 1:
            sp += f"↔{c['colspan']}"
        parts.append(f"{c['tag']}{sp}:{c['text']}")
    print(f"  [{i:2}] {parts}")

print("\n=== 진수원 / 의대식당 가격 테이블 (table[1], table[4], table[7]) ===")
for idx in (1, 4, 7):
    print(f"\n-- table[{idx}] --")
    for r in tables[idx].css("tr"):
        print(f"   {[c['text'] for c in row_cells(r)]}")

print("\n=== 운영시간 테이블 (table[0], table[3], table[6]) ===")
for idx, name in ((0, "진수원"), (3, "의대식당"), (6, "후생관")):
    print(f"\n-- table[{idx}] {name} --")
    for r in tables[idx].css("tr"):
        print(f"   {[c['text'] for c in row_cells(r)]}")

print("\n=== 후생관 식단표 table[8] 전수 (코너 구조 + rowspan) ===")
for i, r in enumerate(tables[8].css("tr")):
    cells = row_cells(r)
    parts = [f"{c['tag']}{'↕'+str(c['rowspan']) if c['rowspan']>1 else ''}:{c['text'][:30]}"
             for c in cells]
    print(f"  [{i:2}] ({len(cells)}칸) {parts}")

dump = {
    "price_table_rows": grid,
    "price_table_uses_span": has_span,
    "hours_tables": {n: [[c["text"] for c in row_cells(r)] for r in tables[i].css("tr")]
                     for i, n in ((0, "진수원"), (3, "의대식당"), (6, "후생관"))},
    "menu_tables_rowcells": {n: [row_cells(r) for r in tables[i].css("tr")]
                             for i, n in ((2, "진수원"), (5, "의대식당"), (8, "후생관"))},
}
(OUT / "jbnu_price_hours.json").write_text(
    json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n결과 저장: {OUT / 'jbnu_price_hours.json'}")
