"""조사 1·3 심화 — 생협 API 응답의 실제 모양 분석.

확인 항목
  a) GET(15건) vs POST(3건) 차이 — 주간 전체를 한 번에 받을 수 있는가
  b) 후생관 코너(subData) 전수 — meal_service.corner 설계 근거
  c) 'diet' 안의 미운영 표현 사전 — service_status 매핑 근거
  d) 가격 필드 존재 여부 — 전 응답을 훑어 숫자/원 패턴 탐색
  e) 미등록 날짜 응답 형태 — closed 와 unknown 을 가르는 신호

사용: python tools/probe_coop_shape.py
"""

from __future__ import annotations

import collections
import json
import pathlib
import re
import ssl
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "probe"

API = "https://coopjbnu.kr/function/get_cafeteria_menu.php"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"


def ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    c.set_ciphers("DEFAULT@SECLEVEL=1")
    return c


def fetch(client, method: str, params: dict) -> dict:
    h = {"User-Agent": UA}
    r = client.post(API, data=params, headers=h) if method == "POST" else client.get(API, params=params, headers=h)
    return r.json()


def main() -> None:
    report = {}
    with httpx.Client(timeout=30.0, verify=ctx(), follow_redirects=True) as c:

        # ── a) GET vs POST, now=Y vs now=N ─────────────────────────────
        print("=== a) 호출 파라미터별 반환 범위 ===")
        combos = [
            ("POST", {"date": "20260810", "now": "Y"}),
            ("POST", {"date": "20260810", "now": "N"}),
            ("POST", {"date": "20260810"}),
            ("GET", {"date": "20260810", "now": "Y"}),
            ("GET", {"date": "20260810", "now": "N"}),
        ]
        for method, p in combos:
            try:
                j = fetch(c, method, p)
                lst = j.get("list", [])
                dates = sorted({i.get("date") for i in lst})
                rests = sorted({i.get("restNm") for i in lst})
                print(f"[{method} {p}] list={len(lst)}  dates={dates}  rests={rests}")
                report[f"{method}|{json.dumps(p, sort_keys=True)}"] = {
                    "count": len(lst), "dates": dates, "restaurants": rests,
                }
            except Exception as e:  # noqa: BLE001
                print(f"[{method} {p}] FAIL {type(e).__name__}: {e}")

        # ── 주간 전체(GET now=N 또는 Y 중 넓은 쪽)를 기준 데이터로 확보 ──
        week = fetch(c, "GET", {"date": "20260810", "now": "N"})
        (OUT / "coop_api_week_20260810.json").write_text(
            json.dumps(week, ensure_ascii=False, indent=2), encoding="utf-8")

        # ── b) 후생관 코너 전수 ──────────────────────────────────────────
        print("\n=== b) 후생관 코너(subData) 전수 — 2026-08-10 ===")
        day = fetch(c, "POST", {"date": "20260810", "now": "Y"})
        for item in day.get("list", []):
            print(f"\n■ {item.get('restNm')}  date={item.get('date')}  day={item.get('day')!r}")
            for i, s in enumerate(item.get("subData", [])):
                diet = (s.get("diet") or "").replace("\n", " / ").strip()
                print(f"  [{i:2}] cate1={s.get('cate1')!r} cate2={s.get('cate2')!r} "
                      f"cate3={s.get('cate3')!r}\n       diet={diet!r}")

        # ── c) 미운영 표현 사전 + d) 가격 패턴 ────────────────────────────
        print("\n=== c/d) 여러 날짜를 훑어 미운영 표현·가격 패턴 수집 ===")
        diet_lines = collections.Counter()
        cate_combo = collections.Counter()
        price_hits = []
        all_keys = collections.Counter()
        probe_dates = ["20260810", "20260811", "20260812", "20260602", "20260527",
                       "20260420", "20260316", "20251104"]
        empty_dates = []
        for d in probe_dates:
            j = fetch(c, "POST", {"date": d, "now": "Y"})
            lst = j.get("list", [])
            if not lst:
                empty_dates.append(d)
                continue
            for item in lst:
                all_keys.update(item.keys())
                for s in item.get("subData", []):
                    all_keys.update(f"subData.{k}" for k in s.keys())
                    cate_combo[(s.get("cate1"), s.get("cate2"))] += 1
                    for ln in (s.get("diet") or "").split("\n"):
                        ln = ln.strip()
                        if not ln:
                            continue
                        diet_lines[ln] += 1
                        if re.search(r"\d{3,}|원", ln):
                            price_hits.append(ln)

        print(f"\n등장한 최상위/하위 키 전수: {dict(all_keys)}")
        print(f"cate1×cate2 조합: {dict(cate_combo)}")
        print(f"\n미등록(list=[]) 날짜: {empty_dates}")
        print("\n가장 흔한 diet 라인 15개 (미운영 표현 탐지용):")
        for ln, n in diet_lines.most_common(15):
            print(f"  {n:4}회  {ln!r}")
        print(f"\n가격/숫자 의심 라인 {len(price_hits)}건: {price_hits[:20]}")

        report["keys_seen"] = dict(all_keys)
        report["cate_combos"] = {f"{a}|{b}": n for (a, b), n in cate_combo.items()}
        report["empty_dates"] = empty_dates
        report["diet_line_top"] = diet_lines.most_common(40)
        report["price_like_lines"] = price_hits[:50]

    (OUT / "coop_shape_probe.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n결과 저장: {OUT / 'coop_shape_probe.json'}")


if __name__ == "__main__":
    main()
