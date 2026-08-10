"""노트북 → 서버 밀어넣기.

    python -m crawler.push --source coop_week_menu
    python -m crawler.push --source coop_week_menu --date 2026-09-07
    python -m crawler.push --all-backfill

환경변수
    JBNU_SERVER   기본 https://jbnu-chatbot.onrender.com
    SKILL_TOKEN   Render Dashboard → Environment 에서 복사

★ 토큰은 **환경변수로만** 받는다. 명령행 인자로 주면 프로세스 목록과
  셸 히스토리에 남는다.

★ 원문 바이트를 그대로 보낸다. 파싱은 서버가 다시 한다 —
  밖에서 들어온 데이터를 그대로 믿지 않기 위해서다.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import os
import pathlib
import sys

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler import fetch as fetch_mod   # noqa: E402
from crawler import run as run_mod       # noqa: E402

KST = dt.timezone(dt.timedelta(hours=9))
DEFAULT_SERVER = "https://jbnu-chatbot.onrender.com"
TOKEN_ENV = "SKILL_TOKEN"


def server_url() -> str:
    return (os.environ.get("JBNU_SERVER") or DEFAULT_SERVER).rstrip("/")


def token() -> str:
    t = (os.environ.get(TOKEN_ENV) or "").strip()
    if not t:
        raise SystemExit(
            f"{TOKEN_ENV} 환경변수가 없다.\n"
            f"  Render Dashboard → 서비스 → Environment → {TOKEN_ENV} 값을 복사해\n"
            f'  PowerShell: $env:{TOKEN_ENV}="<값>"')
    return t


def fetch_one(source_key: str, cfg: dict, date: str | None):
    params = dict(cfg.get("params") or {})
    if date and cfg.get("date_param"):
        fmt = cfg.get("date_format")
        d = dt.date.fromisoformat(date)
        params[cfg["date_param"]] = d.strftime(fmt) if fmt else d.isoformat()

    csrf = cfg.get("csrf")
    if csrf:
        return fetch_mod.fetch_with_csrf(
            source_key, cfg["url"], page_url=csrf["page_url"],
            meta_name=csrf.get("meta_name", "_csrf"),
            header=csrf.get("header", "X-CSRF-Token"),
            params=params, media_type=cfg.get("media_type", "html"))
    return fetch_mod.fetch(source_key, cfg["url"], params=params,
                           method=cfg.get("method", "GET"),
                           media_type=cfg.get("media_type", "html"))


def push(result, *, timeout: float = 60.0) -> dict:
    body = {
        "source_key": result.source_key,
        "url": result.url,
        "final_url": result.final_url,
        "http_status": result.http_status,
        # ★ 노트북이 받아온 시각. 서버가 받은 시각으로 바뀌면
        #   신선도가 실제보다 좋아 보인다.
        "fetched_at": result.fetched_at,
        "media_type": result.media_type,
        "content_b64": base64.b64encode(result.content).decode("ascii"),
    }
    r = httpx.post(f"{server_url()}/admin/ingest", json=body, timeout=timeout,
                   headers={"X-Skill-Token": token()})
    if r.status_code != 200:
        raise SystemExit(f"서버가 거부했다 {r.status_code}: {r.text[:300]}")
    return r.json()


def run_one(source_key: str, cfg: dict, date: str | None, *, dry: bool) -> bool:
    label = f"{source_key}" + (f" ({date})" if date else "")
    try:
        res = fetch_one(source_key, cfg, date)
    except Exception as e:  # noqa: BLE001
        print(f"  [{label}] 수집 실패 {type(e).__name__}: {e}")
        return False

    print(f"  [{label}] fetch {res.http_status}  {len(res.content):,}B")
    if res.http_status != 200:
        print("    ← 노트북에서도 차단됐다. 캠퍼스 망에서 다시 시도해 볼 것")
        return False
    if dry:
        print("    [dry-run] 전송 안 함")
        return True

    out = push(res)
    mark = "OK" if out["ok"] else "실패"
    print(f"    → 서버 {out['outcome']}  식단 {out['parsed']}건 "
          f"/ 격리 {out['quarantined']}  {mark}")
    for why in out.get("reasons") or []:
        print(f"      · {why}")
    if out.get("error"):
        print(f"      ! {out['error']}")
    return bool(out["ok"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="노트북 크롤 결과를 서버로 밀어넣기")
    ap.add_argument("--source", default="coop_week_menu")
    ap.add_argument("--date", help="그 주로 백필 (YYYY-MM-DD)")
    ap.add_argument("--weeks-back", type=int, default=0,
                    help="지난 N주를 함께 채운다")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    sources = run_mod.load_sources()
    cfg = sources.get(args.source)
    if cfg is None:
        print(f"모르는 source: {args.source}")
        return 1

    print(f"서버: {server_url()}")
    dates: list[str | None] = [args.date]
    if args.weeks_back:
        base = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
        dates = [(base - dt.timedelta(weeks=w)).isoformat()
                 for w in range(args.weeks_back + 1)]

    ok = 0
    for d in dates:
        if run_one(args.source, cfg, d, dry=args.dry_run):
            ok += 1
    print(f"\n{ok}/{len(dates)} 성공")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
