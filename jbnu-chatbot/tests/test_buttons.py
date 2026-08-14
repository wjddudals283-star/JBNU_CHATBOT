"""우리가 붙인 버튼이 막다른 길로 가지 않는지 — 커밋마다 돈다.

★ 왜 테스트로 넣나 (2026-08-13)
  하루에 세 번 같은 모양이 나왔다.
      처음으로 버튼   모든 답변에 붙는데 아무도 안 눌러봄
      식단 버튼 5개   첫 기능인데 버튼이 거의 다 검색으로 샘
      나열형 라벨     길이 상한을 풀어서 살렸는데 살린 게 못 쓰는 것
  전부 '부수적' 이라고 여긴 것들이다. 답변 본문은 하루 종일 봤는데
  버튼은 한 번도 안 봤다.

  ★ 우리는 만든 것은 테스트하는데, 항상 거기 있던 것은 테스트 안 한다.
  그래서 사람이 기억하는 대신 **커밋마다 도는 자리**에 놓는다.
  (전수 확인은 tools/button_probe.py — 실제 DB 로 눌러 본다.
   여기는 데이터 없이도 도는 라우팅 불변식만 지킨다)

★ 나열형 라벨은 **우리 수정이 만든 문제**였다
  라벨 길이 상한을 24 → 48 로 풀어서 '일반복학, 임신·출산·육아 복학, …' 이
  살아났는데, 살아난 게 눌러도 못 찾는 것이었다.
  한 수정이 다음 문제를 드러내는 구조고, 도구가 없었으면 못 봤다.
"""

from __future__ import annotations

import pytest

from skill import clarify, routing, templates

FACILITIES = ["후생관", "진수원", "의대식당", "생활관 식당"]
MEALS = ["아침", "점심", "저녁"]


@pytest.mark.parametrize("f", FACILITIES)
@pytest.mark.parametrize("m", MEALS)
def test_식단_버튼은_식단으로_간다(f, m):
    """★ 6개 중 5개가 안내 검색으로 새고 있었다.

    식단 블록으로 들어오면 되는데 **버튼은 폴백으로 돌아온다.**
    """
    for text in (f"{f} 내일 {m}", f"{f} 몇 시까지 해", f"{f} 가격",
                 f"{f} 오늘 메뉴"):
        assert routing.by_utterance(text)[0] == "food.menu.today", text


def test_시설명이_없는_식단_버튼도_식단으로_간다():
    """'내일 저녁' 은 '저녁' 만 남아 검색으로 샜다."""
    for m in MEALS:
        assert routing.by_utterance(f"내일 학식 {m}")[0] == "food.menu.today"
    assert routing.by_utterance("학식 어디 열어")[0] == "food.menu.today"


def test_기숙사는_식단_별칭이_아니다():
    """★ 시설 이름 중 **음식 말고 다른 뜻이 없는 것**만 넣는다.

    '기숙사'·'생활관' 을 넣으면 '기숙사 통금' 이 식단으로 가 버린다.
    """
    assert routing.by_utterance("기숙사 통금")[0] != "food.menu.today"


def test_학사일정_버튼은_일정으로_간다():
    for text in ("학사일정", "학사일정 전체", "이번 달 학사일정"):
        assert routing.by_utterance(text)[0] == "deadline.upcoming", text


def test_처음으로는_웰컴으로_간다():
    """★ 모든 답변에 붙는 버튼이다. 검색으로 가면
    "'처음'은 학과마다 달라요" 가 나온다 — 실제로 그랬다."""
    for text in ("처음으로", "시작하기"):
        assert routing.by_utterance(text)[0] == "welcome", text


def test_웰컴_메뉴는_되묻기로_안_간다():
    """★ 웰컴 메뉴의 약속은 '누르면 답이 나온다' 이다.

    첫 화면에서 되묻기가 나오면 '뭘 물어도 되묻네' 로 읽힌다.
    '졸업요건' 을 뺀 이유다 — 고장이 아니라 자리가 아니었다.
    """
    assert "졸업요건" not in templates.WELCOME_MENU
    qr = templates.render_welcome()["template"]["quickReplies"]
    assert len(qr) == len(templates.WELCOME_MENU)
    # 보내는 말과 화면 라벨이 같아야 한다 — 줄이면 검색이 달라진다
    assert all(q["label"] == q["messageText"] for q in qr)


def test_나열형_라벨은_쪼갠다():
    """★ 우리 수정이 만든 문제였다.

    라벨 길이 상한을 24 → 48 로 풀어서 이게 살아났는데,
    살아난 게 눌러도 못 찾는 것이었다. 쪼개는 근거는 **원문의 쉼표**다.
    """
    label = "일반복학, 임신·출산·육아 복학, 창업복학, 질병복학(학부생)"
    assert clarify._split_listed(label) == [
        "일반복학", "임신·출산·육아 복학", "창업복학", "질병복학(학부생)"]
    # 쉼표가 없으면 그대로 둔다
    assert clarify._split_listed("일반 휴학") == ["일반 휴학"]
    # 쪼갠 조각이 너무 짧으면 원래대로 (쪼개서 못 쓰게 만들지 않는다)
    assert clarify._split_listed("가, 나") == ["가, 나"]


def test_사이트가_여럿인_것과_학과마다_다른_것은_다르다():
    """★ '교내 행사' 후보가 본부·연구소·센터 다섯 곳이었는데
    "학과마다 내용이 달라요" 라고 답했다. 학과는 하나도 없었다.

    사이트가 여럿 = 여러 곳에 흩어짐.
    학과마다 다름 = 본부에 공통 문서가 없음 (오늘 만든 축).
    두 개를 같은 말로 답하면 답이 있는데도 되묻는 것처럼 보인다.
    """
    from skill import section_search as ss, templates

    class _Hit:
        site_name = "전북대학교 본부"
        page_title = "교내공지"
        page_url = "https://x"
        quote_path = path = "교내공지"

    r = ss.SearchResult(ss.Outcome.AMBIGUOUS, query_tokens=["행사"])
    r.hits = [_Hit(), _Hit()]
    out = templates.render_section(r, utterance="교내 행사")
    body = " ".join(o.get("simpleText", {}).get("text", "")
                    for o in out["template"]["outputs"])
    assert "학과마다" not in body, body


# ═══════════════════════════════════════════════════════════════
# 별칭이 낱말 안쪽에 걸리면 안 된다
# ═══════════════════════════════════════════════════════════════

def test_별칭이_낱말_안쪽에_걸리지_않는다():
    """★ 배포본 실측에서 확신 답변이 '모른다' 로 후퇴했다 (2026-08-14).

    '컴퓨터인공지능학부 교육과정' 이 공지 검색으로 갔다.
    인**공지**능 안에 '공지' 가 들어 있었다.
    한글은 낱말 사이에 공백이 없어서 부분문자열이 그대로 덫이 된다 —
    '학자금 대출' 이 사이트 별칭에 걸렸던 것과 같은 종류다.
    """
    assert routing.by_utterance("컴퓨터인공지능학부 교육과정")[0] is None
    assert routing.by_utterance("인공지능 대학원")[0] is None
    # 진짜 공지는 그대로 간다
    assert routing.by_utterance("수강신청 공지")[0] == "notice.search"
    assert routing.by_utterance("학사공지")[0] == "notice.search"


def test_경계를_보려면_공백을_남겨야_한다():
    """★ _norm 으로 자른 뒤에 경계를 찾다가 멀쩡한 것을 깼다.

    '생활관 학식 조식' 의 '학식' 이 관/조 사이에 낀 것처럼 보였다.
    필요한 정보를 먼저 버리고 나서 찾은 것이다 — 고치다 만든 문제다.
    """
    for m in ("조식", "중식", "석식"):
        assert routing.by_utterance(f"생활관 학식 {m}")[0] == "food.menu.today"
    # 붙여 쓰면 경계가 없다. 없는 걸 지어내지 않고 예전처럼 센다.
    assert routing.by_utterance("오늘학식")[0] == "food.menu.today"
