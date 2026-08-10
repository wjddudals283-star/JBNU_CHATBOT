"""실사이트 스모크 — **배포 서버의 네트워크 위치에서** 돈다.

★ 왜 로컬 스모크로는 부족한가
  로컬 스모크는 네트워크 위치를 고정한 반쪽 검증이다.
  실제로 생협 API 가 한국 IP 에서는 200, Render(해외 IP)에서는 403 이었다.
  로컬에서 아무리 돌려도 초록불이고, 실전에서만 빨간불이다.
  캐시버스터(시간 의존) 때 배운 것과 같은 유형 — **위치 의존** 결함이다.

두 경로로 돈다.
  1) GET /admin/smoke      필요할 때 즉시 (인증 필요)
  2) 스케줄러 일 1회        회귀를 자동으로 잡는다. 결과는 [smoke] 로그로

★ 헤더 변형을 여러 개 시도해 **어느 조합이 통과하는지**를 같이 보고한다.
  차단이 UA 때문인지 IP 때문인지는 그 표를 봐야 갈린다.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from crawler import fetch as fetch_mod

log = logging.getLogger("jbnu.smoke")
KST = dt.timezone(dt.timedelta(hours=9))

COOP_API = "https://coopjbnu.kr/function/get_cafeteria_menu.php"
COOP_PAGE = "https://coopjbnu.kr/menu/week_menu.php"
LIKEHOME = "https://likehome.jbnu.ac.kr/home/main/inner.php"
JBNU_PAGE = "https://www.jbnu.ac.kr/web/unvrslife/campuslife/cafeteria.do"
JBNU_AJAX = "https://www.jbnu.ac.kr/web/unvrslife/campuslife/cafeteria/dataAjax.do"

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


@dataclass
class Probe:
    name: str
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    data: dict[str, str] | None = None
    params: dict[str, str] | None = None


def coop_variants(date: str) -> list[Probe]:
    """생협 API 헤더 변형. 어디서 갈리는지 보려고 한 단계씩 더한다."""
    d = {"date": date}
    ko = "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    return [
        Probe("coop/bare", "POST", COOP_API, {}, d),
        Probe("coop/ua", "POST", COOP_API, {"User-Agent": BROWSER_UA}, d),
        Probe("coop/ua+xhr", "POST", COOP_API,
              {"User-Agent": BROWSER_UA, "X-Requested-With": "XMLHttpRequest"}, d),
        Probe("coop/ua+ref", "POST", COOP_API,
              {"User-Agent": BROWSER_UA, "Referer": COOP_PAGE}, d),
        Probe("coop/ua+ref+lang", "POST", COOP_API,
              {"User-Agent": BROWSER_UA, "Referer": COOP_PAGE,
               "Accept-Language": ko, "Origin": "https://coopjbnu.kr"}, d),
        Probe("coop/full", "POST", COOP_API,
              {"User-Agent": BROWSER_UA, "Referer": COOP_PAGE,
               "Accept-Language": ko, "Origin": "https://coopjbnu.kr",
               "X-Requested-With": "XMLHttpRequest",
               "Accept": "application/json, text/javascript, */*; q=0.01"}, d),
        # 페이지 자체가 열리는지 — API 만 막힌 건지 사이트 전체가 막힌 건지 가른다
        Probe("coop/page", "GET", COOP_PAGE, {"User-Agent": BROWSER_UA}),
    ]


def other_sources() -> list[Probe]:
    return [
        Probe("likehome/page", "GET", LIKEHOME, {"User-Agent": BROWSER_UA},
              params={"sMenu": "B7100"}),
        Probe("jbnu/page", "GET", JBNU_PAGE, {"User-Agent": BROWSER_UA}),
    ]


def run_probe(client: httpx.Client, p: Probe) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        if p.method == "POST":
            r = client.post(p.url, data=p.data or {}, headers=p.headers)
        else:
            r = client.get(p.url, params=p.params, headers=p.headers)
        body = r.text
        out = {
            "name": p.name, "method": p.method, "status": r.status_code,
            "bytes": len(r.content), "elapsed_ms": round((time.perf_counter() - t0) * 1000),
            "ok": 200 <= r.status_code < 300,
            "content_type": r.headers.get("content-type"),
            # ★ 차단 사유가 여기 적혀 있는 경우가 많다. 이게 진단의 출발점이다.
            "body_head": body[:400].strip(),
            "server": r.headers.get("server"),
        }
        # 차단 페이지가 200 으로 오는 경우도 있어 내용도 본다
        if out["ok"] and p.name.startswith("coop/") and p.method == "POST":
            out["looks_like_json"] = body.lstrip().startswith(("{", "["))
        return out
    except Exception as e:  # noqa: BLE001
        return {"name": p.name, "method": p.method, "status": None, "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "elapsed_ms": round((time.perf_counter() - t0) * 1000)}


# 프로브 이름 접두사 → sources.yaml 의 source_key
PROBE_SOURCE = {"coop/": "coop_week_menu", "likehome/": "likehome_week_menu",
                "jbnu/": "jbnu_cafeteria_day"}


def _source_of(name: str) -> str | None:
    for prefix, key in PROBE_SOURCE.items():
        if name.startswith(prefix):
            return key
    return None


def known_blocked(sources: dict, source_key: str) -> dict | None:
    cfg = (sources or {}).get(source_key) or {}
    return cfg.get("known_blocked")


def run(date: str | None = None, *, timeout: float = 20.0,
        sources: dict | None = None) -> dict[str, Any]:
    """전 프로브 실행. 서버의 네트워크 위치에서 본 결과를 그대로 돌려준다.

    ★ 감시 방향
      · 알려진 차단(known_blocked)이 여전히 차단 → 정상 상태. INFO
      · 알려진 차단이 **풀림(200)** → ★알린다. 1차 원천을 되찾을 기회다
      · 알려진 차단이 아닌데 차단 → 진짜 문제. ERROR
    """
    if sources is None:
        from crawler import schedule as sched_mod
        sources = sched_mod.load_schedule()

    now = dt.datetime.now(KST)
    date = date or now.strftime("%Y%m%d")
    probes = coop_variants(date) + other_sources()

    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=timeout, verify=fetch_mod.lax_ssl(),
                      follow_redirects=True) as c:
        for p in probes:
            res = run_probe(c, p)
            res["source_key"] = _source_of(p.name)
            results.append(res)
            log.info("[smoke] %s status=%s bytes=%s %s",
                     res["name"], res.get("status"), res.get("bytes"),
                     "" if res["ok"] else f"body={res.get('body_head', '')[:120]!r}")

    coop = [r for r in results if r["name"].startswith("coop/")]
    coop_ok = [r["name"] for r in coop if r["ok"]]
    verdict = _verdict(coop)

    alerts = _alerts(results, sources)
    for a in alerts:
        (log.warning if a["kind"] == "block_lifted" else log.error)(
            "[smoke] %s source=%s %s", a["tag"], a["source_key"], a["message"])
    if not alerts:
        log.info("[smoke] SUMMARY ok — 경보 없음 (coop_ok=%s)", coop_ok or "none")

    return {
        "at": now.isoformat(),
        "date_probed": date,
        "verdict": verdict,
        "coop_passing_variants": coop_ok,
        "alerts": alerts,
        "results": results,
    }


def _alerts(results: list[dict[str, Any]], sources: dict) -> list[dict[str, Any]]:
    """소스별로 '알려진 상태'와 다른 것만 경보로 올린다."""
    out: list[dict[str, Any]] = []
    by_source: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        if r.get("source_key"):
            by_source.setdefault(r["source_key"], []).append(r)

    for key, rows in by_source.items():
        any_ok = any(r["ok"] for r in rows)
        blocked_info = known_blocked(sources, key)

        if blocked_info and any_ok:
            # ★ 감시 방향을 뒤집은 지점. 차단이 풀렸다 = 1차 원천 복구 기회.
            passing = [r["name"] for r in rows if r["ok"]]
            out.append({
                "kind": "block_lifted", "tag": "BLOCK-LIFTED", "source_key": key,
                "message": (f"차단이 풀렸다 (통과: {passing}). "
                            f"{blocked_info.get('since')} 부터 차단으로 기록돼 있었다. "
                            f"sources.yaml 의 known_blocked 를 지우고 1차로 되돌릴 것"),
                "passing": passing,
            })
        elif not blocked_info and not any_ok:
            out.append({
                "kind": "newly_blocked", "tag": "BLOCKED", "source_key": key,
                "message": f"전 변형 실패. 새 차단이거나 원천 장애 "
                           f"({rows[0].get('status')})",
            })
        # blocked_info 이고 여전히 차단 → 알려진 상태. 경보 없음(소음 방지)
    return out


def _verdict(coop: list[dict[str, Any]]) -> str:
    """헤더로 뚫리는지 / 사이트 전체가 막혔는지 가른다."""
    api = [r for r in coop if r["method"] == "POST"]
    page = next((r for r in coop if r["name"] == "coop/page"), None)
    ok_api = [r for r in api if r["ok"]]

    if len(ok_api) == len(api) and api:
        return "정상 — 전 변형 통과"
    if ok_api:
        return f"헤더 의존 — {[r['name'] for r in ok_api]} 만 통과. 그 조합을 채택하면 된다"
    if page and page["ok"]:
        return "API 만 차단 — 페이지는 열린다. 엔드포인트 단위 차단(WAF 규칙) 가능성"
    return "사이트 전체 차단 — IP/지역 차단 유력. 헤더로는 안 뚫린다"


def to_json(result: dict[str, Any]) -> dict[str, Any]:
    return result


def as_probe_dicts() -> list[dict[str, Any]]:
    return [asdict(p) for p in coop_variants("20260810") + other_sources()]
