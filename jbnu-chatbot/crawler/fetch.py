"""원문 가져오기 + 스냅샷 보관.

Source Layer 는 원문을 **그대로** 보관한다. 파싱은 그 위에서 다시 할 수 있어야 한다.
셀렉터가 깨졌을 때 과거 원문으로 파서를 고쳐 재실행하는 게 가능해야 하기 때문이다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import pathlib
import re
import ssl
from dataclasses import dataclass

import httpx

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")


def lax_ssl() -> ssl.SSLContext:
    """구형 TLS/약한 DH 를 쓰는 국내 기관 서버 대응."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.set_ciphers("DEFAULT@SECLEVEL=1")
    return ctx


def compute_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# 매 요청마다 바뀌는 부분. 이걸 지우지 않으면 해시가 항상 달라져
# 'unchanged' 판정이 영원히 성립하지 않는다.
#
# 실측: likehome 은 CSS/JS 링크에 유닉스 타임스탬프를 캐시버스터로 붙인다.
#   <link href="jscss/common.css?ver=1786358870">   ← 매초 증가
# 같은 페이지를 1초 간격으로 두 번 받으면 해시가 다르다. 그러면
#   · 파서가 매번 불린다 (T5 가 실전에서 무력화)
#   · 스냅샷이 무한히 쌓인다
# 픽스처는 바이트가 동일해서 테스트로는 안 잡힌다. 실제 호출로만 드러난다.
DEFAULT_VOLATILE_PATTERNS = [
    r"[?&](?:ver|v|_|ts|cb|rnd|nocache)=\d+",    # 캐시버스터
    r'name="_csrf"\s+content="[0-9a-fA-F-]+"',   # 세션별 CSRF 토큰
    r"(?i)jsessionid=[0-9A-F]+",
]


def normalize_for_hash(content: bytes, patterns: list[str] | None = None) -> bytes:
    """변동분을 지운 뒤의 바이트. **비교 전용**이다.

    스냅샷에는 언제나 원문을 그대로 저장한다 — 정규화한 걸 저장하면
    나중에 파서를 고쳐 과거 원문을 재파싱할 때 원본이 없다.
    """
    text = content.decode("utf-8", errors="replace")
    for p in (patterns if patterns is not None else DEFAULT_VOLATILE_PATTERNS):
        text = re.sub(p, "", text)
    return text.encode("utf-8")


@dataclass
class FetchResult:
    source_key: str
    url: str
    final_url: str
    http_status: int
    content: bytes
    content_hash: str      # 원문 바이트 해시. 감사 추적용
    fetched_at: str
    media_type: str
    stable_hash: str = ""  # 변동분 제거 후 해시. **변경 감지는 이걸로 한다**

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


def make_result(source_key: str, url: str, final_url: str, status: int,
                content: bytes, fetched_at: str, media_type: str,
                volatile_patterns: list[str] | None = None) -> FetchResult:
    return FetchResult(
        source_key=source_key, url=url, final_url=final_url, http_status=status,
        content=content, content_hash=compute_hash(content),
        stable_hash=compute_hash(normalize_for_hash(content, volatile_patterns)),
        fetched_at=fetched_at, media_type=media_type,
    )


def fetch(source_key: str, url: str, *, params: dict | None = None,
          method: str = "GET", media_type: str = "html",
          client: httpx.Client | None = None, now: dt.datetime | None = None,
          volatile_patterns: list[str] | None = None) -> FetchResult:
    own = client is None
    c = client or httpx.Client(timeout=30.0, verify=lax_ssl(), follow_redirects=True,
                               headers={"User-Agent": UA})
    try:
        if method.upper() == "POST":
            r = c.post(url, data=params or {})
        else:
            r = c.get(url, params=params)
        return make_result(
            source_key, url, str(r.url), r.status_code, r.content,
            (now or dt.datetime.now(dt.timezone.utc)).isoformat(),
            media_type, volatile_patterns,
        )
    finally:
        if own:
            c.close()


_META_CSRF = r'<meta[^>]+name=["\']{name}["\'][^>]+content=["\']([^"\']+)'


def fetch_with_csrf(source_key: str, url: str, *, page_url: str,
                    meta_name: str = "_csrf", header: str = "X-CSRF-Token",
                    params: dict | None = None, media_type: str = "html",
                    now: dt.datetime | None = None,
                    volatile_patterns: list[str] | None = None) -> FetchResult:
    """CSRF 토큰이 필요한 POST 엔드포인트.

    학교 XHR(dataAjax.do)은 토큰 없이 호출하면 200 이 아니라 **403 + 안내 HTML**을 준다.
    토큰은 세션 바인딩이므로 순서와 클라이언트 재사용이 둘 다 중요하다.
        1) page_url 을 GET → JSESSIONID 획득 + <meta name="_csrf"> 파싱
        2) 같은 httpx.Client 로 POST, 헤더에 토큰을 실어 보낸다
    """
    with httpx.Client(timeout=30.0, verify=lax_ssl(), follow_redirects=True,
                      headers={"User-Agent": UA}) as c:
        page = c.get(page_url)
        m = re.search(_META_CSRF.format(name=re.escape(meta_name)), page.text)
        headers = {"Referer": page_url, "AJAX": "true",
                   "X-Requested-With": "XMLHttpRequest"}
        if m:
            headers[header] = m.group(1)
        r = c.post(url, data=params or {}, headers=headers)
        return make_result(source_key, url, str(r.url), r.status_code, r.content,
                           (now or dt.datetime.now(dt.timezone.utc)).isoformat(),
                           media_type, volatile_patterns)


def save_snapshot_file(result: FetchResult, snapshot_dir: pathlib.Path) -> pathlib.Path:
    ext = {"html": "html", "json": "json", "pdf": "pdf", "image": "bin"}[result.media_type]
    d = snapshot_dir / result.source_key
    d.mkdir(parents=True, exist_ok=True)
    stamp = result.fetched_at.replace(":", "").replace("-", "")[:15]
    path = d / f"{stamp}_{result.content_hash[:12]}.{ext}"
    path.write_bytes(result.content)
    return path


def snapshot_id(result: FetchResult) -> str:
    """stable_hash 기준. 같은 내용은 같은 스냅샷 행으로 모인다.

    content_hash 로 만들면 캐시버스터 때문에 매 크롤마다 새 행이 생겨
    스냅샷 테이블이 무한히 커진다.
    """
    return f"jbnu:snap/{result.source_key}/{(result.stable_hash or result.content_hash)[:16]}"
