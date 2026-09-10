"""구글폼 응답 제출 도구.

폼 페이지를 읽어 문항 구조를 파악하고(schema), 답안을 폼 필드로 바꾸고(payload),
formResponse 로 보낸다(client). 답은 사람이 만든 CSV 에서만 온다 — 이 도구는
응답을 지어내지 않는다.
"""

from .schema import Form, Question, QuestionType, parse_form_html, normalize_form_url
from .payload import build_payload, PayloadError
from .client import FormClient, SubmitResult

__all__ = [
    "Form", "Question", "QuestionType", "parse_form_html", "normalize_form_url",
    "build_payload", "PayloadError",
    "FormClient", "SubmitResult",
]
