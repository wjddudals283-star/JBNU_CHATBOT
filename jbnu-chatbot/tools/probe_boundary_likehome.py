"""조사 마무리 — (1) 생협 API 데이터 커버리지 경계, (2) 대조군 likehome 표 구조.

확인 항목
  e) 미래 날짜 어디까지 데이터가 있는가 = '아직 안 올라옴(unknown)' 경계
  f) 'day' 필드 의미 (요일 인덱스?)
  g) likehome 주간식단표가 서버렌더 HTML 테이블인가 / 방학에 비는가
     + date 파라미터가 주 시작일로 정규화되는지

사용: python tools/probe_boundary_likehome.py
"""

from __future__ import annotations

import datetime as dt
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

API = "https://coopjbnu.kr/function/get_cafeteria_menu.php"
LIKEHOME = "https://likehome.jbnu.ac.kr/home/main/inner.php"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
WD = "월화수목금토일"


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

        # ── f) day 필드 의미 ────────────────────────────────────────────
        print("=== f) 'day' 필드 의미 ===")
        day_map = {}
        for d in ["20260810", "20260811", "20260812", "20260813", "20260814",
                  "20260815", "20260816"]:
            j = c.post(API, data={"date": d, "now": "Y"}).json()
            lst = j.get("list", [])
            real = dt.date.fromisoformat(f"{d[:4]}-{d[4:6]}-{d[6:]}")
            got = {i.get("day") for i in lst}
            day_map[d] = {"weekday_ko": WD[real.weekday()], "day_field": sorted(got),
                          "records": len(lst)}
            print(f"  {d} ({WD[real.weekday()]}) → day={sorted(got)}  list={len(lst)}")
        report["day_field"] = day_map

        # ── e) 미래 커버리지 경계 ────────────────────────────────────────
        print("\n=== e) 미래 날짜 커버리지 경계 (POST now=Y, 1일 단위) ===")
        base = dt.date(2026, 8, 10)
        coverage = {}
        last_ok = None
        for off in range(0, 45):
            d = base + dt.timedelta(days=off)
            key = d.strftime("%Y%m%d")
            j = c.post(API, data={"date": key, "now": "Y"}).json()
            n = len(j.get("list", []))
            coverage[key] = n
            if n:
                last_ok = key
            mark = "●" if n else "·"
            print(f"  {key} ({WD[d.weekday()]}) {mark} {n}", end="\n" if off % 1 == 0 else "")
        print(f"\n  → 데이터가 존재하는 마지막 날짜: {last_ok}")
        report["future_coverage"] = coverage
        report["last_date_with_data"] = last_ok

        # ── 과거 경계도 간단히 ───────────────────────────────────────────
        print("\n=== 과거 커버리지 샘플 ===")
        past = {}
        for key in ["20250901", "20251104", "20260302", "20260601"]:
            j = c.post(API, data={"date": key, "now": "Y"}).json()
            past[key] = len(j.get("list", []))
            print(f"  {key} → {past[key]}건")
        report["past_coverage"] = past

        # ── g) likehome ────────────────────────────────────────────────
        print("\n=== g) 대조군 likehome (생활관 주간식단) ===")
        like = {}
        for date in ["2026-08-10", "2026-06-01", "2026-05-25"]:
            r = c.get(LIKEHOME, params={"sMenu": "B7100", "date": date})
            html = r.text
            tree = HTMLParser(html)
            tables = tree.css("table")
            info = {
                "requested_date": date,
                "final_url": str(r.url),
                "redirected": str(r.url) != f"{LIKEHOME}?sMenu=B7100&date={date}",
                "status": r.status_code,
                "bytes": len(r.content),
                "tables": len(tables),
            }
            if tables:
                t = tables[0]
                rows = t.css("tr")
                cells = [[(td.text() or "").strip() for td in row.css("td,th")] for row in rows]
                nonempty = sum(1 for row in cells for cv in row if cv)
                info.update({
                    "rows": len(rows),
                    "cell_total": sum(len(r_) for r_ in cells),
                    "cell_nonempty": nonempty,
                    "sample_rows": cells[:6],
                })
                # 이미지로 식단을 올렸는지 확인
                imgs = [n.attributes.get("src", "") for n in t.css("img")]
                info["imgs_in_table"] = imgs[:10]
            like[date] = info
            print(f"\n  [{date}] status={info['status']} tables={info['tables']} "
                  f"final={info['final_url']}")
            if tables:
                print(f"    rows={info['rows']} cells={info['cell_total']} "
                      f"nonempty={info['cell_nonempty']} imgs_in_table={info['imgs_in_table']}")
                for row in info["sample_rows"]:
                    print(f"      {row}")
        report["likehome"] = like

        # likehome 페이지에서 식단 이미지 게시 흔적 탐색
        r = c.get(LIKEHOME, params={"sMenu": "B7100", "date": "2026-06-01"})
        img_srcs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', r.text, re.I)
        report["likehome_all_imgs"] = img_srcs
        print(f"\n  likehome 전체 img 태그: {img_srcs}")
        (OUT / "likehome_20260601.html").write_bytes(r.content)

    (OUT / "boundary_likehome_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {OUT / 'boundary_likehome_probe.json'}")


if __name__ == "__main__":
    main()
