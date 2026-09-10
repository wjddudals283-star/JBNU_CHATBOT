"""폼 페이지 가져오기 · 응답 보내기.

성공 판정이 까다롭다. 구글은 검증 실패도 200 으로 돌려주고 폼을 다시 그린다.
그래서 상태코드만 보지 않는다 —
  200 + 본문에 FB_PUBLIC_LOAD_DATA_ 가 없다   → 제출 완료 (확인 페이지)
  200 + 본문에 FB_PUBLIC_LOAD_DATA_ 가 있다   → 구글이 거절하고 폼을 다시 그렸다
  3xx → accounts.google.com                  → 로그인 필요. 이 도구로는 못 한다
  그 외                                      → 실패
"""

from __future__ import annotations

import time
from urllib.parse import urlencode
from dataclasses import dataclass
from typing import Optional

import httpx

from .schema import Form, SchemaError, normalize_form_url, parse_form_html

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


@dataclass
class SubmitResult:
    ok: bool
    status: int
    reason: str
    elapsed: float = 0.0


class LoginRequired(SchemaError):
    pass


class FormClient:
    def __init__(self, *, timeout: float = 20.0, transport: httpx.BaseTransport | None = None,
                 min_interval: float = 0.0):
        self._http = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": UA, "Accept-Language": "ko,en;q=0.8"},
            follow_redirects=False,
            transport=transport,
        )
        self.min_interval = min_interval
        self._last_sent = 0.0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "FormClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── URL ──
    def resolve_url(self, url: str) -> str:
        """forms.gle 단축 주소를 docs.google.com 주소로 푼다. 이미 풀린 주소면 그대로."""
        u = url if "://" in url else "https://" + url
        if "forms.gle" not in u:
            return u
        r = self._http.get(u)
        loc = r.headers.get("location")
        if r.status_code not in (301, 302, 303, 307, 308) or not loc:
            raise SchemaError(f"단축 주소를 풀 수 없습니다 (HTTP {r.status_code}): {url}")
        return loc

    # ── 읽기 ──
    def fetch_form(self, url: str) -> Form:
        form_id, viewform = normalize_form_url(self.resolve_url(url))
        r = self._http.get(viewform)
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location", "")
            if "accounts.google.com" in loc or "ServiceLogin" in loc:
                raise LoginRequired("로그인이 필요한 폼입니다 (응답자 제한 설정). 이 도구는 비로그인 폼만 다룹니다")
            raise SchemaError(f"예상치 못한 리다이렉트: {loc}")
        if r.status_code != 200:
            raise SchemaError(f"폼 페이지를 읽지 못했습니다 (HTTP {r.status_code})")
        form = parse_form_html(r.text, form_id=form_id)
        # 편집자 주소(/d/<id>/)로 들어왔으면 응답 주소도 같은 형태로 둔다
        form.public = "/forms/d/e/" in viewform
        return form

    # ── 쓰기 ──
    def submit(self, form: Form, payload: list[tuple[str, str]]) -> SubmitResult:
        self._throttle()
        url = form.response_url
        t0 = time.monotonic()
        try:
            # (name, value) 목록을 직접 인코딩한다 — 체크박스는 같은 이름이 반복되므로 dict 가 아니다.
            r = self._http.post(url, content=urlencode(payload).encode(),
                                headers={"Referer": form.viewform_url,
                                         "Content-Type": "application/x-www-form-urlencoded"})
        except httpx.HTTPError as e:
            return SubmitResult(False, 0, f"네트워크 오류: {e}", time.monotonic() - t0)
        dt = time.monotonic() - t0
        if r.status_code in (301, 302, 303, 307, 308):
            loc = r.headers.get("location", "")
            if "accounts.google.com" in loc or "ServiceLogin" in loc:
                return SubmitResult(False, r.status_code, "로그인 필요", dt)
            return SubmitResult(False, r.status_code, f"리다이렉트: {loc}", dt)
        if r.status_code != 200:
            return SubmitResult(False, r.status_code, f"HTTP {r.status_code}", dt)
        if "FB_PUBLIC_LOAD_DATA_" in r.text:
            return SubmitResult(False, 200, "구글이 응답을 거절했습니다 (폼이 다시 그려짐 — 필수·형식 검증 실패 가능)", dt)
        return SubmitResult(True, 200, "제출 완료", dt)

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        wait = self.min_interval - (time.monotonic() - self._last_sent)
        if wait > 0:
            time.sleep(wait)
        self._last_sent = time.monotonic()
