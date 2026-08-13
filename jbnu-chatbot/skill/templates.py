"""응답 템플릿 — ★생성 모델 금지 구역.

    답변 = 템플릿[block_id] ⊗ facts

여기 있는 건 전부 f-string 이다. 값이 없으면 문장을 만들지 않고 C 분기로 간다.
"모른다고 말할 수 있는 챗봇이 정확한 챗봇이다."

가격 표시 규칙 (§가격 조인)
  · 매칭된 것만 카드에 넣는다. 68% 가 정상 상태라 —를 그대로 노출하면
    카드의 3분의 1이 비어 보여 챗봇이 고장난 것처럼 읽힌다
  · 미매칭 개수는 footer 에 알리고 단가표 링크로 넘긴다
  · 없는 값을 만들지 않는다는 원칙은 그대로다. 표현만 정리한 것이다
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from skill import kakao
from skill.josa import attach as J
from skill.branch import Branch, MealAnswer

MEAL_KO = {"breakfast": "아침", "lunch": "점심", "dinner": "저녁"}


def observed_label(iso: str | None) -> str:
    if not iso:
        return ""
    d = dt.datetime.fromisoformat(iso)
    return f"{d.month}/{d.day} {d.hour:02d}:{d.minute:02d} 확인"


def date_label(date: str) -> str:
    d = dt.date.fromisoformat(date)
    return f"{d.month}/{d.day}"


def render_meal(answer: MealAnswer, *, facility_name: str, date: str,
                meal_type: str, source_url: str, price_url: str | None = None,
                contact: str | None = None,
                has_price_table: bool = False) -> dict:
    """식단 질문 하나 → 카카오 응답. 분기별로 문장이 완전히 다르다."""
    meal_ko = MEAL_KO.get(meal_type, meal_type)
    dl = date_label(date)

    if answer.branch is Branch.A:
        return _render_a(answer, facility_name=facility_name, dl=dl,
                         meal_ko=meal_ko, source_url=source_url,
                         price_url=price_url, has_price_table=has_price_table)
    if answer.branch is Branch.B:
        return _render_b(answer, facility_name=facility_name, dl=dl,
                         meal_ko=meal_ko, source_url=source_url,
                         date=date, meal_type=meal_type)
    if answer.branch is Branch.C1:
        return _render_c1(facility_name=facility_name, dl=dl, meal_ko=meal_ko,
                          source_url=source_url)
    return _render_c2(answer, facility_name=facility_name, dl=dl,
                      meal_ko=meal_ko, source_url=source_url, contact=contact)


# ── A. 사실 있음 + 신선함 ────────────────────────────────────────

def _render_a(answer: MealAnswer, *, facility_name: str, dl: str, meal_ko: str,
              source_url: str, price_url: str | None,
              has_price_table: bool = False) -> dict:
    # ── description 은 **행끼리 구별될 때만** 넣는다 ──
    #   같은 값이 4줄 반복되면 정보량이 0이다. 전 행이 같은 값이면
    #   그건 행 정보가 아니라 카드 정보이므로 header 로 올린다.
    raw: list[tuple[str, str | None, str]] = []   # (이름, 가격, 코너)
    for row in answer.operating_rows:
        zone = row["zone"] or row["corner"]
        for it in row["items"]:
            raw.append((it["name"], it.get("price_text"), zone))

    unpriced = sum(1 for _, p, _ in raw if not p)
    zones = {z for _, _, z in raw if z}
    any_price = any(p for _, p, _ in raw)
    # 가격이 하나도 없고 코너가 전부 같으면 → 코너를 header 로
    header_suffix = ""
    if not any_price and len(zones) == 1:
        header_suffix = f" · {next(iter(zones))}"

    items: list[dict] = []
    for name, price, zone in raw:
        entry = {"title": name}
        desc = price or (zone if not header_suffix else None)
        if desc:
            entry["description"] = desc
        items.append(entry)

    if not items:
        return _render_c2(answer, facility_name=facility_name, dl=dl,
                          meal_ko=meal_ko, source_url=source_url, contact=None)

    footer_bits = [observed_label(answer.observed_at)]
    # 단가표가 있는 시설에서만 안내한다. 생활관은 단가표 자체가 없어서
    # '단가표 참고'라고 하면 있지도 않은 곳으로 보내는 셈이 된다.
    if unpriced and has_price_table:
        footer_bits.append(f"가격 미표기 {unpriced}개는 단가표 참고")

    overflow = None
    if len(items) > kakao.MAX_LIST_ITEMS:
        overflow = kakao.web_button(f"전체 {len(items)}개 보기", source_url)

    card, dropped = kakao.list_card(
        f"{facility_name} {dl} {meal_ko}{header_suffix}", items,
        buttons=[kakao.web_button("원문 보기", source_url)] if not overflow else None,
        overflow_button=overflow,
    )

    qr = [kakao.quick_reply("내일 메뉴", f"{facility_name} 내일 {meal_ko}"),
          kakao.quick_reply("운영시간", f"{facility_name} 몇 시까지 해")]
    if price_url and unpriced:
        qr.append(kakao.quick_reply("가격표", f"{facility_name} 가격"))

    note = " · ".join(x for x in footer_bits if x)
    outputs = [card]
    if note:
        outputs.append(kakao.simple_text(note))
    return kakao.response(outputs, qr)


# ── B. 명확한 미운영 ─────────────────────────────────────────────

WEEKDAY_KO = ["일", "월", "화", "수", "목", "금", "토", "공휴일"]


def hours_summary(hours: list[dict[str, Any]], *, meal_type: str | None = None) -> str:
    """운영시간 관측을 사람이 읽을 한 줄로. **판단 근거를 그대로 보여준다.**

    ★ 단서(caveat)를 쓰지 않는다. "학기 구분 없는 시간표 기준이에요" 같은 문장은
      학생이 뭘 다르게 해야 할지 알려주지 않고 의심만 심는다. 그러면 "혹시 모르니까"
      가보게 되고, 그게 우리가 막으려던 헛걸음이다.
      단서는 학생이 검증할 수 없지만 관측은 검증할 수 있다 —
      시간표가 이상하면 학생이 먼저 알아챈다.
    """
    open_rows = [h for h in hours if not h["is_closed"] and h.get("open_time")]
    if meal_type is not None:
        open_rows = [h for h in open_rows if h["meal_type"] in ("", meal_type)]
    if not open_rows:
        return ""

    grouped: dict[tuple, set[int]] = {}
    for h in open_rows:
        key = (h["meal_type"], h["open_time"], h["close_time"])
        grouped.setdefault(key, set()).add(h["weekday"])

    parts = []
    for (meal, o, c), days in sorted(grouped.items(), key=lambda x: (x[0][1], x[0][0])):
        if days >= {1, 2, 3, 4, 5}:
            when = "평일"
        else:
            when = "·".join(WEEKDAY_KO[d] for d in sorted(days))
        label = MEAL_KO.get(meal, meal)
        parts.append(f"{when} {label} {o}–{c}".strip())
    return ", ".join(parts)


CLOSED_MARKER_WORDS = {"운영없음", "미운영", "식사없음"}


def _not_offered_text(answer: MealAnswer, *, facility_name: str, dl: str,
                      meal_ko: str, date: str, meal_type: str) -> str:
    """'그 날은 안 한다'와 '그 끼니를 아예 안 한다'는 다른 말이다.

    ★ 둘을 같은 문장으로 내면 자기모순이 된다 — 일요일이라 닫혔는데
      "점심은 운영하지 않아요"라고 하면서 평일 점심시간을 근거로 보여주게 된다.
      (실제로 렌더해 보고 발견한 오류다.)
    """
    wd = (dt.date.fromisoformat(date).weekday() + 1) % 7   # 0=일 규약
    closed_today = [h for h in answer.hours
                    if h["weekday"] == wd and h["is_closed"]
                    and h["meal_type"] in ("", meal_type)]

    stamp = (f" ({observed_label(answer.hours[0]['observed_at'])})"
             if answer.hours else "")

    if closed_today:
        # 그 요일이 쉬는 날이다. 평상시 운영시간을 근거로 보여준다.
        lines = [f"{facility_name}은 {dl}({WEEKDAY_KO[wd]}요일)에는 운영하지 않아요."]
        usual = hours_summary(answer.hours, meal_type=meal_type)
        if usual:
            lines += ["", f"평소 운영시간은 {usual}이에요.{stamp}"]
        return "\n".join(lines)

    # 이 끼니를 아예 안 하는 시설이다. 실제로 여는 끼니를 보여준다.
    lines = [f"{facility_name}은 {meal_ko}은 운영하지 않아요."]
    other = hours_summary(answer.hours)
    if other:
        lines += ["", f"운영시간은 {other}이에요.{stamp}"]
    return "\n".join(lines)


def _render_b(answer: MealAnswer, *, facility_name: str, dl: str, meal_ko: str,
              source_url: str, date: str, meal_type: str) -> dict:
    if answer.reason == "not_offered":
        text = _not_offered_text(answer, facility_name=facility_name, dl=dl,
                                 meal_ko=meal_ko, date=date, meal_type=meal_type)
    else:
        # 원천이 명시한 미운영. note 가 '운영없음' 류면 문장과 중복이라 안 붙인다.
        why = next((r.get("note") for r in answer.rows if r.get("note")), None)
        # 원천 표기의 대괄호는 벗긴다: '[지방선거]' → '지방선거'.
        # 표기 정리일 뿐 내용을 바꾸지 않는다.
        if why:
            why = why.strip().strip("[]")
        detail = "" if (not why or why in CLOSED_MARKER_WORDS) else f" ({why})"
        lines = [f"{facility_name}은 {dl} {meal_ko}에 운영하지 않아요{detail}."]

        # ★ 근거를 보여준다. 물어본 끼니는 닫혔으니 **언제 여는지**를 보여주는 게
        #   학생의 다음 질문이다. 여기에 근거가 빠져 있었다 — 합의한 문안과 달랐다.
        #   운영시간 관측이 없으면(serves=None) 억지로 만들지 않고 관측 시각만 남긴다.
        summary = hours_summary(answer.hours)
        if summary:
            lines += ["", f"운영시간은 {summary}이에요."]
            stamp = observed_label(answer.hours[0]["observed_at"])
            if stamp:
                lines[-1] += f" ({stamp})"
        else:
            lines += ["", f"{observed_label(answer.observed_at)} 기준이에요."]
        text = "\n".join(lines)
    return kakao.response(
        [kakao.simple_text(text)],
        [kakao.quick_reply("다른 끼니", f"{facility_name} 오늘 메뉴"),
         kakao.quick_reply("다른 식당", "학식 어디 열어"),
         kakao.quick_reply("처음으로", "처음으로")],
    )


# ── C-1. 운영은 하는데 메뉴가 아직 없음 ──────────────────────────

def _render_c1(*, facility_name: str, dl: str, meal_ko: str, source_url: str) -> dict:
    text = (f"{facility_name} {dl} {meal_ko} 메뉴가 아직 올라오지 않았어요.\n\n"
            f"운영은 하는 날이니 조금 뒤에 다시 확인해 주세요.\n"
            f"{source_url}")
    return kakao.response(
        [kakao.simple_text(text)],
        [kakao.quick_reply("내일 메뉴", f"{facility_name} 내일 {meal_ko}"),
         kakao.quick_reply("다른 식당", "학식 어디 열어")],
    )


# ── C-2. 확인 불가 ──────────────────────────────────────────────

def _render_c2(answer: MealAnswer, *, facility_name: str, dl: str, meal_ko: str,
               source_url: str, contact: str | None) -> dict:
    why = {
        "stale": "마지막 확인이 오래돼서 지금 자료로 쓰기 어려워요.",
        "no_record": "아직 자료를 가져오지 못했어요.",
        "unknown": "자료가 비어 있어 확인하지 못했어요.",
    }.get(answer.reason, "확인하지 못했어요.")

    lines = [f"{facility_name} {dl} {meal_ko} 식단을 확인하지 못했어요.", why, "",
             "원문에서 직접 확인해 주세요.", source_url]
    if contact:
        lines += ["", f"문의 · {contact}"]
    return kakao.response(
        [kakao.simple_text("\n".join(lines))],
        [kakao.quick_reply("다른 식당", "학식 어디 열어"),
         kakao.quick_reply("처음으로", "처음으로")],
    )


# ── 폴백 ────────────────────────────────────────────────────────

def render_overview(rows, *, date: str, meal_type: str) -> dict:
    """식당을 안 말한 발화 — 운영 중인 곳을 한 장으로.

    rows: [(facility_id, 이름, MealAnswer), ...]

    ★ 폴백으로 보내지 않는다. 자료는 있고 어느 식당인지만 모르는 상태다.
      '모른다'와 '안 물었다'는 다르다.
    """
    meal_ko = MEAL_KO.get(meal_type, meal_type)
    dl = date_label(date)

    items, operating = [], []
    for _fid, name, a in rows:
        if a.branch is Branch.A:
            n = sum(len(r["items"]) for r in a.operating_rows)
            first = next((i["name"] for r in a.operating_rows for i in r["items"]), "")
            items.append({"title": name,
                          "description": f"{first} 외 {n - 1}개" if n > 1 else first})
            operating.append(name)
        elif a.branch is Branch.B:
            items.append({"title": name, "description": "오늘은 운영 안 해요"})
        else:
            items.append({"title": name, "description": "확인 중"})

    if not items:
        return render_fallback()

    header = f"{dl} {meal_ko} 학식"
    card, dropped = kakao.list_card(
        header, items,
        overflow_button=(kakao.web_button(f"전체 {len(items)}곳", "https://coopjbnu.kr/menu/week_menu.php")
                         if len(items) > kakao.MAX_LIST_ITEMS else None))

    qr = [kakao.quick_reply(f"{n} 자세히", f"{n} {meal_ko}") for n in operating[:3]]
    qr.append(kakao.quick_reply("내일 학식", f"내일 {meal_ko}"))
    return kakao.response([card], qr)


def render_upcoming(rows, *, today: str, days: int, source_url: str,
                    observed_at: str | None = None, stale: bool = False) -> dict:
    """다가오는 학사일정 (deadline.upcoming).

    ★ 진행 중인 기간도 보여준다. 9/3에 물으면 9/1~9/7 수강신청 변경 기간은
      이미 시작했지만 **아직 늦지 않았다.** 시작했다고 빼면 놓치게 만든다.
    """
    if stale:
        return kakao.response(
            [kakao.simple_text(
                "학사일정을 확인하지 못했어요.\n"
                "마지막 확인이 오래돼서 지금 자료로 쓰기 어려워요.\n\n"
                f"원문에서 직접 확인해 주세요.\n{source_url}")],
            [kakao.quick_reply("오늘 학식"), kakao.quick_reply("처음으로")])

    if not rows:
        return kakao.response(
            [kakao.simple_text(
                f"앞으로 {days}일 안에 예정된 학사일정이 없어요.\n\n"
                f"전체 일정은 학사일정 페이지에서 볼 수 있어요.\n{source_url}")],
            [kakao.quick_reply("이번 학기 전체", "학사일정 전체"),
             kakao.quick_reply("처음으로")])

    d0 = dt.date.fromisoformat(today)
    items = []
    for r in rows:
        start = dt.date.fromisoformat(r["start_date"])
        end = dt.date.fromisoformat(r["end_date"]) if r["end_date"] else None
        items.append({"title": r["title"],
                      "description": _dday(d0, start, end)})

    overflow = (kakao.web_button(f"전체 {len(items)}건", source_url)
                if len(items) > kakao.MAX_LIST_ITEMS else None)
    card, _ = kakao.list_card(
        f"앞으로 {days}일 학사일정", items,
        buttons=None if overflow else [kakao.web_button("학사일정 보기", source_url)],
        overflow_button=overflow)

    outputs = [card]
    if observed_at:
        outputs.append(kakao.simple_text(f"{observed_label(observed_at)} 기준"))
    return kakao.response(outputs, [
        kakao.quick_reply("이번 달 전체", "이번 달 학사일정"),
        kakao.quick_reply("오늘 학식"),
    ])


def render_calendar_item(result, *, today: str, source_url: str,
                         observed_at: str | None = None,
                         stale: bool = False) -> dict:
    """특정 학사일정 조회 — "수강신청 언제야".

    ★ 답변에 **질문 대상**을 반드시 넣는다.
      답에 질문 대상이 없으면 학생이 "내 질문에 답한 게 맞나"를 판단할 수 없고,
      판단할 수 없는 답은 틀린 답과 구별이 안 된다.
    """
    from skill.calendar_search import Outcome
    label = result.topic.label if result.topic else "학사일정"

    if stale:
        return kakao.response(
            [kakao.simple_text(
                f"{label} 일정을 확인하지 못했어요.\n"
                f"마지막 확인이 오래돼서 지금 자료로 쓰기 어려워요.\n\n"
                f"원문에서 직접 확인해 주세요.\n{source_url}")],
            [kakao.quick_reply("학사일정 전체", "학사일정"),
             kakao.quick_reply("처음으로")])

    if result.outcome is Outcome.NO_DATA:
        # ★ 조회할 자료가 없다. '그 항목이 없다'와 다른 말이다.
        return kakao.response(
            [kakao.simple_text(
                f"{label} 일정을 확인하지 못했어요.\n"
                f"학사일정 자료를 아직 가져오지 못했어요.\n\n"
                f"원문에서 직접 확인해 주세요.\n{source_url}")],
            [kakao.quick_reply("처음으로")])

    if result.outcome is Outcome.NOT_FOUND:
        # ★ 조회는 했다. 그 사실을 밝힌다 — 안 찾아보고 없다고 한 것과 다르다.
        lines = [f"{label} 일정을 학사일정에서 찾지 못했어요.",
                 f"학사일정 {result.searched_total}건을 확인했어요.", "",
                 "원문에서 직접 확인해 주세요.", source_url]
        if result.topic and result.topic.see_also:
            lines.insert(2, f"{result.topic.see_also}에 따로 올라올 수 있어요.")
        return kakao.response(
            [kakao.simple_text("\n".join(lines))],
            [kakao.quick_reply("학사일정 전체", "학사일정"),
             kakao.quick_reply("처음으로")])

    d0 = dt.date.fromisoformat(today)
    ranked = result.entries
    head = ranked[0]
    lines = [f"{label} — {_period_text(head)}이에요.",
             f"· {head['title']}"]
    if len(ranked) > 1:
        lines.append("")
        lines.append("관련 일정도 있어요.")
        for e in ranked[1:4]:
            lines.append(f"· {e['title']} — {_period_text(e)}")
    if result.topic and result.topic.see_also:
        lines += ["", f"※ {result.topic.see_also}에 별도 안내가 있을 수 있어요."]
    if observed_at:
        lines += ["", f"{observed_label(observed_at)} 기준 · {source_url}"]

    return kakao.response(
        [kakao.simple_text("\n".join(lines))],
        [kakao.quick_reply("학사일정 전체", "학사일정"),
         kakao.quick_reply("오늘 학식")])


def _period_text(e: dict) -> str:
    s = dt.date.fromisoformat(e["start_date"])
    if e.get("end_date"):
        t = dt.date.fromisoformat(e["end_date"])
        return f"{s.month}/{s.day}~{t.month}/{t.day}"
    return f"{s.month}/{s.day}"


def _dday(today: dt.date, start: dt.date, end: dt.date | None) -> str:
    """D-day 표기. 진행 중이면 '마감까지'를 보여준다 — 그게 학생이 쓸 정보다."""
    if end and start <= today <= end:
        left = (end - today).days
        return "오늘 마감" if left == 0 else f"진행 중 · {left}일 남음"
    delta = (start - today).days
    when = f"{start.month}/{start.day}"
    if end:
        when += f"~{end.month}/{end.day}"
    if delta == 0:
        return f"오늘 · {when}"
    if delta == 1:
        return f"내일 · {when}"
    return f"D-{delta} · {when}"


def render_fallback() -> dict:
    """못 알아들었을 때. **못 알아들었다는 것을 분명히 알린다.**"""
    return kakao.response(
        [kakao.simple_text(
            "아직 그 질문은 답변할 자료가 준비되지 않았어요.\n\n"
            "지금 확인할 수 있는 건 이런 것들이에요.")],
        [kakao.quick_reply("오늘 학식"), kakao.quick_reply("총학 공지"),
         kakao.quick_reply("열람실 운영시간"), kakao.quick_reply("공약 진행상황")],
    )


# ─────────────────────────────────────────────────────────────────────────
# 안내 페이지 인용
#
# ★ 요약하지 않는다. 찾은 문단을 그대로 옮긴다. 규정 요약은 특히 위험하다.
# ★ 길면 자르되 **잘랐다는 사실을 표시**한다. 조용히 자르면 잘린 조건이
#   없는 조건처럼 읽힌다.
# ★ 경로를 붙인다. 답에 질문 대상이 없으면 학생이 판단할 수 없다.

QUOTE_BUDGET = 700          # 인용 본문에 쓸 최대 글자 (simpleText 1000 안에서)
PAGE_LEVEL_BUDGET = 420     # 페이지로 답할 때 맛보기로 보여줄 글자
SEARCH_HINT = "총학생회에 직접 물어보면 확인해 드릴게요."


def _quote_block(hit) -> tuple[str, bool]:
    """인용문과 '잘렸는가'. 자를 때는 문장 경계에서 자른다.

    ★ 표를 세로로 전치하는 안을 재보고 **안 만들기로 했다**
      '사학과 졸업요건' 이 8칸 표를 한 줄로 이어 줘서 헤더와 값이 멀어졌다.
      전치하면 "적용년도 / 2010년이후입학자" 처럼 짝지어 보일 것 같았다.

      전수로 재니 두 가지가 어긋났다.
        1. 답한 문항의 17%(긴)~28%(짧은) 에 표가 들어가는데 **거의 다 3칸**이다.
           3칸은 지금도 읽힌다 — 전치하면 10줄이 30줄이 되어 오히려 나빠진다.
               등급 | 평점 | 비고(100점 만점 기준)
               A+ | 4.5 | 95 ~ 100
        2. 정작 **문제였던 8칸 표는 칸이 안 맞아 전치가 불가능**하다.
               8칸  적용년도 | 학과 | 교양(42이상) | … | 계
               2칸  전필 | 전선            ← 병합 셀(colspan)
               9칸  2010년이후입학자 | 사학 | …
      필요하면서 가능한 건이 **0건**이다.

      진짜 원인은 렌더링이 아니라 **파서가 병합 셀을 못 편다**는 것이다.
      colspan/rowspan 을 펴서 칸 수를 맞추면 그때 전치가 의미를 갖는다.
      그건 파서 작업이고 여기가 아니다.
    """
    text = (hit.quote_text or hit.text or "").strip()
    if len(text) <= QUOTE_BUDGET:
        return text, False
    cut = text[:QUOTE_BUDGET]
    for sep in ("\n", ". ", "다. ", "요. "):
        i = cut.rfind(sep)
        if i > QUOTE_BUDGET * 0.5:
            cut = cut[:i + len(sep)]
            break
    return cut.rstrip(), True


def _source_line(hit) -> str:
    when = hit.page_modified or observed_label(hit.observed_at)
    page = hit.page_title or "전북대 홈페이지"
    # 어느 학과 문서인지 밝힌다. 205개 사이트가 붙은 뒤로는
    # 페이지 제목만으로 '내 학과 얘기인가' 를 판단할 수 없다.
    site = getattr(hit, "site_name", "")
    where = f"{site} · {page}" if site and site not in page else page
    return f"📄 {where}" + (f" ({when} 기준)" if when else "")


def render_attribute_hint(subject: str, attribute: str, *,
                          example_site: str = "") -> dict:
    """형식 안내 되묻기 — 답이 학생 속성에 달려 있을 때.

    ★ 버튼 되묻기와 구조가 다르다
        휴학     같은 페이지 형제 5개        → 버튼으로 끝난다
        졸업요건  학과 60곳+ 이 저마다 다르다  → 버튼 10개로 안 된다
      버튼이 없어도 되묻기다. 그리고 상태를 안 만든다 —
      학생이 전체 질문을 다시 치므로 지금 경로가 그대로 처리한다.

    ★ 다단계를 따로 설계할 필요가 없다
      '경영학과 졸업요건' → 학과 페이지 → 그 안에서 전공·복수전공이 형제면
      버튼 되묻기가 이어받는다. 형식 안내(속성) → 버튼(문서 갈래)로 저절로 이어진다.

    ★ 여기서 학과 목록을 보여주지 않는다
      60곳 중 5곳만 보여주면 나머지 55곳 학생에게는 틀린 목록이다.
      후보 상한이 만든 숫자를 선택지처럼 내밀면 안 된다.
    """
    # ★ 예시는 **실제로 후보에 오른 학과**를 쓴다. 지어내면 그 이름으로
    #   물었을 때 우리가 못 찾는다 — 학생을 헛걸음시키는 안내가 된다.
    ex = example_site or "간호대학"
    text = (f"'{subject}'{J(subject, '은/는')} {attribute}마다 달라요.\n\n"
            f"'{ex} {subject}'처럼 {attribute}를 붙여서 물어봐 주세요.\n"
            f"그러면 그 {attribute}의 안내를 그대로 보여드릴게요.")
    return kakao.response([kakao.simple_text(text)],
                          [kakao.quick_reply("처음으로")])


def render_chosen(label: str, text: str, *, where: str, page_url: str) -> dict:
    """2턴 — 학생이 고른 것을 준다. **사과하지 않는다.**

    ★ 1턴과 2턴은 다른 문장을 써야 한다
        1턴 (우리가 찍음)  "어느 부분인지 딱 집지는 못했어요"   ← 맞는 말이다
        2턴 (학생이 고름)   "'일반 휴학'에 대한 안내예요"       ← 사과가 들어가면 안 된다
      학생이 방금 골라준 건데 못 집었다고 하면 되묻기를 왜 했는지가 사라진다.
    """
    body, clipped = (text, False)
    if len(body) > QUOTE_BUDGET:
        cut = body[:QUOTE_BUDGET]
        for sep in ("\n", ". ", "다. ", "요. "):
            i = cut.rfind(sep)
            if i > QUOTE_BUDGET * 0.5:
                cut = cut[:i + len(sep)]
                break
        body, clipped = cut.rstrip(), True

    lines = [f"'{label}'에 대한 안내예요.", "", f"📄 {where}", "", body]
    if clipped:
        lines += ["", "(내용이 길어 일부만 옮겼어요. 전체는 아래에서 확인해 주세요)"]
    return kakao.response(
        [kakao.simple_text("\n".join(x for x in lines if x is not None).strip())],
        [kakao.quick_reply("처음으로")],
    )


def render_clarify(subject: str, options: list[str], *, where: str,
                   page_url: str = "") -> dict:
    """되묻기 — 문서가 갈라 놓은 대로 물어본다.

    ★ 선택지를 지어내지 않는다
      전부 그 문서의 최상위 블록 **제목 그대로**다.
      우리가 문장을 안 지어내는 것과 같은 이유로 없는 선택지가 나올 수 없다.

    ★ 버튼이 새 발화를 보낸다 (messageText = 제목 전체)
      상태를 안 들고 있어도 되고, 두 번째 발화엔 한정어가 있어서
      다시 되묻지 않는다. 화면 라벨만 카카오 상한(14자)에 맞춰 줄인다 —
      **보내는 말은 줄이지 않는다.** 줄이면 검색이 달라진다.
    """
    lines = [f"'{subject}'{J(subject, '은/는')} 여러 갈래로 나뉘어 있어요.",
             "어느 쪽인지 골라 주시면 그 부분을 보여드릴게요.", ""]
    if where:
        lines.append(f"📄 {where}")
    replies = [kakao.quick_reply(kakao._clip(o, kakao.MAX_BTN_LABEL_V), o)
               for o in options[:kakao.MAX_QUICK_REPLIES]]
    return kakao.response([kakao.simple_text("\n".join(lines).strip())], replies)


def render_section(result, *, utterance: str = "") -> dict:
    """섹션 검색 결과 → 카카오 응답."""
    from skill.section_search import Outcome

    subject = result.subject or (utterance or "").strip()[:20]

    if result.outcome is Outcome.PERSONAL:
        # ★ 개인 기록은 우리가 볼 수 없다. 비슷한 규정을 인용하면
        #   학생은 자기 성적을 물었는데 학칙을 받게 된다.
        return kakao.response(
            [kakao.simple_text(
                "본인 성적·수강신청·장학금 내역 같은 개인 기록은 확인해 드릴 수 없어요.\n"
                "학교 포털(OASIS)에 로그인해서 보셔야 해요.\n\n"
                "제도나 규정이 궁금하시면 그건 알려드릴 수 있어요.")],
            [kakao.quick_reply("처음으로")])

    if result.outcome is Outcome.NO_QUERY:
        return kakao.response(
            [kakao.simple_text(
                "무엇을 찾아드릴지 잘 모르겠어요.\n"
                "'장학금', '휴학', '복수전공'처럼 단어를 넣어 물어봐 주세요.")],
            [kakao.quick_reply("처음으로")])

    if result.outcome is Outcome.NO_DATA:
        # ★ 조회할 자료가 아직 없다. '그런 내용이 없다'와 다른 말이다.
        return kakao.response(
            [kakao.simple_text(
                f"'{subject}'{J(subject, '을/를')} 찾지 못했어요.\n"
                f"학교 안내 페이지를 아직 가져오지 못했어요.\n\n{SEARCH_HINT}")],
            [kakao.quick_reply("처음으로")])

    if result.outcome is Outcome.NOT_FOUND:
        # ★ 조회는 했다. 몇 개를 봤는지 밝힌다 — 안 찾아보고 없다고 한 것과 다르다.
        return kakao.response(
            [kakao.simple_text(
                f"'{subject}'에 대한 안내를 찾지 못했어요.\n"
                f"모아둔 안내 {result.searched_sections:,}건을 확인했어요.\n\n"
                f"{SEARCH_HINT}")],
            [kakao.quick_reply("처음으로")])

    if result.outcome is Outcome.AMBIGUOUS:
        # ★ 비슷한 후보가 여럿이면 찍지 않는다. 찍는 것은 추론이다.
        # 제목에 **학과 이름**을 둔다. 205개 사이트가 붙은 뒤로는
        # '졸업요건' 이 학과마다 있어서 문서 제목만으로는 고를 수가 없다.
        items = []
        for h in result.hits[:kakao.MAX_LIST_ITEMS]:
            site = getattr(h, "site_name", "") or h.page_title
            items.append({"title": site,
                          "description": h.page_title or
                          (h.quote_path or h.path).split(" > ")[-1],
                          "link": h.page_url})
        multi_site = len({getattr(h, "site_name", "") for h in result.hits}) > 1
        missing = getattr(result, "missing_tokens", [])
        if missing:
            # 질문의 낱말을 못 찾았으면 그 사실을 먼저 말한다.
            # 비슷한 걸 보여주되 답이라고 말하지 않는다.
            miss = " ".join(missing)
            header = f"'{miss}' 관련 안내는 못 찾았어요"
            tail = (f"'{miss}'{J(miss, '이/가')} 들어간 안내는 없었어요. "
                    f"비슷한 것들이에요.\n\n{SEARCH_HINT}")
        else:
            # ★ 조사는 바로 앞말('안내')을 따른다. subject 를 따르게 걸었다가
            #   "'통금' 안내이 여러 곳에" 가 나왔다 — 원래 맞던 걸 깬 것이다.
            header = f"'{subject}' 안내가 여러 곳에 있어요"
            tail = ("학과마다 내용이 달라요. 어느 학과인지 알려주시면 그곳만 찾아드릴게요."
                    if multi_site else "어느 쪽을 찾으시는지 눌러서 확인해 주세요.")
        card, _ = kakao.list_card(header, items)
        return kakao.response([card, kakao.simple_text(tail)],
                              [kakao.quick_reply("처음으로")])

    hit = result.top

    # ★ 섹션을 못 고르겠으면 고르지 않는다 — 한 단계 올라간다.
    #   틀린 문단을 확신 있게 인용하는 것보다 맞는 페이지를 통째로 보여주는 게 낫다.
    #   학생이 스스로 찾을 수 있으니까. '애매하면 고르지 않는다' 의 적용은
    #   침묵만이 아니다.
    if getattr(result, "page_level", False):
        where = f"{hit.site_name} · {hit.page_title}" if hit.site_name else hit.page_title
        head, _ = _quote_block(hit)
        lines = [f"'{subject}'{J(subject, '은/는')} 이 문서에 있어요.",
                 "", f"📄 {where}"]
        via = getattr(result, "via_synonym", "")
        if via:
            lines.append(f"('{via}'라는 이름으로 올라와 있어요)")
        lines += ["", f"[{hit.quote_path or hit.path}]", head[:PAGE_LEVEL_BUDGET]]
        lines += ["", "어느 부분인지 딱 집지는 못했어요. 아래에서 확인해 주세요.",
                  hit.page_url]
        return kakao.response([kakao.simple_text("\n".join(lines))],
                              [kakao.quick_reply("처음으로")])

    quote, clipped = _quote_block(hit)
    lines = [f"[{hit.quote_path or hit.path}]"]
    # ★ 다른 이름으로 찾았으면 그 사실을 밝힌다.
    #   '졸업요건' 을 물었는데 '졸업기준' 문서를 찾았을 수 있다.
    #   같은 것일 수도, 가까운 것일 수도 있다 — 학생이 판단할 수 있게 알려준다.
    #   '경로를 표시한다' 는 원칙의 연장이다.
    via = getattr(result, "via_synonym", "")
    if via:
        lines.append(f"('{via}'라는 이름으로 올라와 있어요)")
    lines += ["", quote]
    if clipped:
        # 자른 사실을 숨기지 않는다. 잘린 조건은 없는 조건처럼 읽힌다.
        lines += ["", "…(뒷부분이 있어요. 아래 링크에서 전문을 확인해 주세요)"]
    lines += ["", _source_line(hit), hit.page_url]

    return kakao.response(
        [kakao.simple_text("\n".join(lines))],
        [kakao.quick_reply("처음으로")])


def render_notices(result, *, utterance: str = "") -> dict:
    """공지 검색 결과.

    ★ 제목·게시일·링크만 보여준다. 본문을 안 읽었으므로 내용을 요약하지 않는다.
      '이런 공지가 있고 여기서 볼 수 있다' 까지가 우리가 아는 전부다.
    """
    from skill.section_search import Outcome
    subject = result.subject if hasattr(result, "subject") else ""
    subject = subject or " ".join(result.query_tokens[:3]) or utterance[:20]

    if result.outcome is Outcome.PERSONAL:
        return render_section(result, utterance=utterance)

    if result.outcome is Outcome.NO_QUERY:
        return kakao.response(
            [kakao.simple_text("어떤 공지를 찾으시는지 단어를 넣어 물어봐 주세요.\n"
                               "예: '장학금 공지', '기숙사 모집'")],
            [kakao.quick_reply("처음으로")])

    if result.outcome is Outcome.NO_DATA:
        return kakao.response(
            [kakao.simple_text(f"'{subject}' 공지를 찾지 못했어요.\n"
                               f"공지 자료를 아직 가져오지 못했어요.\n\n{SEARCH_HINT}")],
            [kakao.quick_reply("처음으로")])

    if result.outcome is Outcome.NOT_FOUND:
        # 조회는 했다. 몇 건을 봤는지 밝힌다.
        return kakao.response(
            [kakao.simple_text(
                f"'{subject}'{J(subject, '이/가')} 제목에 든 공지를 찾지 못했어요.\n"
                f"모아둔 공지 {result.searched_total:,}건을 확인했어요.\n\n"
                f"제목에 없을 뿐 본문에는 있을 수 있어요.\n{SEARCH_HINT}")],
            [kakao.quick_reply("처음으로")])

    items = []
    for h in result.hits[:kakao.MAX_LIST_ITEMS]:
        where = h.site_name or h.board_name or ""
        when = h.published_at or "날짜 없음"
        items.append({"title": h.title,
                      "description": f"{when} · {where}"[:kakao.LIST_ITEM_DESC],
                      "link": h.url})
    card, _ = kakao.list_card(f"'{subject}' 공지", items)
    return kakao.response(
        [card, kakao.simple_text("제목만 보고 찾은 거라 자세한 내용은 눌러서 확인해 주세요.")],
        [kakao.quick_reply("처음으로")])


def render_manual(entry, *, utterance: str = "") -> dict:
    """총학이 직접 확인한 답 (T4).

    ★ 출처를 반드시 밝힌다. 홈페이지 링크가 없으므로
      **어디에 물어 확인했는지**를 대신 적는다.
      검증할 수 없는 답은 믿어달라는 요구다 — 우리는 그걸 하지 않는다.
    """
    lines = [entry.answer.strip()]
    # ★ 없는 제도를 물었을 때는 대안까지 줘야 답이 끝난다.
    #   '없어요' 만 하면 학생은 다른 데서 계속 찾는다.
    if getattr(entry, "alternative", ""):
        lines += ["", entry.alternative.strip()]
    lines.append("")
    who = entry.source or "총학생회 확인"
    lines.append(f"📌 총학생회가 {who}한 내용이에요 ({entry.verified_at} 확인)")
    return kakao.response(
        [kakao.simple_text("\n".join(lines))],
        [kakao.quick_reply("처음으로")])
