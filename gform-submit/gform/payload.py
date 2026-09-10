"""답안(dict) → formResponse 필드.

검증은 여기서 한다. 구글이 거절하는 것을 보내고 나서 알면 늦다 — 200 으로 폼을
다시 그려주기 때문에 실패가 조용하다. 그래서 보내기 전에 우리가 먼저 거른다.

  필수 문항 비어 있음      → PayloadError
  선택지에 없는 값         → '기타' 허용 문항이면 other 로, 아니면 PayloadError
  체크박스 아닌데 여러 값  → PayloadError
  날짜·시각 형식 오류      → PayloadError
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Union

from .schema import Form, Question, QuestionType

Value = Union[str, list[str], tuple[str, ...], None]
OTHER_SENTINEL = "__other_option__"


class PayloadError(ValueError):
    def __init__(self, question: Question | None, message: str):
        self.question = question
        where = f"[{question.label}] " if question else ""
        super().__init__(where + message)


def _as_list(v: Value) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip() != ""]
    s = str(v).strip()
    return [s] if s else []


def split_multi(cell: str, sep: str = "|") -> list[str]:
    """CSV 한 칸의 여러 값. 구분자는 호출 쪽 옵션. 공백만 있는 조각은 버린다."""
    return [p.strip() for p in cell.split(sep) if p.strip()]


_DATE_RE = re.compile(r"^(?:(\d{4})-)?(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2}))?$")
_TIME_RE = re.compile(r"^(\d{1,3}):(\d{2})(?::(\d{2}))?$")


def _date_fields(q: Question, raw: str, out: list[tuple[str, str]]) -> None:
    m = _DATE_RE.match(raw)
    if not m:
        raise PayloadError(q, f"날짜 형식은 YYYY-MM-DD 또는 YYYY-MM-DD HH:MM 입니다: {raw!r}")
    year, month, day, hour, minute = m.groups()
    if q.date_has_year and not year:
        raise PayloadError(q, f"연도가 필요한 날짜 문항입니다: {raw!r}")
    if q.date_has_time and hour is None:
        raise PayloadError(q, f"시각이 필요한 날짜 문항입니다 (YYYY-MM-DD HH:MM): {raw!r}")
    if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
        raise PayloadError(q, f"월·일 범위를 벗어났습니다: {raw!r}")
    f = q.field_name
    if q.date_has_year and year:
        out.append((f"{f}_year", year))
    out.append((f"{f}_month", str(int(month))))
    out.append((f"{f}_day", str(int(day))))
    if q.date_has_time and hour is not None:
        out.append((f"{f}_hour", str(int(hour))))
        out.append((f"{f}_minute", minute))


def _time_fields(q: Question, raw: str, out: list[tuple[str, str]]) -> None:
    m = _TIME_RE.match(raw)
    if not m:
        raise PayloadError(q, f"시각 형식은 HH:MM (기간은 HH:MM:SS) 입니다: {raw!r}")
    hour, minute, second = m.groups()
    if not q.time_is_duration and not (0 <= int(hour) <= 23):
        raise PayloadError(q, f"시는 0~23 이어야 합니다: {raw!r}")
    if not (0 <= int(minute) <= 59):
        raise PayloadError(q, f"분은 0~59 이어야 합니다: {raw!r}")
    f = q.field_name
    out.append((f"{f}_hour", str(int(hour))))
    out.append((f"{f}_minute", minute))
    if q.time_is_duration:
        out.append((f"{f}_second", second or "0"))


def _choice_fields(q: Question, values: list[str], out: list[tuple[str, str]]) -> None:
    if len(values) > 1 and not q.multi:
        raise PayloadError(q, f"하나만 고를 수 있는 문항에 {len(values)}개가 들어왔습니다: {values}")
    f = q.field_name
    for v in values:
        if v in q.options:
            out.append((f, v))
        elif q.allows_other:
            out.append((f, OTHER_SENTINEL))
            out.append((f"{f}.other_option_response", v))
        else:
            raise PayloadError(q, f"선택지에 없는 값입니다: {v!r}. 가능한 값: {q.options}")
    if q.multi and values:
        # 체크박스는 빈 sentinel 을 같이 보내는 것이 실제 브라우저 동작이다.
        out.append((f"{f}_sentinel", ""))


def build_payload(form: Form, answers: Mapping[Any, Value], *,
                  email: str | None = None) -> list[tuple[str, str]]:
    """answers 의 키는 entry id(int 또는 'entry.123') 이거나 Question.label 이다.

    반환은 (name, value) 목록 — 체크박스처럼 같은 이름이 반복되므로 dict 가 아니다.
    """
    # 키 → Question 해석
    by_label = {q.label: q for q in form.inputs}
    by_entry = {q.entry_id: q for q in form.inputs}
    resolved: dict[int, list[str]] = {}
    for key, val in answers.items():
        q = _resolve(key, by_label, by_entry)
        if q is None:
            raise PayloadError(None, f"폼에 없는 문항입니다: {key!r}")
        resolved[q.entry_id] = _as_list(val)

    out: list[tuple[str, str]] = []
    for q in form.inputs:
        values = resolved.get(q.entry_id, [])
        if not values:
            if q.required:
                raise PayloadError(q, "필수 문항이 비어 있습니다")
            continue
        if q.qtype in (QuestionType.SHORT, QuestionType.PARAGRAPH):
            if len(values) > 1:
                raise PayloadError(q, "텍스트 문항에는 값 하나만 넣을 수 있습니다")
            out.append((q.field_name, values[0]))
        elif q.qtype is QuestionType.DATE:
            _date_fields(q, values[0], out)
        elif q.qtype is QuestionType.TIME:
            _time_fields(q, values[0], out)
        elif q.qtype in (QuestionType.CHOICE, QuestionType.DROPDOWN, QuestionType.CHECKBOX,
                         QuestionType.SCALE, QuestionType.GRID):
            _choice_fields(q, values, out)
        else:  # pragma: no cover — inputs 에서 걸러진다
            raise PayloadError(q, f"지원하지 않는 유형: {q.qtype.name}")

    if form.collects_email:
        if not email:
            raise PayloadError(None, "이 폼은 이메일을 수집합니다. emailAddress 칸(또는 --email)이 필요합니다")
        out.append(("emailAddress", email))
    elif email:
        # 폼이 안 받는 값을 보내는 건 의미 없다. 조용히 버리지 않고 알린다.
        raise PayloadError(None, "이 폼은 이메일을 수집하지 않는데 emailAddress 가 들어왔습니다")

    out.append(("fvv", "1"))
    out.append(("pageHistory", form.page_history))
    if form.fbzx:
        out.append(("fbzx", form.fbzx))
        out.append(("partialResponse", f'[null,null,"{form.fbzx}"]'))
    return out


def _resolve(key: Any, by_label: dict, by_entry: dict) -> Question | None:
    if isinstance(key, int):
        return by_entry.get(key)
    k = str(key).strip()
    if k.startswith("entry.") and k[6:].isdigit():
        return by_entry.get(int(k[6:]))
    if k.isdigit():
        return by_entry.get(int(k))
    if k in by_label:
        return by_label[k]
    # '제목 [entry.123]' 형태 — 제목이 겹칠 때 템플릿이 이렇게 쓴다
    m = re.search(r"\[entry\.(\d+)\]\s*$", k)
    if m:
        return by_entry.get(int(m.group(1)))
    return None


def header_for(form: Form) -> list[str]:
    """CSV 템플릿 헤더. 라벨이 겹치면 `[entry.N]` 을 붙여 가른다."""
    labels = [q.label for q in form.inputs]
    dup = {l for l in labels if labels.count(l) > 1}
    cols = [f"{q.label} [entry.{q.entry_id}]" if q.label in dup else q.label for q in form.inputs]
    if form.collects_email:
        cols.insert(0, "emailAddress")
    return cols
