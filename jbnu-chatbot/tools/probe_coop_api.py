"""조사 1·3 — 생협 식단 JSON API 직접 호출.

week_menu.php 의 인라인 JS에서 발견한 엔드포인트:
    POST /function/get_cafeteria_menu.php   data: date=YYYYMMDD, now=Y
응답 스키마 / 가격 필드 유무 / 방학 기간 동작을 확인한다.

사용: python tools/probe_coop_api.py
"""

from __future__ import annotations

import json
import pathlib
import ssl
import sys

import httpx

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "probe"
OUT.mkdir(parents=True, exist_ok=True)

API = "https://coopjbnu.kr/function/get_cafeteria_menu.php"
REFERER = "https://coopjbnu.kr/menu/week_menu.php"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# 방학(오늘) / 개강 직후 / 지난 학기 평일 — 세 구간을 비교한다
DATES = [
    ("20260810", "오늘 · 방학 중 월요일"),
    ("20260907", "2학기 개강 후 월요일(예정)"),
    ("20260602", "지난 학기 평일(1학기 중)"),
    ("20260527", "지난 학기 평일(1학기 중)"),
]

VARIANTS = [
    ("post_referer", "POST", {"Referer": REFERER}),
    ("post_bare", "POST", {}),          # Referer 없이도 되는지 = 크롤러 난이도
    ("get", "GET", {"Referer": REFERER}),  # GET 도 받는지
]


def ctx() -> ssl.SSLContext:
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    c.set_ciphers("DEFAULT@SECLEVEL=1")
    return c


def call(client: httpx.Client, method: str, date: str, extra: dict) -> dict:
    data = {"date": date, "now": "Y"}
    headers = {"User-Agent": UA, "X-Requested-With": "XMLHttpRequest", **extra}
    if method == "POST":
        r = client.post(API, data=data, headers=headers)
    else:
        r = client.get(API, params=data, headers=headers)
    out = {"status_code": r.status_code, "bytes": len(r.content),
           "content_type": r.headers.get("content-type")}
    try:
        out["json"] = r.json()
    except Exception:  # noqa: BLE001
        out["text_head"] = r.text[:400]
    return out


def summarize(payload: dict) -> str:
    if "json" not in payload:
        return f"  JSON 아님 → {payload.get('text_head', '')[:120]!r}"
    j = payload["json"]
    if not isinstance(j, dict):
        return f"  최상위가 dict 아님: {type(j).__name__}"
    lst = j.get("list") or []
    lines = [f"  status={j.get('status')!r}  list={len(lst)}건  top_keys={list(j.keys())}"]
    for item in lst:
        sub = item.get("subData") or []
        lines.append(
            f"    - restNm={item.get('restNm')!r} date={item.get('date')!r} "
            f"subData={len(sub)}건 keys={list(item.keys())}"
        )
        for s in sub[:3]:
            diet = (s.get("diet") or "")
            lines.append(
                f"        cate=[{s.get('cate1')}/{s.get('cate2')}] {s.get('cate3')!r} "
                f"keys={list(s.keys())}"
            )
            for ln in [x for x in diet.split("\n") if x.strip()][:6]:
                lines.append(f"          · {ln.strip()}")
    return "\n".join(lines)


def main() -> None:
    results = {}
    with httpx.Client(timeout=30.0, verify=ctx(), follow_redirects=True) as c:
        # 세션 쿠키가 필요한지 확인하기 위해 먼저 페이지를 한 번 연다
        c.get(REFERER, headers={"User-Agent": UA})

        print("=== 호출 방식 비교 (date=20260602) ===")
        for name, method, extra in VARIANTS:
            try:
                p = call(c, method, "20260602", extra)
                results[f"variant__{name}"] = p
                j = p.get("json")
                n = len(j.get("list", [])) if isinstance(j, dict) else "-"
                print(f"[{name}] {p['status_code']} {p['bytes']}B ct={p['content_type']} list={n}")
            except Exception as e:  # noqa: BLE001
                print(f"[{name}] FAIL {type(e).__name__}: {e}")
                results[f"variant__{name}"] = {"error": str(e)}

        print("\n=== 날짜별 응답 ===")
        for date, note in DATES:
            print(f"\n--- {date} ({note}) ---")
            try:
                p = call(c, "POST", date, {"Referer": REFERER})
                results[f"date__{date}"] = p
                print(summarize(p))
            except Exception as e:  # noqa: BLE001
                print(f"  FAIL {type(e).__name__}: {e}")
                results[f"date__{date}"] = {"error": str(e)}

    (OUT / "coop_api_probe.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n결과 저장: {OUT / 'coop_api_probe.json'}")


if __name__ == "__main__":
    main()
