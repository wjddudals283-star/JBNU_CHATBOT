"""총학 공지·행사 구글시트 (T4).

    시트   전북대 총학 챗봇 · 총학 공지·행사 입력
    칸     게시일 | 분류 | 제목 | 내용 | 인스타링크 | 마감일 | 작성국

★ 왜 시트인가 — 인스타 API 를 접었다
  Graph API 는 앱 심사·토큰 60일 만료·페이스북 페이지 연결이 따라온다.
  총학이 관리해야 할 비밀이 하나 늘고, 60일마다 사람이 기억해야 한다.
  학식 백필에서 이미 배운 것이다 — **사람이 기억하는 구조는 학기 중에 진다.**
  시트는 총학이 이미 쓰는 도구고, 인스타에 올릴 때 한 번 더 붙여넣으면 끝난다.

★ 캡션을 요약하지 않는다
  8/14 에 자족성 판정기로 실제 표본을 재봤다 —
  모집 기간·대상·방법·참가비·본선 일시가 캡션 안에 전부 있었다.
  우리가 줄이면 그 값들이 사라진다. 그대로 옮기고, 길면 자른 사실을 표시한다.

★ 이상한 값은 지어내지 않고 격리한다
  날짜 칸에 '9월 초' 라고 적혀 있으면 그건 날짜가 아니다.
  추측해서 2026-09-01 로 바꾸면 **총학이 안 쓴 마감일**을 우리가 만든 것이 된다.
  T4 는 학생이 제일 믿는 자리라 지어내기의 대가가 제일 크다.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import re
from dataclasses import dataclass, field
from typing import Any

# 시트 머리글 → 우리 이름. 총학이 칸 순서를 바꿔도 이름으로 찾는다.
# ★ 위치로 읽으면 칸 하나만 옮겨도 조용히 어긋난다.
HEADERS = {
    "게시일": "published_at",
    "분류": "categories",
    "제목": "title",
    "내용": "body",
    "인스타링크": "link",
    "마감일": "deadline",
    "작성국": "bureau",
}

REQUIRED = ("published_at", "title")

# 2026-09-01 · 2026.9.1 · 2026/9/1 · 9/1 을 받는다. 그 밖은 안 받는다.
_DATE = re.compile(r"^\s*(?:(\d{4})[.\-/])?(\d{1,2})[.\-/](\d{1,2})\s*$")


class ParseError(ValueError):
    """시트를 아예 못 읽었다. 기존 데이터는 건드리지 않는다."""


@dataclass
class ParsedSheet:
    rows: list[dict[str, Any]] = field(default_factory=list)
    quarantined: list[tuple[int, str]] = field(default_factory=list)

    @property
    def counts(self) -> tuple[int, int]:
        return len(self.rows), len(self.quarantined)


def parse_date(raw: str, *, today: dt.date | None = None) -> str | None:
    """날짜 칸 → YYYY-MM-DD. 날짜가 아니면 None.

    ★ 연도를 안 쓰면 오늘 연도로 본다. 그건 추측이 아니라 규약이다 —
      '9/1' 은 사람이 올해라고 읽는다. 다만 **지어낸 값이면 안 되므로**
      아예 형식이 아닌 것('9월 초', '미정')은 None 으로 돌려보낸다.
    """
    m = _DATE.match(raw or "")
    if not m:
        return None
    y, mo, d = m.groups()
    year = int(y) if y else (today or dt.date.today()).year
    try:
        return dt.date(year, int(mo), int(d)).isoformat()
    except ValueError:
        return None


def _key(published_at: str, title: str) -> str:
    """시트에 행 ID 가 없어서 게시일+제목으로 만든다.

    ★ 제목을 고치면 새 글이 된다. 그게 맞다 —
      같은 글의 수정인지 다른 글인지 우리가 알 방법이 없고,
      추측해서 합치면 옛 내용이 새 내용을 덮어쓸 수 있다.
    """
    h = hashlib.sha256(f"{published_at}|{title.strip()}".encode("utf-8"))
    return f"council-{h.hexdigest()[:16]}"


def parse(text: str, *, today: dt.date | None = None) -> ParsedSheet:
    """게시된 CSV → 행 목록."""
    text = (text or "").lstrip("﻿")
    if not text.strip():
        raise ParseError("시트가 비어 있다 (응답 본문 0바이트)")

    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ParseError("머리글 줄이 없다") from None

    # 머리글 → 열 번호. 공백·괄호 주석을 무시하고 이름으로 찾는다.
    col: dict[str, int] = {}
    for i, name in enumerate(header):
        clean = re.sub(r"\s+", "", (name or "")).split("(")[0]
        if clean in HEADERS:
            col[HEADERS[clean]] = i

    missing = [k for k in REQUIRED if k not in col]
    if missing:
        # ★ 머리글이 바뀌면 **전부 격리**가 아니라 파싱 실패다.
        #   기존 데이터를 지우지 않고 그대로 두기 위해서다 (T3 와 같은 규칙).
        raise ParseError(
            f"머리글에 {missing} 가 없다. 받은 머리글: {header[:8]}")

    out = ParsedSheet()
    for n, row in enumerate(reader, start=2):        # 2행부터가 자료
        def get(field_name: str) -> str:
            i = col.get(field_name)
            return (row[i].strip() if i is not None and i < len(row) else "")

        title = get("title")
        pub_raw = get("published_at")
        if not title and not pub_raw:
            continue                                   # 빈 줄은 조용히 넘긴다

        pub = parse_date(pub_raw, today=today)
        if not title:
            out.quarantined.append((n, "제목이 비었다"))
            continue
        if not pub:
            out.quarantined.append((n, f"게시일을 날짜로 못 읽었다: {pub_raw!r}"))
            continue

        dl_raw = get("deadline")
        dl = parse_date(dl_raw, today=today)
        if dl_raw and not dl:
            # ★ 마감일이 이상하면 **그 줄을 통째로 격리한다.**
            #   마감일을 NULL 로 두고 내보내면 '마감 없는 공지' 가 되어
            #   지난 모집이 영영 안 꺼진다. 지어내지도, 무시하지도 않는다.
            out.quarantined.append((n, f"마감일을 날짜로 못 읽었다: {dl_raw!r}"))
            continue

        out.rows.append({
            "post_key": _key(pub, title),
            "published_at": pub,
            "title": title,
            "body": get("body"),          # ★ 원문 그대로. 자르지도 다듬지도 않는다.
            "link": get("link"),
            "deadline": dl,
            # ★ 사람이 적은 그대로 싣는다. 비었으면 빈 채로 둔다 —
            #   제목을 보고 '이건 취업이겠지' 하고 채우면 그게 추측이다.
            "categories": get("categories"),
            "bureau": get("bureau"),
            "row_no": n,
        })
    return out
