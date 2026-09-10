"""구글폼 페이지 → 문항 구조.

viewform HTML 안에 `var FB_PUBLIC_LOAD_DATA_ = [...]` 로 폼 정의가 통째로 들어 있다.
공식 스키마가 아니라 관측으로 알아낸 구조다. 위치가 바뀌면 여기서 깨진다 —
그래서 모든 인덱스 접근은 `_at()` 으로 감싸 None 으로 떨어지게 했다.

관측된 구조 (2024~2026 기준)
  data[1][1]        문항 목록
  data[1][8]        폼 제목 (없으면 data[3])
  data[1][10][6]    이메일 수집 여부 (0 = 안 함)
  item[0]  문항 id          item[1] 제목       item[2] 설명
  item[3]  유형 코드        item[4] 위젯 목록 (그리드는 행마다 하나)
  widget[0] entry id  widget[1] 선택지 [[label, …, is_other], …]
  widget[2] 필수(1)   widget[3] 그리드 행 라벨 [label]
"""

from __future__ import annotations

import enum
import json
import re
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlparse


class QuestionType(enum.IntEnum):
    SHORT = 0
    PARAGRAPH = 1
    CHOICE = 2          # 객관식 (하나)
    DROPDOWN = 3
    CHECKBOX = 4        # 체크박스 (여러 개)
    SCALE = 5           # 선형 배율
    TITLE = 6           # 제목·설명 블록 — 입력 없음
    GRID = 7            # 객관식/체크박스 그리드
    PAGE_BREAK = 8
    DATE = 9
    TIME = 10
    IMAGE = 11
    VIDEO = 12
    FILE_UPLOAD = 13
    UNKNOWN = -1

    @classmethod
    def of(cls, code: Any) -> "QuestionType":
        try:
            return cls(int(code))
        except (ValueError, TypeError):
            return cls.UNKNOWN


# 입력을 받지 않는 유형 — CSV 칸을 만들지 않는다.
NON_INPUT = {QuestionType.TITLE, QuestionType.PAGE_BREAK, QuestionType.IMAGE,
             QuestionType.VIDEO, QuestionType.UNKNOWN}
# 입력은 받지만 이 도구로는 못 보내는 유형 — 있으면 경고하고 건너뛴다.
UNSUPPORTED = {QuestionType.FILE_UPLOAD}
MULTI_VALUE = {QuestionType.CHECKBOX}
WITH_OPTIONS = {QuestionType.CHOICE, QuestionType.DROPDOWN, QuestionType.CHECKBOX,
                QuestionType.SCALE, QuestionType.GRID}


@dataclass
class Question:
    """CSV 의 한 칸에 대응하는 입력 단위. 그리드는 행마다 Question 하나다."""
    entry_id: int
    title: str
    qtype: QuestionType
    required: bool = False
    options: list[str] = field(default_factory=list)
    allows_other: bool = False
    row_label: Optional[str] = None      # 그리드 행
    page: int = 0
    description: str = ""
    # 날짜 문항 옵션: 연도 포함? 시각 포함?
    date_has_year: bool = True
    date_has_time: bool = False
    time_is_duration: bool = False

    @property
    def field_name(self) -> str:
        return f"entry.{self.entry_id}"

    @property
    def label(self) -> str:
        """CSV 헤더에 쓰는 이름. 그리드는 `문항 » 행`."""
        return f"{self.title} » {self.row_label}" if self.row_label else self.title

    @property
    def is_input(self) -> bool:
        return self.qtype not in NON_INPUT and self.qtype not in UNSUPPORTED

    @property
    def multi(self) -> bool:
        return self.qtype in MULTI_VALUE


@dataclass
class Form:
    form_id: str
    title: str
    questions: list[Question]
    page_count: int = 1
    collects_email: bool = False
    fbzx: Optional[str] = None
    description: str = ""
    skipped: list[str] = field(default_factory=list)   # 지원 안 하는 문항 제목
    public: bool = True      # /forms/d/e/<id> (공개 응답 주소) 인가, /forms/d/<id> (편집자 주소) 인가

    @property
    def inputs(self) -> list[Question]:
        return [q for q in self.questions if q.is_input]

    def by_entry(self, entry_id: int) -> Optional[Question]:
        for q in self.questions:
            if q.entry_id == entry_id:
                return q
        return None

    @property
    def page_history(self) -> str:
        return ",".join(str(i) for i in range(self.page_count))

    @property
    def _base(self) -> str:
        return f"https://docs.google.com/forms/d/{'e/' if self.public else ''}{self.form_id}"

    @property
    def viewform_url(self) -> str:
        return self._base + "/viewform"

    @property
    def response_url(self) -> str:
        return self._base + "/formResponse"


class SchemaError(ValueError):
    pass


# ───────────────────────── URL ─────────────────────────

_ID_RE = re.compile(r"/forms/d/(e/)?([A-Za-z0-9_-]+)")


def normalize_form_url(url: str) -> tuple[str, str]:
    """(form_id, viewform_url). forms.gle 단축 주소는 호출 쪽에서 먼저 풀어야 한다.

    /forms/d/e/<id>/  공개 응답 주소
    /forms/d/<id>/    편집자 주소 — 그대로 viewform 을 붙여 쓴다
    """
    u = urlparse(url if "://" in url else "https://" + url)
    if u.netloc.endswith("forms.gle"):
        raise SchemaError("forms.gle 단축 주소는 먼저 풀어야 합니다 (FormClient.resolve_url)")
    m = _ID_RE.search(u.path)
    if not m or "docs.google.com" not in u.netloc:
        raise SchemaError(f"구글폼 주소가 아닙니다: {url}")
    is_public, form_id = bool(m.group(1)), m.group(2)
    base = f"https://docs.google.com/forms/d/{'e/' if is_public else ''}{form_id}"
    return form_id, base + "/viewform"


# ───────────────────────── HTML → data ─────────────────────────

_MARK = "FB_PUBLIC_LOAD_DATA_"


def extract_load_data(html: str) -> list:
    """`var FB_PUBLIC_LOAD_DATA_ = [...]` 의 배열만 뽑는다. 정규식으로 끝을 찾지 않고
    JSON 디코더로 한 값을 읽어 `];` 가 본문에 있어도 안전하다."""
    at = html.find(_MARK)
    if at < 0:
        raise SchemaError("폼 데이터(FB_PUBLIC_LOAD_DATA_)가 없습니다 — 로그인이 필요한 폼이거나 폼 페이지가 아닙니다")
    start = html.find("[", at)
    if start < 0:
        raise SchemaError("폼 데이터 시작을 찾지 못했습니다")
    try:
        data, _ = json.JSONDecoder().raw_decode(html, start)
    except json.JSONDecodeError as e:
        raise SchemaError(f"폼 데이터 JSON 해석 실패: {e}") from e
    if not isinstance(data, list):
        raise SchemaError("폼 데이터가 배열이 아닙니다")
    return data


def _at(obj: Any, *idx: int, default: Any = None) -> Any:
    """중첩 리스트 인덱스 접근. 없으면 default. 구조가 달라져도 예외 대신 None."""
    cur = obj
    for i in idx:
        if not isinstance(cur, list) or i >= len(cur) or i < -len(cur):
            return default
        cur = cur[i]
    return default if cur is None else cur


_FBZX_RE = re.compile(r'name="fbzx"\s+value="(-?\d+)"')
_EMAIL_INPUT_RE = re.compile(r'name="emailAddress"')


def parse_form_html(html: str, form_id: str = "") -> Form:
    data = extract_load_data(html)
    body = _at(data, 1, default=[])
    items = _at(body, 1, default=[]) or []
    title = _at(body, 8) or _at(data, 3) or ""
    description = _at(body, 0) or ""

    # 이메일 수집: settings[6] 가 0 이 아니면 켜진 것. HTML 에 입력칸이 있어도 켜진 것.
    email_flag = _at(body, 10, 6, default=0)
    collects_email = bool(email_flag) or bool(_EMAIL_INPUT_RE.search(html))

    questions: list[Question] = []
    skipped: list[str] = []
    page = 0
    for item in items:
        qtype = QuestionType.of(_at(item, 3))
        qtitle = str(_at(item, 1) or "").strip()
        qdesc = str(_at(item, 2) or "").strip()
        if qtype is QuestionType.PAGE_BREAK:
            page += 1
            continue
        if qtype in NON_INPUT:
            continue
        if qtype in UNSUPPORTED:
            skipped.append(qtitle or f"(item {_at(item, 0)})")
            continue
        widgets = _at(item, 4, default=[]) or []
        for w in widgets:
            entry_id = _at(w, 0)
            if entry_id is None:
                continue
            raw_opts = _at(w, 1, default=[]) or []
            options, allows_other = [], False
            for o in raw_opts:
                label = _at(o, 0)
                is_other = bool(_at(o, 4, default=0))
                if is_other:
                    allows_other = True
                elif label is not None:
                    options.append(str(label))
            row = _at(w, 3, 0)
            q = Question(
                entry_id=int(entry_id),
                title=qtitle,
                qtype=qtype,
                required=bool(_at(w, 2, default=0)),
                options=options,
                allows_other=allows_other,
                row_label=str(row) if (qtype is QuestionType.GRID and row is not None) else None,
                page=page,
                description=qdesc,
            )
            if qtype is QuestionType.DATE:
                # widget[7] = [has_time, has_year] 로 관측됨. 없으면 보수적으로 연도 있음·시각 없음.
                q.date_has_time = bool(_at(w, 7, 0, default=0))
                q.date_has_year = bool(_at(w, 7, 1, default=1))
            if qtype is QuestionType.TIME:
                q.time_is_duration = bool(_at(w, 6, 0, default=0))
            questions.append(q)

    fbzx_m = _FBZX_RE.search(html)
    return Form(
        form_id=form_id or str(_at(data, 14) or ""),
        title=str(title),
        questions=questions,
        page_count=page + 1,
        collects_email=collects_email,
        fbzx=fbzx_m.group(1) if fbzx_m else None,
        description=str(description),
        skipped=skipped,
    )
