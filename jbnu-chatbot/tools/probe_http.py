"""0장 조사 — HTTP 사다리 프로브.

requests(기본 UA) → 브라우저 UA → (Playwright는 probe_xhr.py)
각 단계의 status / 길이 / 인코딩 / 테이블·이미지 존재 여부를 기록하고
원문 스냅샷을 docs/probe/ 에 저장한다.

사용: python tools/probe_http.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import ssl
import sys
import traceback

import httpx

sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "probe"
OUT.mkdir(parents=True, exist_ok=True)

TODAY = "2026-08-10"

TARGETS = [
    ("coop_week_menu", "https://coopjbnu.kr/menu/week_menu.php"),
    ("jbnu_cafeteria", "https://www.jbnu.ac.kr/web/unvrslife/campuslife/cafeteria.do"),
    ("likehome_week_menu", f"https://likehome.jbnu.ac.kr/home/main/inner.php?sMenu=B7100&date={TODAY}"),
    ("univcoop_shop", "https://data.univcoop.or.kr/coop/chonbuk_coop/shop/"),
]

DEFAULT_UA = None  # httpx 기본 UA 사용
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def lax_ssl() -> ssl.SSLContext:
    """구형 TLS/약한 DH 를 쓰는 국내 기관 서버 대응."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    return ctx


def analyze(html: str) -> dict:
    """식단표가 HTML 테이블인지 이미지인지 판별하는 지표."""
    tables = re.findall(r"<table[^>]*>", html, re.I)
    imgs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.I)
    # 식단 관련 이미지 후보 (파일명/경로에 menu, food, siksa 등)
    menu_imgs = [s for s in imgs if re.search(r"menu|food|siksa|sikdan|diet|week", s, re.I)]
    # 표 안의 td 개수 (본문 테이블이 실제로 차 있는지)
    tds = len(re.findall(r"<t[dh][^>]*>", html, re.I))
    # 식단 관련 키워드
    kw = {k: len(re.findall(k, html)) for k in ["조식", "중식", "석식", "메뉴", "식단", "원"]}
    return {
        "table_tags": len(tables),
        "td_th_cells": tds,
        "img_total": len(imgs),
        "img_menu_like": menu_imgs[:10],
        "keyword_hits": kw,
        "has_loading_placeholder": bool(re.search(r"Loading|로딩", html)),
        "script_tags": len(re.findall(r"<script", html, re.I)),
    }


def probe(key: str, url: str, label: str, headers: dict | None) -> dict:
    rec = {"source": key, "url": url, "stage": label}
    try:
        with httpx.Client(
            headers=headers or {},
            follow_redirects=True,
            timeout=30.0,
            verify=lax_ssl(),
        ) as c:
            r = c.get(url)
        body = r.text
        raw = r.content
        rec.update(
            {
                "ok": True,
                "status": r.status_code,
                "final_url": str(r.url),
                "redirects": [str(h.url) for h in r.history],
                "bytes": len(raw),
                "text_len": len(body),
                "declared_encoding": r.headers.get("content-type", ""),
                "resolved_encoding": r.encoding,
                "content_hash": hashlib.sha256(raw).hexdigest()[:16],
                "server": r.headers.get("server"),
                "analysis": analyze(body),
            }
        )
        snap = OUT / f"{key}__{label}.html"
        snap.write_bytes(raw)
        rec["snapshot"] = str(snap.relative_to(ROOT))
    except Exception as e:  # noqa: BLE001
        rec.update({"ok": False, "error": f"{type(e).__name__}: {e}"})
        rec["trace_tail"] = traceback.format_exc().strip().splitlines()[-1]
    return rec


def main() -> None:
    results = []
    for key, url in TARGETS:
        for label, headers in (("default_ua", DEFAULT_UA), ("browser_ua", BROWSER_HEADERS)):
            rec = probe(key, url, label, headers)
            results.append(rec)
            head = f"[{key}/{label}]"
            if rec["ok"]:
                a = rec["analysis"]
                print(
                    f"{head} {rec['status']} {rec['bytes']}B enc={rec['resolved_encoding']} "
                    f"tables={a['table_tags']} cells={a['td_th_cells']} imgs={a['img_total']} "
                    f"loading={a['has_loading_placeholder']} kw={a['keyword_hits']}"
                )
                if rec["redirects"]:
                    print(f"{head}   redirects: {rec['redirects']} -> {rec['final_url']}")
                if a["img_menu_like"]:
                    print(f"{head}   menu-like imgs: {a['img_menu_like']}")
            else:
                print(f"{head} FAIL {rec['error']}")

    (OUT / "http_probe.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n결과 저장: {OUT / 'http_probe.json'}")


if __name__ == "__main__":
    main()
