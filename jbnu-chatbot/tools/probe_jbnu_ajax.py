"""조사 1 — jbnu.ac.kr 식단 XHR 엔드포인트 직접 호출.

발견: POST /web/unvrslife/campuslife/cafeteria/dataAjax.do   data: type=day|rest
      응답은 JSON 이 아니라 HTML 조각 ($('#cafeteriaWrap').html(data))
      date 파라미터 없음 → '이번 주'만 반환하는지 확인한다.

사용: python tools/probe_jbnu_ajax.py
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

BASE = "https://www.jbnu.ac.kr"
AJAX = f"{BASE}/web/unvrslife/campuslife/cafeteria/dataAjax.do"
PAGE = f"{BASE}/web/unvrslife/campuslife/cafeteria.do"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


def ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    c.set_ciphers("DEFAULT@SECLEVEL=1")
    return c


def main() -> None:
    report = {}
    with httpx.Client(timeout=30.0, verify=ctx(), follow_redirects=True,
                      headers={"User-Agent": UA}) as c:
        page = c.get(PAGE)  # 세션 + CSRF 토큰 확보
        m = re.search(r'<meta[^>]+name=["\']_csrf["\'][^>]+content=["\']([^"\']+)', page.text)
        token = m.group(1) if m else None
        print(f"CSRF 토큰: {token!r}  쿠키: {list(c.cookies.keys())}")
        report["csrf_token_found"] = bool(token)
        report["cookies"] = list(c.cookies.keys())

        AJ = {"Referer": PAGE, "AJAX": "true", "X-Requested-With": "XMLHttpRequest"}
        if token:
            AJ["X-CSRF-Token"] = token

        for typ in ("day", "rest"):
            r = c.post(AJAX, data={"type": typ}, headers=AJ)
            html = r.text
            (OUT / f"jbnu_dataAjax_{typ}.html").write_bytes(r.content)

            tree = HTMLParser(html)
            tables = tree.css("table")
            imgs = [n.attributes.get("src", "") for n in tree.css("img")]
            text = re.sub(r"\s+", " ", tree.text() or "").strip()

            info = {
                "status": r.status_code,
                "bytes": len(r.content),
                "content_type": r.headers.get("content-type"),
                "is_json": html.lstrip().startswith(("{", "[")),
                "tables": len(tables),
                "imgs": imgs[:10],
                "text_len": len(text),
            }
            print(f"\n=== type={typ} ===")
            print(f"  {info['status']} {info['bytes']}B ct={info['content_type']} "
                  f"tables={info['tables']} imgs={len(imgs)}")

            if tables:
                t = tables[0]
                rows = [[(cd.text() or '').strip() for cd in row.css("td,th")]
                        for row in t.css("tr")]
                info["row_count"] = len(rows)
                info["sample_rows"] = rows[:8]
                print(f"  rows={len(rows)}")
                for row in rows[:8]:
                    cells = [re.sub(r"\s+", " ", x)[:38] for x in row]
                    print(f"    {cells}")
            else:
                print(f"  본문 앞부분: {text[:400]!r}")

            # 날짜 표기 추출 → 어느 주를 반환하는지
            dates = sorted(set(re.findall(r"20\d{2}[-./]\d{1,2}[-./]\d{1,2}", html)))
            md = sorted(set(re.findall(r"\d{1,2}\s*월\s*\d{1,2}\s*일", html)))
            info["dates_found"] = dates[:20]
            info["kor_dates_found"] = md[:20]
            print(f"  날짜 표기: {dates[:10]}  한글날짜: {md[:10]}")

            # 가격 표기 존재 여부
            prices = sorted(set(re.findall(r"[\d,]{3,7}\s*원", text)))
            info["prices_found"] = prices[:20]
            print(f"  가격 표기: {prices[:15]}")

            report[typ] = info

        # date 파라미터를 받아주는지 시험
        print("\n=== date 파라미터 수용 여부 ===")
        base_len = None
        for extra in [{}, {"date": "20260601"}, {"sdate": "2026-06-01"},
                      {"searchDate": "2026-06-01"}]:
            r = c.post(AJAX, data={"type": "day", **extra}, headers=AJ)
            if base_len is None:
                base_len = len(r.content)
            same = "동일" if len(r.content) == base_len else "다름"
            print(f"  {extra or '(없음)'} → {len(r.content)}B ({same})")
            report[f"dateparam__{json.dumps(extra, sort_keys=True)}"] = len(r.content)

    (OUT / "jbnu_ajax_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {OUT / 'jbnu_ajax_probe.json'}")


if __name__ == "__main__":
    main()
