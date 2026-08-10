"""스킬 엔드포인트 인증 — 공유 시크릿 헤더.

    헤더:  X-Skill-Token: <랜덤 값>
    서버:  불일치 → 401,  토큰 미설정 → 503

배경
  스킬서버는 공개 HTTPS 라 **주소만 알면 누구나 호출**할 수 있다.
  Render URL 은 서비스명 기반이라 추측 가능하고, 저장소가 public 이라
  서비스명이 render.yaml 에 그대로 적혀 있다. 배포하는 순간 실제로 열린다.
  카카오 오픈빌더가 커스텀 헤더를 지원하므로 공유 시크릿으로 막는다.

★ Fail closed — SKILL_TOKEN 이 없으면 열어두지 않고 503 을 준다.
  "설정을 깜빡했다"가 곧 "누구나 호출 가능"이 되면 안 된다.
  침묵이 위험한 것과 같은 이유로, 설정 누락은 조용히 통과시키지 않는다.

★ 상수 시간 비교 — 문자열 == 은 앞에서부터 비교하다 다르면 즉시 끝나서
  응답 시간으로 토큰을 한 글자씩 알아낼 수 있다.
"""

from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException

TOKEN_ENV = "SKILL_TOKEN"
HEADER_NAME = "X-Skill-Token"
MIN_TOKEN_LEN = 16


def configured_token() -> str | None:
    v = (os.environ.get(TOKEN_ENV) or "").strip()
    return v or None


def check(token: str | None) -> None:
    """토큰 검사. 통과하면 None, 아니면 HTTPException."""
    expected = configured_token()
    if expected is None:
        raise HTTPException(
            status_code=503,
            detail=f"{TOKEN_ENV} 미설정 — 인증을 구성하기 전에는 응답하지 않는다",
        )
    if len(expected) < MIN_TOKEN_LEN:
        raise HTTPException(
            status_code=503,
            detail=f"{TOKEN_ENV} 가 너무 짧다 (최소 {MIN_TOKEN_LEN}자)",
        )
    if token is None or not hmac.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="인증 실패")


async def require_token(x_skill_token: str | None = Header(default=None)) -> None:
    """FastAPI 의존성. 라우터에 붙여서 쓴다."""
    check(x_skill_token)
