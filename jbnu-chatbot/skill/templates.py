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

from skill import career
from skill import kakao
from skill.josa import attach as J
from skill.branch import Branch, MealAnswer

MEAL_KO = {"breakfast": "아침", "lunch": "점심", "dinner": "저녁"}

# 학사일정을 넓혀 볼 수 있는 최대 폭. 버튼 문구와 서버의 상한이 **같은 수**여야
# 한다 — 다르면 버튼이 못 지킬 약속을 하게 된다.
MAX_UPCOMING_DAYS = 90


def observed_label(iso: str | None) -> str:
    if not iso:
        return ""
    d = dt.datetime.fromisoformat(iso)
    return f"{d.month}/{d.day} {d.hour:02d}:{d.minute:02d} 확인"


def date_label(date: str) -> str:
    d = dt.date.fromisoformat(date)
    return f"{d.month}/{d.day}"


def _next_day_button(facility_name: str, meal_ko: str, date: str,
                     today: str | None) -> dict:
    """'내일 메뉴' — **오늘을 보고 있을 때만** 그렇게 부른다.

    ★ 이미 내일을 보여주면서 '내일 메뉴' 를 또 붙이고 있었다 (2026-08-14)
      누르면 같은 날짜가 다시 나온다. 자기 자신으로 돌아오는 버튼이다.
      날짜를 모르면(today=None) 상대어를 안 쓴다 — 틀린 이름을 붙이느니 안 붙인다.
    """
    if today and date == today:
        return kakao.quick_reply("내일 메뉴", f"{facility_name} 내일 {meal_ko}")
    return kakao.quick_reply("오늘 메뉴", f"{facility_name} 오늘 {meal_ko}")


def render_meal(answer: MealAnswer, *, facility_name: str, date: str,
                meal_type: str, source_url: str, price_url: str | None = None,
                contact: str | None = None, today: str | None = None,
                source_name: str = "생협 홈페이지",
                has_price_table: bool = False) -> dict:
    """식단 질문 하나 → 카카오 응답. 분기별로 문장이 완전히 다르다."""
    meal_ko = MEAL_KO.get(meal_type, meal_type)
    dl = date_label(date)

    if answer.branch is Branch.A:
        return _render_a(answer, facility_name=facility_name, dl=dl,
                         meal_ko=meal_ko, source_url=source_url,
                         price_url=price_url, has_price_table=has_price_table,
                         date=date, today=today)
    if answer.branch is Branch.B:
        return _render_b(answer, facility_name=facility_name, dl=dl,
                         meal_ko=meal_ko, source_url=source_url,
                         date=date, meal_type=meal_type)
    if answer.branch is Branch.C1:
        return _render_c1(facility_name=facility_name, dl=dl, meal_ko=meal_ko,
                          source_url=source_url, date=date, today=today)
    return _render_c2(answer, facility_name=facility_name, dl=dl,
                      meal_ko=meal_ko, source_url=source_url, contact=contact,
                      source_name=source_name)


# ── A. 사실 있음 + 신선함 ────────────────────────────────────────

def _render_a(answer: MealAnswer, *, facility_name: str, dl: str, meal_ko: str,
              source_url: str, price_url: str | None, date: str,
              today: str | None = None,
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

    qr = [_next_day_button(facility_name, meal_ko, date, today),
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


# 달력 위젯을 알아보는 **관측된 표시** — 요일 머리글이 이어서 나온다.
# ★ 임계값이 아니다. 밀도로 자르면 코퍼스가 바뀔 때 흔들린다.
#   원문이 '이 표는 달력이다' 라고 스스로 말해 주는 자리를 쓴다.
_WEEKDAY_HEAD = ("일", "월", "화", "수", "목", "금", "토")


def is_calendar_widget(text: str) -> bool:
    """요일 머리글이 표 앞머리에 이어서 나오면 달력 격자다.

    ★ '취업 상담' 이 43칸 달력을 인용했다 (2026-08-17)
      학생이 물은 건 상담 안내지 날짜 격자가 아니다.
      우리가 표를 그릴 수 없으면 인용하지 않고 링크로 보낸다 —
      '못 그리겠으면 그리지 마라'.
    """
    if not text or text.count("|") < 6:
        return False
    cells = [c.strip() for c in text.split("|")]
    # 앞쪽 어딘가에서 요일 일곱 개가 연달아 나오는지
    for i in range(min(4, len(cells))):
        if tuple(cells[i:i + 7]) == _WEEKDAY_HEAD:
            return True
    return False


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

def _render_c1(*, facility_name: str, dl: str, meal_ko: str, source_url: str,
               date: str, today: str | None = None) -> dict:
    text = (f"{facility_name} {dl} {meal_ko} 메뉴가 아직 올라오지 않았어요.\n\n"
            f"운영은 하는 날이니 조금 뒤에 다시 확인해 주세요.\n"
            f"{source_url}")
    return kakao.response(
        [kakao.simple_text(text)],
        [_next_day_button(facility_name, meal_ko, date, today),
         kakao.quick_reply("다른 식당", "학식 어디 열어")],
    )


# ── C-2. 확인 불가 ──────────────────────────────────────────────

def stale_notice(observed_at: str | None, *, source_url: str,
                 source_name: str = "원문") -> dict:
    """**낡았다는 사실을 학생에게 말한다.** 사람이 판단하지 않는다.

    ★ '못 긁음' 을 '답' 인 척 내보내고 있었다 (2026-08-14)
      '(8/12 22:27 확인 기준)' 은 학생에게 **최신처럼 읽힌다.**
      우리는 그 뒤로 한 번도 못 가져왔는데 그 사실이 화면에 없었다.
      '없다의 갈래' 중 '못 긁음' 이 정확히 이 자리인데 갈래만 있고 말이 없었다.

    ★ 자동화보다 이게 먼저다
      자동화를 시도하다 실패하면 개강 때 낡은 자료가 최신인 척 나간다.
      낡았다고 말해 두면 자동화가 늦어져도 **틀린 답은 안 나간다.**

    임계는 repo.MAX_STALENESS_HOURS 가 이미 정한다 — 사람이 매번 안 본다.
    """
    when = f"{date_label(observed_at[:10])} 자료예요" if observed_at else "오래된 자료예요"
    return kakao.text_card(
        f"이건 {when}. 그 뒤로 못 가져왔어요.",
        f"최신은 {source_name}에서 확인해 주세요.",
        buttons=[kakao.web_button(f"{source_name} 열기", source_url)])


def _render_c2(answer: MealAnswer, *, facility_name: str, dl: str, meal_ko: str,
               source_url: str, contact: str | None,
               source_name: str = "생협 홈페이지") -> dict:
    if answer.reason == "stale":
        # ★ '확인하지 못했어요' 로 뭉개지 않는다. 가진 게 언제 것인지 말한다.
        return kakao.response(
            [kakao.simple_text(
                f"{facility_name} {dl} {meal_ko} 식단은 지금 자료로 쓰기 어려워요."),
             stale_notice(answer.observed_at, source_url=source_url,
                          source_name=source_name)],
            [kakao.quick_reply("다른 식당", "학식 어디 열어"),
             kakao.quick_reply("처음으로", "처음으로")],
        )

    why = {
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

def render_meal_ask(names: list[str], *, date: str) -> dict:
    """어느 식당인지 되묻는다.

    ★ 시각으로 끼니를 고르지 않는 것과 같은 이유다
      학생이 '학식' 이라고만 했으면 우리가 고를 근거가 없다.
      네 식당의 세 끼니를 한 화면에 쏟으면 읽을 수가 없고,
      하나를 골라 주면 그건 우리가 학생 의도를 추측한 것이다.
      되묻기는 문서가 갈라 놓은 대로 묻는 것이고, 식당은 실제로 갈라져 있다.
    """
    text = (f"{date_label(date)} 학식이에요.\n"
            "어느 식당을 볼까요?")
    return kakao.response(
        [kakao.simple_text(text)],
        [kakao.quick_reply(n, f"{n} 학식") for n in names[:kakao.MAX_QUICK_REPLIES]])


def render_meal_day(name: str, answers: list, *, date: str,
                    source_url: str, source_name: str = "생협 홈페이지") -> dict:
    """한 식당의 **세 끼니 전부**.

    ★ 학생이 '오늘' 을 물었는데 '아침' 만 답하고 있었다
      시각으로 끼니를 하나 고르고 있었다 — 새벽에 물으면 조식만 나갔다.
      그런데 그날 점심에는 세 식당 다 메뉴가 있었다. 있는 답을 안 보여준 것이다.
      시각으로 고르는 건 **우리가 학생 의도를 추측하는 것**이다.

    ★ 끼니마다 근거를 갈라 말한다
      원천이 '운영없음' 이라고 적어 놓은 것과 칸이 비어 있는 것은 다르다.
      전자는 관측된 휴무고 후자는 아직 안 올라온 것이다.
      한 문장으로 뭉개면 없는 사실을 만들어낸다.
    """
    lines = [f"{name} · {date_label(date)}", ""]
    truncated: list[str] = []      # 잘린 끼니 — 갈 길을 열어준다
    stale_at: str | None = None    # 낡은 끼니가 하나라도 있으면 아래에서 말한다
    for meal_type, a in answers:
        ko = MEAL_KO.get(meal_type, meal_type)
        if a.branch is Branch.A:
            names_ = [i["name"] for r in a.operating_rows for i in r["items"]]
            body = " · ".join(names_[:6]) or "메뉴 표기 없음"
            if len(names_) > 6:
                # ★ 자른 사실을 표시하고 **끝내지 않는다.**
                #   후생관 점심은 코너가 11개라 32품목이 온다. 6개만 보이고
                #   나머지를 볼 길이 없으면 그건 우리가 정보를 숨긴 것이다.
                body += f" 외 {len(names_) - 6}개"
                truncated.append(ko)
            lines.append(f"[{ko}] {body}")
        elif a.branch is Branch.B and a.reason == "closed_observed":
            # 원천이 '운영없음' 이라고 적었다 — 관측된 휴무다
            lines.append(f"[{ko}] 식단표에 '운영없음' 으로 올라와 있어요")
        elif a.branch is Branch.B:
            lines.append(f"[{ko}] 이 시간대는 운영하지 않아요")
        elif a.branch is Branch.C1:
            # ★ 운영은 하는데 메뉴가 아직 없다. '휴무' 가 아니다.
            lines.append(f"[{ko}] 운영하는데 메뉴가 아직 안 올라왔어요")
        elif a.reason == "stale":
            # ★ '못 긁음' 을 '확인 못 했음' 으로 뭉개지 않는다.
            #   가진 게 있는데 낡은 것과, 아예 없는 것은 다른 말이다.
            stale_at = stale_at or a.observed_at
            lines.append(f"[{ko}] 지금 자료로 쓰기 어려워요 (아래 참고)")
        else:
            lines.append(f"[{ko}] 아직 확인하지 못했어요")
    stamp = observed_label(next((a.observed_at for _m, a in answers
                                 if a.observed_at), None))
    if stamp:
        lines += ["", f"({stamp} 기준)"]
    lines.append(source_url)
    qr = [kakao.quick_reply(f"{ko} 자세히", f"{name} {ko}") for ko in truncated]
    qr += [kakao.quick_reply("다른 식당", "학식"),
           kakao.quick_reply("처음으로", "처음으로")]
    outputs = [kakao.simple_text("\n".join(lines))]
    if stale_at:
        # ★ 세 끼니가 다 낡아도 한 번만 말한다. 같은 말 세 번은 정보가 아니다.
        outputs.append(stale_notice(stale_at, source_url=source_url,
                                    source_name=source_name))
    return kakao.response(outputs, qr[:kakao.MAX_QUICK_REPLIES])


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
    # ★ 시설명이 없으면 폴백 경로에서 식단으로 안 간다 — '내일 저녁' 이
    #   '저녁' 만 남아 안내 검색으로 샜다. '학식' 을 넣어 갈래를 고정한다.
    qr.append(kakao.quick_reply("내일 학식", f"내일 학식 {meal_ko}"))
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
        # ★ 여기 붙던 '이번 학기 전체' 버튼이 자기 자신으로 돌아왔다 (2026-08-14)
        #   messageText 가 '학사일정 전체' 였는데 '전체' 를 읽는 데가 없어서
        #   같은 14일 조회가 다시 돌고 같은 답과 같은 버튼이 나왔다.
        #   학생은 빠져나갈 수 없다 — '못 찾았어요' 보다 나쁘다.
        #
        #   ① 버튼이 보내는 말을 우리가 **실제로 읽는 말**로 바꾼다 ('90일').
        #   ② 이미 최대로 넓혀 놓고 또 넓히자고 하지 않는다.
        qr = []
        if days < MAX_UPCOMING_DAYS:
            qr.append(kakao.quick_reply(f"앞으로 {MAX_UPCOMING_DAYS}일",
                                        f"학사일정 {MAX_UPCOMING_DAYS}일"))
        qr.append(kakao.quick_reply("처음으로"))
        return kakao.response(
            [kakao.simple_text(
                f"앞으로 {days}일 안에 예정된 학사일정이 없어요.\n\n"
                f"전체 일정은 학사일정 페이지에서 볼 수 있어요.\n{source_url}")],
            qr)

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
    if not ranked:
        # ★ 여기서 터지고 있었다 — 빈 목록에 ranked[0] (2026-08-14)
        #   서버에는 학사일정이 있어서 안 터졌을 뿐이다.
        #   수집이 멈추거나 항목이 다 지나간 날이면 '시험 언제' 가 500 이 된다.
        #   **자료가 있다는 가정이 코드에 박혀 있으면 없는 날 학생이 대신 발견한다.**
        return kakao.response(
            [kakao.simple_text(
                f"{label} 일정을 학사일정에서 찾지 못했어요.\n\n"
                f"원문에서 직접 확인해 주세요.\n{source_url}")],
            [kakao.quick_reply("학사일정 전체", "학사일정"),
             kakao.quick_reply("처음으로")])
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


def _render_unreadable_table(hit, subject: str) -> dict:
    """표라서 그대로 옮기면 못 읽는 자리 — 인용하지 않고 링크로 보낸다.

    ★ '못 그리겠으면 그리지 마라' (2026-08-17)
      학식에서 '운영 안 해요'(단정) 를 '식단표에 운영없음으로 올라와 있어요'(근거)
      로 바꾼 것과 같은 모양이다. 못 읽는 걸 읽는 척 내밀지 않는다.
      학생은 링크로 원문까지 갈 수 있다 — 답에 닿는 길은 열려 있다.
    """
    where = (f"{hit.site_name} · {hit.page_title}" if hit.site_name
             else hit.page_title)
    stamp = stamp_line(where, observed_label(hit.observed_at),
                       page_modified=hit.page_modified or "")
    lines = [f"'{subject}'{J(subject, '은/는')} 이 문서에 있어요.", "",
             stamp, "",
             "그 자리가 날짜 표라서 그대로 옮기면 읽기 어려워요.",
             "아래에서 확인해 주세요.", hit.page_url]
    return kakao.response([kakao.simple_text("\n".join(lines))],
                          [kakao.quick_reply("처음으로")])


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


def stamp_line(where: str, when: str, *, page_modified: str = "") -> str:
    """출처 한 줄. **언제 본 것인지 반드시 붙인다.**

    ★ 이게 없어서 낡은 답을 낡은 줄 모르고 내보냈다
      학교가 OASIS → JUMP 로 갈아탔는데 우리 사본은 그 전 것이었고,
      학생은 그 답이 언제 것인지 알 방법이 없었다.
      우리는 원문이 바뀌는 걸 막을 수 없다. 막을 수 있는 건
      **언제 본 것인지 숨기는 것**이다.

    ★ 우리 관측을 앞에, 학교 표기를 뒤에 — 둘 다 보여준다
      학교의 last_modified 는 2.7% 만 채워져 있고, JUMP 전환 때는
      '2025-01-09' 그대로였다. 그걸 우리 관측처럼 내보내면 안 본 것을 말하는 것이다.
      그렇다고 버릴 것도 아니다. 최근 값이면 학생에게 쓸모가 있고,
      **둘이 어긋나면 그 자체가 정보다** — 학교가 안 갱신했다는 신호다.
    """
    bits = [x for x in (when, f"학교 표기 {page_modified}" if page_modified else "")
            if x]
    return f"📄 {where}" + (f" ({' · '.join(bits)})" if bits else "")


def _source_line(hit) -> str:
    # ★ 학교가 말한 수정일보다 **우리가 본 시각**을 쓴다
    #   OASIS → JUMP 전환 때 학교의 last_modified 는 '2025-01-09' 그대로였다.
    #   페이지는 바뀌었는데 그 값은 안 바뀐다 — 2.7% 만 채워져 있고
    #   채워진 것도 못 믿는다. 우리가 보증할 수 있는 건 '우리가 언제 봤나' 뿐이다.
    #   학교 주장을 우리 관측처럼 내보내면 그건 우리가 안 본 것을 말하는 것이다.
    when = observed_label(hit.observed_at)
    page = hit.page_title or "전북대 홈페이지"
    # 어느 학과 문서인지 밝힌다. 205개 사이트가 붙은 뒤로는
    # 페이지 제목만으로 '내 학과 얘기인가' 를 판단할 수 없다.
    site = getattr(hit, "site_name", "")
    where = f"{site} · {page}" if site and site not in page else page
    return stamp_line(where, when, page_modified=hit.page_modified or "")


HQ_SITE_NAME = "전북대학교 본부"


def render_attribute_hint(subject: str, attribute: str, *,
                          example_site: str = "",
                          candidates: list[tuple[str, str]] | None = None
                          ) -> dict:
    """형식 안내 되묻기 — 답이 학생 속성에 달려 있을 때.

    ★ 버튼 되묻기와 구조가 다르다
        휴학     같은 페이지 형제 5개        → 버튼으로 끝난다
        졸업요건  학과 60곳+ 이 저마다 다르다  → 버튼 10개로 안 된다
      버튼이 없어도 되묻기다. 그리고 상태를 안 만든다 —
      학생이 전체 질문을 다시 치므로 지금 경로가 그대로 처리한다.

    ★ 다단계를 따로 설계할 필요가 없다
      '경영학과 졸업요건' → 학과 페이지 → 그 안에서 전공·복수전공이 형제면
      버튼 되묻기가 이어받는다. 형식 안내(속성) → 버튼(문서 갈래)로 저절로 이어진다.

    ★ 되물어 놓고 대답을 못 받고 있었다 (2026-08-15 실측)
      "어느 학과인지 알려주시면" 이라고 물으면 사람은 **'경제학부'** 라고만 답한다.
      그건 자연스러운 대화다. 그런데 그 한 마디에는 주제가 없어서
      우리는 새 질문으로 처리했고 교육목표·학과앨범이 나갔다.
          되묻기 13건 중 버튼 11건은 이어짐, 형식 안내 2건은 **전부 끊김**
      버튼이 사는 이유는 라벨이 **완전한 문구**('일반 휴학')라 상태가 필요 없어서다.

    ★ 그래서 후보 학과를 버튼으로 준다 — 라벨을 완전한 문구로
      '경제학부 졸업요건' 을 보내므로 지금 경로가 그대로 처리한다. 상태를 안 만든다.

    ★ 다만 **전체 목록이 아니라고 밝힌다**
      60곳 중 후보에 오른 몇 곳일 뿐이다. 그걸 전체처럼 내밀면
      나머지 학생에게는 틀린 목록이 된다. 그래서 문구로 못 박고
      예시도 그대로 남긴다 — 목록에 없는 학생은 직접 치면 된다.
      (학식에서 '운영 안 해요' 를 '식단표에 운영없음으로 올라와 있어요' 로
       바꾼 것과 같은 모양 — 아는 만큼만 말한다)
    """
    # ★ 예시는 **실제로 후보에 오른 학과**를 쓴다. 지어내면 그 이름으로
    #   물었을 때 우리가 못 찾는다 — 학생을 헛걸음시키는 안내가 된다.
    #   본부는 '학과' 가 아니므로 버튼에서 뺀다.
    seen: set[str] = set()
    items: list[dict] = []
    # ★ 가나다순 — 학생이 읽을 기준을 준다.
    #   검색 점수 순서는 학생에게 아무 뜻이 없다. 자기 학과를 눈으로 찾는 목록이다.
    for name, url in sorted(candidates or [], key=lambda c: c[0]):
        if not name or not url or name == HQ_SITE_NAME or name in seen:
            continue
        seen.add(name)
        items.append({"title": name, "description": subject, "link": url})
        if len(items) >= kakao.MAX_LIST_ITEMS:
            break
    ex = example_site or (items[0]["title"] if items else "간호대학")
    lines = [f"'{subject}'{J(subject, '은/는')} {attribute}마다 달라요.", ""]
    if items:
        lines.append(f"안내를 찾은 {attribute}예요. 전체 목록은 아니에요.")
        lines.append(f"여기 없으면 '{ex} {subject}'처럼 직접 물어봐 주세요.")
    else:
        lines.append(f"'{ex} {subject}'처럼 {attribute}를 붙여서 물어봐 주세요.")
        lines.append(f"그러면 그 {attribute}의 안내를 그대로 보여드릴게요.")

    outputs: list[dict] = []
    if items:
        card, _ = kakao.list_card(f"{subject} — {attribute}별 안내", items)
        outputs.append(card)
    outputs.append(kakao.simple_text("\n".join(lines)))
    return kakao.response(outputs, [kakao.quick_reply("처음으로")])


def render_chosen(label: str, text: str, *, where: str, page_url: str,
                  observed: str = "") -> dict:
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

    lines = [f"'{label}'에 대한 안내예요.", "",
             stamp_line(where, observed), "", body]
    if clipped:
        lines += ["", "(내용이 길어 일부만 옮겼어요. 전체는 아래에서 확인해 주세요)"]
    return kakao.response(
        [kakao.simple_text("\n".join(x for x in lines if x is not None).strip())],
        [kakao.quick_reply("처음으로")],
    )


# 첫 화면 버튼. **누르면 반드시 답이 나오는 것만** 넣는다.
#   메뉴는 약속이다. 눌렀는데 '못 찾았어요' 가 나오면 그 뒤로 아무것도 안 누른다.
#   전부 봇테스트로 답을 확인했다 (2026-08-13).
#   '총학 공지' 는 원천이 인스타라 뺐다 — 크롤이 못 닿는다.
#
# ★ '졸업요건' 을 뺐다 — 되묻기(형식 안내)로 가기 때문이다
#   고장은 아니다. 졸업요건은 실제로 학과마다 다르고 그 안내가 맞는 답이다.
#   그런데 **첫 화면**에서 되묻기가 나오면 '뭘 물어도 되묻네' 로 읽힌다.
#   두세 번째 상호작용이면 괜찮지만 첫 번째는 아니다.
#   다섯 개를 채우려고 되묻기를 넣을 이유가 없다.
WELCOME_MENU = ["오늘 학식", "학사일정", "휴학", "자퇴 절차"]


def render_welcome() -> dict:
    """첫 인사. 무엇을 물을 수 있는지 보여준다.

    ★ 인사만 하고 끝내지 않는다
      학생은 대개 뭘 물어도 되는지 몰라서 아무 말이나 던진다.
      버튼은 '이런 걸 물어도 된다' 는 예시이기도 하다.
    """
    text = ("전북대 총학생회 챗봇이에요.\n"
            "학교 정보를 대신 찾아드려요.\n\n"
            "아래 버튼을 누르거나, 궁금한 걸 그냥 물어보세요.")
    return kakao.response(
        [kakao.simple_text(text)],
        [kakao.quick_reply(m, m) for m in WELCOME_MENU])


def render_clarify(subject: str, options: list[str], *, where: str,
                   page_url: str = "", observed: str = "") -> dict:
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
        lines.append(stamp_line(where, observed))
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
        # ★ OASIS 라고 보내고 있었다 (2026-08-14 배포본 실측)
        #   학교는 JUMP 로 갈아탔다. 우리가 이 전환에 당한 게 두 번째다 —
        #   전에는 사본이 낡아서였고(templates.py 위쪽 주석), 이번엔 우리 문안이었다.
        #
        #   ★ 이름만 바꾸지 않고 링크를 준다. 브랜드 이름은 또 바뀐다.
        #   ★ '수강신청은 별개' 는 학교가 직접 쓴 말이다 —
        #     "수강신청사이트와 JUMP는 별개이오니 유의 바람" (수강신청 안내)
        #     뭉뚱그려 'JUMP 에서 보세요' 라고 하면 수강신청은 틀린 안내가 된다.
        #   확인: 총학생회장 (2026-08-14) · 코퍼스 대조 https://jump.jbnu.ac.kr
        return kakao.response(
            [kakao.simple_text(
                "본인 성적·수강신청·장학금 내역 같은 개인 기록은 확인해 드릴 수 없어요.\n"
                "학교 포털 JUMP 에 로그인해서 보셔야 해요.\n"
                "https://jump.jbnu.ac.kr\n\n"
                "※ 수강신청은 JUMP 와 별개 사이트예요.\n\n"
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
        #   ★ 다만 후보가 **한 사이트뿐이면** 학과 이름은 아무것도 안 가른다.
        #     '등록금 납부 기간' 은 네 줄이 전부 '등록금' 이었다.
        #     구별되는 말(등록안내·차등납부·분할납부·등록금반환)은 설명 줄에 있었는데,
        #     눈이 읽는 건 제목 줄이다. 같은 말 네 개는 선택지가 아니다.
        sites = {getattr(h, "site_name", "") or h.page_title
                 for h in result.hits}
        one_site = len(sites) == 1
        items = []
        seen: set[tuple[str, str]] = set()
        for h in result.hits:
            if len(items) >= kakao.MAX_LIST_ITEMS:
                break
            site = getattr(h, "site_name", "") or h.page_title
            doc = h.page_title or (h.quote_path or h.path).split(" > ")[-1]
            title, desc = (doc, site) if one_site else (site, doc)
            # ★ 완전히 같은 줄은 한 번만. clarify.options 에 이미 있던 규칙인데
            #   여기엔 없었다 — '근로장학생' 은 같은 공지 제목이 세 번 나왔다.
            #   원칙이 한 군데만 있으면 다른 데서 조용히 어긋난다.
            key = (title, desc)
            if key in seen:
                continue
            seen.add(key)
            items.append({"title": title, "description": desc,
                          "link": h.page_url})
        # ★ '사이트가 여럿' 과 '학과마다 다르다' 는 다른 말이다
        #   '교내 행사' 후보가 본부·연구소·센터 다섯 곳이었는데
        #   "학과마다 내용이 달라요" 라고 답했다. 학과는 하나도 없었다.
        #   오늘 만든 축(본부 문서가 후보에 있나)을 여기에도 쓴다 —
        #   그 판정이 needs_attribute 다. 축이 하나면 두 곳에서 안 어긋난다.
        dept_dependent = getattr(result, "needs_attribute", "") == "학과"
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
            if len(items) < 2:
                # ★ 겹치는 걸 지우고 나니 하나만 남았다. 그러면 '여러 곳' 이 아니다.
                #   고를 게 없는데 고르라고 하면 학생을 한 번 더 누르게 만들 뿐이다.
                #
                #   ★ 그렇다고 '여기 있어요' 도 아니다 (2026-08-14 되돌림)
                #     하나로 줄었다고 그게 답이 되는 건 아니다. 여기까지 온 것은
                #     검색이 **고르지 못했다**는 뜻이다 (AMBIGUOUS).
                #     '기숙사 통금' 은 후보가 생활관 게시판 목록 하나였는데
                #     내가 문구를 바꾸면서 더 단정적으로 틀리게 만들었다.
                #     찾은 건 보여주되 답이라고는 말하지 않는다.
                header = f"'{subject}'{J(subject, '으로/로')} 찾은 건 이거예요"
                tail = "이게 답인지는 확인이 필요해요. 눌러서 원문을 봐 주세요."
            else:
                header = f"'{subject}' 안내가 여러 곳에 있어요"
                tail = ("학과마다 내용이 달라요. 어느 학과인지 알려주시면 그곳만 찾아드릴게요."
                        if dept_dependent
                        else "어느 쪽을 찾으시는지 눌러서 확인해 주세요.")
        card, _ = kakao.list_card(header, items)
        return kakao.response([card, kakao.simple_text(tail)],
                              [kakao.quick_reply("처음으로")])

    hit = result.top

    # ★ 섹션을 못 고르겠으면 고르지 않는다 — 한 단계 올라간다.
    #   틀린 문단을 확신 있게 인용하는 것보다 맞는 페이지를 통째로 보여주는 게 낫다.
    #   학생이 스스로 찾을 수 있으니까. '애매하면 고르지 않는다' 의 적용은
    #   침묵만이 아니다.
    # ★ 못 읽는 표는 인용하지 않고 링크만 준다 — **두 경로가 같은 규칙을 쓴다**
    #   처음엔 아래(섹션 인용) 한 곳에만 붙였는데, '취업 상담' 은 page_level 로
    #   가서 그대로 달력이 나왔다. 한 곳만 고치면 다른 문으로 들어온 쪽이 또 터진다.
    _q, _c = _quote_block(hit)
    if is_calendar_widget(_q):
        return _render_unreadable_table(hit, subject)

    if getattr(result, "page_level", False):
        where = f"{hit.site_name} · {hit.page_title}" if hit.site_name else hit.page_title
        head, _ = _quote_block(hit)
        # ★ 페이지 단위 답에도 관측 시각을 붙인다.
        #   여기가 19/46 이 지나가는 길인데 여태 시각이 없었다 —
        #   OASIS → JUMP 처럼 원문이 바뀌면 학생이 알 방법이 없다.
        lines = [f"'{subject}'{J(subject, '은/는')} 이 문서에 있어요.",
                 "", stamp_line(where, observed_label(hit.observed_at),
                                page_modified=hit.page_modified or "")]
        via = getattr(result, "via_synonym", "")
        if via:
            lines.append(f"('{via}'라는 이름으로 올라와 있어요)")
        lines += ["", f"[{hit.quote_path or hit.path}]", head[:PAGE_LEVEL_BUDGET]]
        lines += ["", "어느 부분인지 딱 집지는 못했어요. 아래에서 확인해 주세요.",
                  hit.page_url]
        return kakao.response([kakao.simple_text("\n".join(lines))],
                              [kakao.quick_reply("처음으로")])

    quote, clipped = _quote_block(hit)

    # ★ 못 읽는 표는 인용하지 않고 링크만 준다 (2026-08-17)
    #   '취업 상담' 이 43칸 **달력 위젯**을 인용했다 —
    #       일 | 월 | 화 | 수 | 목 | 금 | 토 | | | | | | 1 2 | 3 | 4 …
    #   학생이 물은 건 상담 안내지 날짜 격자가 아니다.
    #
    #   ★ 밀도(21.9%)로 자르지 않는다. 그건 임계값이고 코퍼스가 바뀌면 흔들린다.
    #     요일 머리글은 **관측된 표시**다 — 그 표가 달력이라고 원문이 말해 준다.
    #     (학식에서 '운영없음' 을 근거로 쓴 것과 같은 모양)
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


# ═══════════════════════════════════════════════════════════════
# 총학 공지·행사 (T4 — 총학이 시트에 직접 넣는다)
# ═══════════════════════════════════════════════════════════════

COUNCIL_INSTAGRAM = "https://www.instagram.com/jbnu_ch/"
COUNCIL_BODY_BUDGET = 700       # 카카오 simpleText 상한 안쪽


def render_council_missing(subject: str, *,
                           instagram: str = COUNCIL_INSTAGRAM) -> dict:
    """시트는 읽었는데 **그 글이 없다.**

    ★ '못 가져왔다' 와 '그건 없다' 는 다른 사실이다
      시트가 비어 있으면 우리 사정이고, 시트에 글이 있는데 그게 없으면
      총학이 아직 안 올렸거나 다른 이름으로 올린 것이다.
      둘을 같은 문장으로 내면 학생이 어디를 봐야 할지 못 정한다.

    ★ 최근 글로 채우지 않는다
      '장학금 공지' 를 물었는데 '댄스제 모집' 을 보여주면
      그건 총학이 장학금 공지를 낸 것처럼 읽힌다.
      학교 공지로 대체하지 않는 것과 **같은 이유**다.
    """
    return kakao.response(
        [kakao.text_card(
            f"'{subject}' 관련 총학 공지는 아직 못 찾았어요.",
            "총학 인스타에 올라와 있을 수 있어요.",
            buttons=[kakao.web_button("총학 인스타 열기", instagram)])],
        [kakao.quick_reply("총학 공지 전체", "총학 공지"),
         kakao.quick_reply("처음으로")])


def render_council_none_active(label: str = "총학 공지", *,
                               instagram: str = COUNCIL_INSTAGRAM) -> dict:
    """시트는 최신인데 **진행 중인 게 없다.**

    ★ '못 가져왔다' 라고 하면 틀린 말이다
      가져왔다. 다만 올라온 글의 마감이 전부 지났다.
      우리 사정(못 읽음)과 학교 사정(지금은 없음)을 섞으면
      학생이 어디를 봐야 할지 못 정한다 — 갈래를 늘리는 이유가 그것이다.

      못 가져왔다   시트를 못 읽었다              → 인스타를 보세요
      진행 중 없음   읽었는데 마감이 다 지났다       → 새로 올라오면 여기 나와요
    """
    return kakao.response(
        [kakao.text_card(
            f"지금 진행 중인 {label}가 없어요.",
            "새로 올라오면 여기서 바로 보여드릴게요.",
            buttons=[kakao.web_button("총학 인스타 열기", instagram)])],
        [kakao.quick_reply("처음으로")])


def render_council_empty(*, stale: bool = False,
                         instagram: str = COUNCIL_INSTAGRAM) -> dict:
    """총학 공지가 후보에 하나도 없을 때.

    ★ '없다' 고 단정하지 않는다. **'우리가 못 가져왔다'** 고 말한다
      학식 stale 문안과 같은 구조다. 없다의 갈래 중 '못 긁음' 이 이 자리다.
      진짜로 공지가 없는 건지, 시트를 못 읽은 건지 우리는 구별 못 한다 —
      구별 못 하는 걸 구별한 척하면 그게 지어내기다.

    ★ 갈 길을 연다
      총학 공지의 원본은 인스타다. 우리가 못 가져왔으면 거기로 보낸다.
    """
    lines = ["최근 총학 공지를 아직 못 가져왔어요.",
             "총학 인스타를 확인해 주세요."]
    if stale:
        # 시트는 읽었는데 오래됐다 — 그 사실까지 밝힌다
        lines.insert(1, "시트를 마지막으로 읽은 지 오래됐어요.")
    return kakao.response(
        [kakao.text_card("\n".join(lines), "",
                         buttons=[kakao.web_button("총학 인스타 열기", instagram)])],
        [kakao.quick_reply("처음으로")])


# 제목을 감싸는 괄호들. 인스타 캡션이 어느 걸 쓸지 우리가 못 정한다.
_TITLE_WRAP = "[]【】〔〕<>《》「」(){}"

# 제목과 본문 사이에 오는 구분자.
# ★ 목록이 좁으면 한 글자가 남는다 (2026-08-14)
#   '-' 만 넣어 뒀는데 실제 캡션이 '[제목] / 모집 기간: …' 이었다.
#   **한 줄짜리 캡션이 처음 들어온 자리**이기도 하다 — 그전 표본은
#   제목이 늘 별도 줄이라 줄바꿈이 구분자 노릇을 했다.
#   어느 기호를 쓸지 총학이 정하는 것이므로 넓게 잡고, 반복(`//`·`--`)과
#   앞뒤 공백까지 함께 걷어낸다.
_TITLE_SEP = " \t\r\n/｜|·ㆍ–—-:∙~"


def _norm_title(s: str) -> str:
    """제목 비교용 — 공백과 괄호를 지운다.

    ★ 같은 제목인데 모양이 다를 수 있다
      대괄호 유무 · 공백 개수 · 줄바꿈. 그 차이로 '다른 제목' 이라고 보면
      접어야 할 자리를 못 접는다.
    """
    return "".join(ch for ch in (s or "")
                   if not ch.isspace() and ch not in _TITLE_WRAP)


def strip_leading_title(body: str, title: str) -> str:
    """본문이 제목으로 시작하면 **그 부분만** 떼어낸다.

    ★ 캡션은 안 고친다 — 화면에서만 접는다 (2026-08-14)
      인스타 캡션이 '[제목]' 으로 시작하는 경우가 많아서
      말풍선 제목과 본문 첫 줄이 똑같이 두 번 찍혔다.
      캡션 자체를 고칠 수는 없다 — 학생이 인스타로 넘어갔을 때
      **같은 글이어야** 하기 때문이다 (자족성 원칙).
      그래서 저장은 원문 그대로 두고, 보여줄 때만 겹치는 앞부분을 뗀다.

    ★ 겹칠 때만, 앞에서만 뗀다
      본문 가운데에 제목이 또 나오는 건 원문이 그런 것이므로 안 건드린다.
    """
    body = (body or "").strip()
    nt = _norm_title(title)
    if not nt or not _norm_title(body).startswith(nt):
        return body
    # 정규화 기준으로 제목만큼 소비한 지점을 원문에서 찾는다
    seen, cut = 0, 0
    for i, ch in enumerate(body):
        if seen >= len(nt):
            cut = i
            break
        if not ch.isspace() and ch not in _TITLE_WRAP:
            seen += 1
        cut = i + 1
    rest = body[cut:]
    # 제목 뒤에 남은 닫는 괄호·구분선·빈 줄을 걷어낸다
    # ★ lstrip 은 집합에 든 글자를 **반복해서** 뗀다 — '//' · ' / ' 도 함께 처리된다.
    return rest.lstrip("]】〕>》」)}").lstrip(_TITLE_SEP).strip()


def _council_lines(p: dict) -> list[str]:
    """공지 한 건 → 줄들. ★ 요약하지 않는다.

    캡션은 이미 자족적이다 (8/14 판정기로 확인 — 날짜·대상·방법·금액·마감시각).
    우리가 줄이면 그 값들이 사라진다. 길면 자르되 **자른 사실을 표시**한다.
    """
    out = [f"[{p['title']}]"]
    body = strip_leading_title(p.get("body") or "", p.get("title") or "")
    if body:
        if len(body) > COUNCIL_BODY_BUDGET:
            body = body[:COUNCIL_BODY_BUDGET].rstrip()
            out += ["", body,
                    "…(뒷부분이 있어요. 아래 링크에서 전문을 확인해 주세요)"]
        else:
            out += ["", body]
    tail = []
    if p.get("deadline"):
        tail.append(f"마감 {date_label(p['deadline'])}")
    if p.get("bureau"):
        tail.append(p["bureau"])
    tail.append(f"{date_label(p['published_at'])} 게시")
    out += ["", " · ".join(tail)]
    if p.get("link"):
        out.append(p["link"])
    return out


def render_council(posts: list[dict], *, utterance: str = "",
                   label: str = "총학 공지",
                   instagram: str = COUNCIL_INSTAGRAM) -> dict:
    """총학 공지 답변.

    ★ 출처를 '총학생회' 라고 밝힌다
      크롤 인용은 '학교가 이렇게 적어 뒀다' 이고, 이건 '총학이 직접 넣었다' 이다.
      학생이 두 개를 같은 무게로 읽으면 안 된다 — T4 가 더 무겁다.

    ★ label 은 **거른 이름**이다
      '교내 행사' 로 물었는데 아래 목록이 '다른 총학 공지' 면
      학생은 거기서 다른 분류가 섞였다고 읽는다. 거른 대로 부른다.
    """
    if not posts:
        return render_council_empty(instagram=instagram)

    lines = _council_lines(posts[0])
    lines += ["", "총학생회가 직접 올린 공지예요."]

    qr = [kakao.quick_reply("처음으로")]
    outputs: list[dict] = [kakao.simple_text("\n".join(lines))]

    if len(posts) > 1:
        items = [{"title": p["title"][:kakao.MAX_TITLE]
                  if hasattr(kakao, "MAX_TITLE") else p["title"],
                  "description": (f"마감 {date_label(p['deadline'])}"
                                  if p.get("deadline")
                                  else f"{date_label(p['published_at'])} 게시"),
                  **({"link": p["link"]} if p.get("link") else {})}
                 for p in posts[1:]]
        card, _ = kakao.list_card(f"다른 {label}", items)
        outputs.append(card)
    return kakao.response(outputs, qr)


# ═══════════════════════════════════════════════════════════════
# 취업·비교과 — "지금 신청할 수 있는 것" 의 목록
# ═══════════════════════════════════════════════════════════════
# ★ 대표 정의 (2026-08-14, 그대로 옮김)
#   "여기서 말한 취업은 채용도 말하는거지만 대부분 취업에 도움되는 활동들"
#   특강·캠프·멘토링·자격증·인턴십·공모전·어학·현장실습·설명회·박람회 + 채용 일부
#
# ★ 전화번호 표가 나가고 있었다
#   career.jbnu.ac.kr 의 **페이지**만 우리에게 있어서다.
#   그 사이트 게시판은 43개 중 25개가 로그인 뒤에 있다 (전수 확인) —
#   안 긁은 게 아니라 못 긁는다. 그건 우회할 선이 아니다.
#   대신 학과·본부 게시판에 891건이 있다. 그걸 쓴다.

CAREER_RECENT_DAYS = 30


def render_career(notices: list, council: list, *, days: int = CAREER_RECENT_DAYS,
                  instagram: str = COUNCIL_INSTAGRAM, excluded: int = 0) -> dict:
    """최근 취업·비교과 공지 목록.

    ★ '지금 신청 가능' 이라고 부르지 않는다 — **우리는 마감을 모른다**
      학교 공지 자료에는 게시일만 있다. 마감은 제목·본문에 글로 적혀 있고
      우리는 본문을 안 읽는다. 게시일 30일을 '신청 가능' 이라고 부르면
      모르는 걸 아는 척하는 것이다.
      학식 stale · 총학 '못 가져왔어요' 와 같은 자리다.

    ★ 총학 시트 글은 마감을 안다 — 그것만 마감을 적는다.
      아는 것과 모르는 것을 한 줄에 섞지 않는다.
    """
    if not notices and not council:
        return kakao.response(
            [kakao.text_card(
                f"최근 {days}일 안에 올라온 취업·비교과 공지를 못 찾았어요.",
                "총학 인스타나 학교 공지에서 확인해 주세요.",
                buttons=[kakao.web_button("총학 인스타 열기", instagram)])],
            [kakao.quick_reply("처음으로")])

    items = []
    for p in council:            # ★ 총학이 직접 넣은 것이 먼저다 (T4)
        desc = (f"마감 {date_label(p['deadline'])} · 총학생회"
                if p.get("deadline") else "총학생회")
        item = {"title": p["title"], "description": desc}
        if p.get("link"):
            item["link"] = p["link"]
        items.append(item)
    # ★ 활동이 먼저다 (2026-08-18 실측 · 대표 정의)
    #   최근 30일 123건 중 학생이 지원할 수 있는 활동은 46건이고
    #   나머지는 직원 채용이다. 대표가 "대부분 취업에 도움되는 활동들" 이라 했다.
    #   버리지는 않는다 — **순서를 준다.** 정렬은 career.sort_and_filter 가 한다.
    #
    # ★ 화면에 '활동/채용' 을 적는다
    #   목록에 학생이 읽을 기준이 없으면 그냥 긴 제목 열 줄이다.
    #   우리가 매긴 딱지가 아니라 제목에 적힌 낱말로 가른 것이라 말할 수 있다.
    for n in notices:
        d = (n.get("published_at") or "")[:10]
        kind = "활동" if career.is_activity(n.get("title") or "") else "채용"
        bits = [x for x in (f"{date_label(d)} 게시" if d else
                            (n.get("board_name") or ""), kind) if x]
        item = {"title": n["title"], "description": " · ".join(bits)}
        if n.get("url"):
            item["link"] = n["url"]
        items.append(item)

    card, dropped = kakao.list_card(
        f"최근 {days}일 취업·비교과 공지", items[:kakao.MAX_LIST_ITEMS])
    tail = [f"최근 {days}일 안에 올라온 취업 관련 공지예요.",
            "마감은 각 공지에서 확인해 주세요."]
    # ★ 뺀 것을 학생에게도 한 줄로 말한다 (조용히 줄이지 않는다)
    #   제목에 적힌 마감이 지난 것만 뺐다 — 제목에 마감이 없으면 안 뺐다.
    if excluded:
        tail.insert(1, f"(마감이 지난 것 {excluded}건은 뺐어요)")
    if dropped:
        tail.insert(1, f"({len(items)}건 중 {len(items) - dropped}건만 보여드려요)")
    return kakao.response(
        [card, kakao.simple_text("\n".join(tail))],
        [kakao.quick_reply("총학 공지", "총학 공지"),
         kakao.quick_reply("처음으로")])


def render_central(topic, subject: str) -> dict:
    """학과 되묻기가 막다른 길인 주제 — 중앙 문서로 보낸다.

    ★ 되묻지 않는다. 물어봐야 학생이 답할 수 없는 질문이기 때문이다.
      '연계전공' 은 학과 소속이 아니라서 뭘 붙여도 못 닿는다.

    ★ 문서에 무엇이 있는지 말해 준다 — **우리가 본 것만.**
      "이 표에 있어요" 만 하면 학생은 눌러야 알 수 있다.
      우리가 본 것(주관학과·이수학점·적용연도)을 적으면 누를지 정할 수 있다.
    """
    lines = [f"'{subject}'{J(subject, '은/는')} 학과별로 나뉘어 있지 않아요.", ""]
    if topic.holds:
        lines.append(f"{topic.label} 안내에 "
                     f"{topic.holds}{J(topic.holds, '이/가')} 표로 있어요.")
    else:
        lines.append(f"{topic.label} 안내에 있어요.")
    lines += ["", "표라서 그대로 옮기면 읽기 어려워요. 아래에서 확인해 주세요.",
              topic.url]
    return kakao.response([kakao.simple_text("\n".join(lines))],
                          [kakao.quick_reply("처음으로")])
