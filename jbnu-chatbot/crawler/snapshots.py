"""안내 페이지 원문 보관 — 재파싱을 위해서다.

    원문이 바뀌었다  → 재수집 (네트워크)
    해석이 바뀌었다  → 재파싱 (디스크)

파서를 고칠 때마다 6,985페이지를 다시 긁는 것은 네트워크를 캐시 대신 쓰는 일이다.
학교 서버에 부담이고, 두 시간이 걸리고, 돌고 있는 수집까지 죽인다.
원문을 갖고 있으면 몇 분이면 끝난다.

★ 왜 처음부터 없었나
  식단·학사일정은 ingest() 가 source_snapshot 에 원문을 남긴다.
  안내 페이지 파이프라인은 받아서 파싱하고 원문을 버렸다 — 내가 안 만들었다.
  그래서 파서를 고칠 때마다 전수 재수집 말고는 방법이 없었다.

★ gzip 으로 접어 둔다
  6,985페이지 × 평균 60KB ≈ 420MB. 그대로 두면 디스크 1GB 를 위협한다.
  HTML 은 잘 접혀서 보통 1/8 로 준다.

★ 주소로 파일 이름을 정한다
  같은 페이지는 늘 같은 파일에 덮어쓴다. 이력이 아니라 **최신 원문**을 갖는 것이
  목적이다. 이력이 필요하면 source_snapshot 쪽 규약(시점 포함 식별자)을 쓴다.
"""

from __future__ import annotations

import gzip
import hashlib
import pathlib
import urllib.parse as up

SUBDIR = "pages"


def path_for(url: str, root: pathlib.Path) -> pathlib.Path:
    host = up.urlsplit(url).hostname or "unknown"
    name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return root / SUBDIR / host / f"{name}.html.gz"


def save(url: str, html: str, root: pathlib.Path) -> pathlib.Path | None:
    """원문을 접어서 저장한다. 실패해도 수집을 멈추지 않는다 —
    스냅샷은 편의지 수집의 조건이 아니다."""
    p = path_for(url, root)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wt", encoding="utf-8", compresslevel=6) as f:
            f.write(html)
        return p
    except Exception:  # noqa: BLE001
        return None


def load(url: str, root: pathlib.Path) -> str | None:
    """저장된 원문. 없으면 None (그러면 그 페이지는 재파싱 대상에서 빠진다)."""
    p = path_for(url, root)
    if not p.exists():
        return None
    try:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return None


def usage(root: pathlib.Path) -> dict:
    """얼마나 쓰고 있나. 디스크는 유한하고, 조용히 차면 배포가 멈춘다."""
    base = root / SUBDIR
    if not base.exists():
        return {"files": 0, "bytes": 0, "mb": 0.0}
    total = files = 0
    for p in base.rglob("*.html.gz"):
        try:
            total += p.stat().st_size
            files += 1
        except OSError:
            pass
    return {"files": files, "bytes": total, "mb": round(total / 1024 / 1024, 1)}
