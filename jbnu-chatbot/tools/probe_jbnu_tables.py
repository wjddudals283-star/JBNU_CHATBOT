"""조사 1 심화 — dataAjax.do(type=day) 응답 구조 해부.

목적
  a) css("td,th") 가 문서 순서를 지키는지 검증 (앞선 프로브에서 라벨이 행 끝에 온 원인)
  b) 10개 테이블이 각각 무엇인지 — 가격표 / 운영시간 / 요일별 식단
  c) 가격 데이터의 소속(식당·코너·품목)을 확인 — menu_item.price 채울 수 있는가

사용: python tools/probe_jbnu_tables.py
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


def cells_in_order(row):
    """자식 노드를 순회해 문서 순서대로 td/th 를 얻는다."""
    out = []
    for n in row.iter(include_text=False):
        if n.tag in ("td", "th"):
            out.append((n.tag, re.sub(r"\s+", " ", (n.text() or "")).strip()))
    return out


def main() -> None:
    report = {}
    for typ in ("day", "rest"):
        path = OUT / f"jbnu_dataAjax_{typ}.html"
        html = path.read_text(encoding="utf-8", errors="replace")
        tree = HTMLParser(html)
        tables = tree.css("table")
        print(f"\n{'='*70}\n=== type={typ} — 테이블 {len(tables)}개 ===")

        tinfo = []
        for ti, t in enumerate(tables):
            cap = t.css_first("caption")
            caption = re.sub(r"\s+", " ", (cap.text() or "").strip()) if cap else None
            rows = t.css("tr")

            # (a) 두 방식 비교
            doc_order = [cells_in_order(r) for r in rows]
            sel_order = [[(n.tag, re.sub(r"\s+", " ", (n.text() or "")).strip())
                          for n in r.css("td,th")] for r in rows]
            mismatch = doc_order != sel_order

            body = re.sub(r"\s+", " ", (t.text() or "")).strip()
            prices = re.findall(r"[\d,]{3,7}\s*원", body)

            print(f"\n--- table[{ti}] caption={caption!r} rows={len(rows)} "
                  f"가격표기={len(prices)}건  순서불일치={mismatch} ---")
            for r_ in doc_order[:6]:
                print(f"   {[f'{tag}:{v[:34]}' for tag, v in r_]}")
            if len(doc_order) > 6:
                print(f"   ... (+{len(doc_order)-6} rows)")

            tinfo.append({
                "index": ti, "caption": caption, "rows": len(rows),
                "price_tokens": len(prices),
                "css_order_differs_from_doc_order": mismatch,
                "doc_order_rows": doc_order[:12],
            })
        report[typ] = tinfo

        # 제목(h/strong) 추출 — 테이블이 어느 식당·구역에 속하는지
        heads = [re.sub(r"\s+", " ", (n.text() or "").strip())
                 for n in tree.css("h1,h2,h3,h4,h5,strong,.tit,.title")]
        heads = [h for h in heads if h][:40]
        print(f"\n[type={typ}] 문서 내 제목 요소: {heads}")
        report[f"{typ}__headings"] = heads

    (OUT / "jbnu_tables_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {OUT / 'jbnu_tables_probe.json'}")


if __name__ == "__main__":
    main()
