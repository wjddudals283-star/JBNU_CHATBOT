"""카카오 스킬 응답 빌더 — UI 제약을 코드로 강제한다.

사람이 매번 기억할 수 없다. 넘치면 자동으로 자르거나 예외를 던진다.

| 항목 | 제한 |
|---|---|
| `outputs` | 1~3개 |
| `quickReplies` | ≤10 |
| `simpleText.text` | ≤1000자 (500자 초과 시 '전체 보기'로 접힘) |
| **`listCard.items`** | **≤5** — 초과 시 5개 + '전체 보기' 버튼 |
| `textCard` | title ≤50 / desc ≤400 |
| `basicCard` | title ≤50 / desc ≤230 / thumbnail 필수 |
| 버튼 | 가로 ≤2(라벨 8자) / 세로 ≤3(라벨 14자) |
| 루트 | `"version": "2.0"` 필수 |

★ 자르는 것과 던지는 것의 구분
  · 넘쳐도 **의미가 보존되면** 자른다 (listCard 5개 + 전체 보기)
  · 자르면 **뜻이 달라지면** 던진다 (outputs 0개, 버튼 라벨 초과)
  조용히 잘려서 답이 반쪽이 되는 것이 이 프로젝트에서 가장 피해야 할 실패다.
"""

from __future__ import annotations

from typing import Any

VERSION = "2.0"

MAX_OUTPUTS = 3
MAX_QUICK_REPLIES = 10
MAX_SIMPLE_TEXT = 1000
SIMPLE_TEXT_FOLD = 500
MAX_LIST_ITEMS = 5
MAX_CAROUSEL = 10
MAX_TEXTCARD_TITLE = 50
MAX_TEXTCARD_DESC = 400
MAX_BASICCARD_TITLE = 50
MAX_BASICCARD_DESC = 230
MAX_BTN_LABEL_H = 8      # 가로 배치
MAX_BTN_LABEL_V = 14     # 세로 배치
MAX_BTN_H = 2
MAX_BTN_V = 3

# listCard.items 의 title/description 은 공식 문서에 별도 상한이 없으나
# 실무상 길면 잘려 보이므로 안전선을 둔다.
LIST_ITEM_TITLE = 36
LIST_ITEM_DESC = 20


class KakaoSpecError(ValueError):
    """조용히 자르면 뜻이 달라지는 위반. 렌더 대신 실패한다."""


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def web_button(label: str, url: str, *, horizontal: bool = False) -> dict:
    limit = MAX_BTN_LABEL_H if horizontal else MAX_BTN_LABEL_V
    if len(label) > limit:
        raise KakaoSpecError(
            f"버튼 라벨 {len(label)}자 > {limit}자 ({'가로' if horizontal else '세로'} 배치): {label!r}"
        )
    return {"action": "webLink", "label": label, "webLinkUrl": url}


def msg_button(label: str, message: str, *, horizontal: bool = False) -> dict:
    limit = MAX_BTN_LABEL_H if horizontal else MAX_BTN_LABEL_V
    if len(label) > limit:
        raise KakaoSpecError(f"버튼 라벨 {len(label)}자 > {limit}자: {label!r}")
    return {"action": "message", "label": label, "messageText": message}


def quick_reply(label: str, message: str | None = None) -> dict:
    return {"action": "message", "label": label, "messageText": message or label}


def simple_text(text: str) -> dict:
    text = (text or "").strip()
    if not text:
        raise KakaoSpecError("simpleText 가 비어 있다")
    if len(text) > MAX_SIMPLE_TEXT:
        raise KakaoSpecError(
            f"simpleText {len(text)}자 > {MAX_SIMPLE_TEXT}자 — 자르면 뜻이 달라진다. "
            f"listCard 로 나누거나 링크로 빼라"
        )
    return {"simpleText": {"text": text}}


def text_card(title: str, description: str, buttons: list[dict] | None = None) -> dict:
    card: dict[str, Any] = {
        "title": _clip(title, MAX_TEXTCARD_TITLE),
        "description": _clip(description, MAX_TEXTCARD_DESC),
    }
    if buttons:
        card["buttons"] = _check_buttons(buttons)
    return {"textCard": card}


def _check_buttons(buttons: list[dict]) -> list[dict]:
    if len(buttons) > MAX_BTN_V:
        raise KakaoSpecError(f"버튼 {len(buttons)}개 > {MAX_BTN_V}개")
    return buttons


def list_card(header: str, items: list[dict], *, buttons: list[dict] | None = None,
              overflow_button: dict | None = None) -> tuple[dict, int]:
    """listCard. **items 5개 초과분은 카카오가 조용히 자른다.**

    그래서 여기서 먼저 자르고, 잘린 개수를 같이 돌려준다.
    호출자는 '전체 보기' 버튼으로 나머지 경로를 반드시 열어야 한다 (T14).
    """
    if not items:
        raise KakaoSpecError("listCard.items 가 비어 있다")

    shown = items[:MAX_LIST_ITEMS]
    dropped = len(items) - len(shown)

    card: dict[str, Any] = {
        "header": {"title": _clip(header, LIST_ITEM_TITLE)},
        "items": [_list_item(i) for i in shown],
    }
    btns = list(buttons or [])
    if dropped and overflow_button is None:
        raise KakaoSpecError(
            f"{dropped}건이 잘리는데 '전체 보기' 버튼이 없다 — "
            f"답이 반쪽이 된 채로 나간다"
        )
    if dropped:
        btns.insert(0, overflow_button)
    if btns:
        card["buttons"] = _check_buttons(btns)
    return {"listCard": card}, dropped


def _list_item(item: dict) -> dict:
    out = {"title": _clip(item["title"], LIST_ITEM_TITLE)}
    if item.get("description"):
        out["description"] = _clip(item["description"], LIST_ITEM_DESC)
    if item.get("link"):
        out["link"] = {"web": item["link"]}
    return out


def response(outputs: list[dict], quick_replies: list[dict] | None = None) -> dict:
    """스킬 응답 루트. version 2.0 이 없으면 구버전으로 간주돼 깨진다."""
    if not outputs:
        raise KakaoSpecError("outputs 가 비어 있다 — 빈 응답을 내보내면 안 된다")
    if len(outputs) > MAX_OUTPUTS:
        raise KakaoSpecError(f"outputs {len(outputs)}개 > {MAX_OUTPUTS}개")

    template: dict[str, Any] = {"outputs": outputs}
    if quick_replies:
        # 추천질문은 잘려도 뜻이 안 달라진다 → 자른다
        template["quickReplies"] = quick_replies[:MAX_QUICK_REPLIES]
    return {"version": VERSION, "template": template}


def validate(payload: dict) -> list[str]:
    """응답 JSON 이 규격을 지키는지 검사한다 (T8).

    빌더를 안 쓰고 손으로 만든 응답도 여기서 걸러진다.
    """
    errs: list[str] = []
    if payload.get("version") != VERSION:
        errs.append(f"version 이 {VERSION!r} 이 아니다: {payload.get('version')!r}")
    t = payload.get("template")
    if not isinstance(t, dict):
        errs.append("template 이 없다")
        return errs

    outputs = t.get("outputs")
    if not outputs:
        errs.append("outputs 가 비어 있다")
    elif len(outputs) > MAX_OUTPUTS:
        errs.append(f"outputs {len(outputs)}개 > {MAX_OUTPUTS}")

    for o in outputs or []:
        if "simpleText" in o:
            n = len(o["simpleText"].get("text", ""))
            if n > MAX_SIMPLE_TEXT:
                errs.append(f"simpleText {n}자 > {MAX_SIMPLE_TEXT}")
        if "listCard" in o:
            items = o["listCard"].get("items", [])
            if len(items) > MAX_LIST_ITEMS:
                errs.append(f"listCard.items {len(items)}개 > {MAX_LIST_ITEMS} (초과분은 잘린다)")
            if not items:
                errs.append("listCard.items 가 비어 있다")
            for b in o["listCard"].get("buttons", []):
                if len(b.get("label", "")) > MAX_BTN_LABEL_V:
                    errs.append(f"버튼 라벨 초과: {b.get('label')!r}")
        if "textCard" in o:
            c = o["textCard"]
            if len(c.get("title", "")) > MAX_TEXTCARD_TITLE:
                errs.append("textCard.title 초과")
            if len(c.get("description", "")) > MAX_TEXTCARD_DESC:
                errs.append("textCard.description 초과")
        if "basicCard" in o:
            if not o["basicCard"].get("thumbnail"):
                errs.append("basicCard 에 thumbnail 이 없다 (필수)")

    qr = t.get("quickReplies", [])
    if len(qr) > MAX_QUICK_REPLIES:
        errs.append(f"quickReplies {len(qr)}개 > {MAX_QUICK_REPLIES}")
    return errs
