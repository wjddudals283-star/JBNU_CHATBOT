"""배포 서버 실측 도구 — 판정기와 라우트 엔드포인트.

★ 도구 자체를 테스트한다
  이 도구가 틀리면 우리는 **틀린 그림을 보고** 배포를 결정하게 된다.
  46문항 결과보다 판정 규칙이 먼저 맞아야 한다.

★ 네트워크를 타지 않는다
  여기서 재는 것은 '응답을 어떻게 읽고 어떻게 판정하는가' 다.
  배포본을 두드리는 건 사람이 손으로 돌린다 (토큰이 필요하다).
"""

from __future__ import annotations

import pathlib

import pytest
from fastapi.testclient import TestClient

from skill import kakao, server
from store import repo
from tools import live_probe as lp


# ═══════════════════════════════════════════════════════════════
# 응답에서 학생이 읽는 부분을 꺼낸다
# ═══════════════════════════════════════════════════════════════

def test_카드_종류가_달라도_같은_자리를_본다():
    """simpleText 든 listCard 든 '학생이 받은 글' 은 하나다."""
    r = kakao.response([kakao.simple_text("휴학은 이렇게 해요.\nhttps://x")],
                       [kakao.quick_reply("처음으로")])
    body, labels, source = lp.flatten(r)
    assert "휴학은 이렇게 해요" in body
    assert labels == ["처음으로"]
    assert source == "https://x"

    card, _ = kakao.list_card("후생관 8/14 점심",
                              [{"title": "제육볶음"}, {"title": "된장국"}],
                              buttons=[kakao.web_button("원문", "https://c")])
    body2, _, source2 = lp.flatten(kakao.response([card], []))
    assert "제육볶음" in body2 and "후생관" in body2
    assert source2 == "https://c"


def test_출처_줄은_인용에서_뺀다():
    """★ 자족성은 인용문에 물어야 한다.

    출처 줄까지 넣고 재면 '📄 전북대학교 본부 · 휴학 / 복학' 때문에
    조각도 자족처럼 보인다 — 자를 무디게 만드는 자리다.
    """
    body = ("'복학' 에 대한 안내예요.\n"
            "📄 전북대학교 본부 · 휴학 / 복학\n"
            "1-2\n"
            "(8/11 13:28 확인 기준)\n"
            "https://www.jbnu.ac.kr/x")
    q = lp.quoted_part(body)
    assert "📄" not in q and "http" not in q and "확인 기준" not in q
    assert "1-2" in q


# ═══════════════════════════════════════════════════════════════
# 네 갈래 판정
# ═══════════════════════════════════════════════════════════════

def test_되물음은_못함과_다르다():
    """★ 되묻기는 실패가 아니라 미완의 대화다. 섞어 세면 고칠 데를 못 찾는다."""
    mark, _ = lp.judge("'휴학'은 여러 갈래로 나뉘어 있어요. 어느 쪽인지 골라 주시면",
                       ["일반 휴학", "군입대 휴학"], "휴학", "answer")
    assert mark.startswith("🔁")


def test_모른다고_하면_못함():
    mark, why = lp.judge("안내를 찾지 못했어요.", [], "휴학", "answer")
    assert mark.startswith("❌") and "모른다" in why


def test_기대값이_없으면_못함():
    mark, why = lp.judge("총장은 이렇게 말했습니다. 오늘도 좋은 하루 되세요.",
                         [], "재이수", "answer")
    assert mark.startswith("❌") and "재이수" in why


def test_조각은_반쪽이다():
    """★ 확신 오답이 아니라서 안 잡히던 칸이다. 안전하고, 쓸모가 없다."""
    mark, why = lp.judge("1-2", [], "1-2", "answer")
    assert mark.startswith("⚠️"), why


def test_기대값이_출처에만_있으면_반쪽():
    body = ("'수강신청' 에 대한 안내예요.\n"
            "📄 전북대학교 본부 · 수강신청\n"
            "가. 사회봉사 과목은 학기당 1과목만 신청할 수 있습니다.")
    mark, why = lp.judge(body, [], "수강신청", "answer")
    assert mark.startswith("⚠️") and "출처" in why


def test_값이_인용문_안에_있으면_답함():
    body = ("재이수 과목의 성적은 A0 이하로 하며, 재이수는 C+ 이하 과목만 가능합니다.\n"
            "📄 전북대학교 본부 · 학사운영규정")
    mark, why = lp.judge(body, [], "재이수", "answer")
    assert mark.startswith("✅"), why


def test_보류_문항인데_답하면_표시가_남는다():
    """답하면 안 되는 문항에 값이 나왔다 — 조용히 ✅ 로 넘기지 않는다."""
    body = "등록금 납부 기간은 8월 1일부터 8월 10일까지입니다."
    mark, why = lp.judge(body, [], "등록금", "defer")
    assert mark.startswith("✅") and "사람이 볼 것" in why


def test_표_칸이_파이프로_안_깨진다():
    """제목에 '휴학 / 복학' 같은 게 온다. 파이프가 들어오면 표가 무너진다."""
    assert "|" not in lp._cell("가|나\n다", 40)


# ═══════════════════════════════════════════════════════════════
# /admin/route — 짐작하지 않고 물어본다
# ═══════════════════════════════════════════════════════════════

@pytest.fixture()
def client(tmp_path, monkeypatch):
    p = tmp_path / "r.db"
    c = repo.connect(p)
    repo.init_db(c)
    c.commit()
    c.close()
    monkeypatch.setenv("SKILL_TOKEN", "x" * 24)
    return TestClient(server.create_app(p, with_scheduler=False))


def _pay(u: str) -> dict:
    return {"userRequest": {"utterance": u}, "action": {"params": {}}}


def test_라우트를_물어볼_수_있다(client):
    h = {"X-Skill-Token": "x" * 24}
    assert client.post("/admin/route", json=_pay("죽고싶어"),
                       headers=h).json()["route"] == "safety"
    assert client.post("/admin/route", json=_pay("오늘 학식"),
                       headers=h).json()["route"] == "food.menu.today"
    assert client.post("/admin/route", json=_pay(""),
                       headers=h).json()["route"] == "welcome"


def test_라우트도_토큰이_필요하다(client):
    assert client.post("/admin/route", json=_pay("휴학")).status_code == 401


def test_라우트가_handle_과_같은_길을_말한다(client):
    """★ 재는 것과 도는 것이 다르면 측정이 거짓말을 한다.

    route_of 를 handle() 이 **쓴다**. 복사본이 아니다.
    그래도 갈라질 수 있는 자리라 몇 개를 눌러 확인한다.
    """
    h = {"X-Skill-Token": "x" * 24}
    for u, expect_mark in (("죽고싶어", "109"),
                           ("처음으로", "총학생회 챗봇"),
                           ("", "총학생회 챗봇")):
        r = client.post("/admin/route", json=_pay(u), headers=h).json()
        out = client.post("/skill", json=_pay(u), headers=h).json()
        text = out["template"]["outputs"][0]["simpleText"]["text"]
        assert expect_mark in text, (u, r)


def test_도구가_로컬_DB를_안_연다():
    """★ 열 수 있는 코드를 두면 언젠가 그리로 샌다.

    오늘 로컬(08-11)과 서버 후보가 다른 걸 확인했다.
    로컬로 재면 존재하지 않는 챗봇을 재게 된다.
    """
    src = pathlib.Path(lp.__file__).read_text(encoding="utf-8")
    assert "repo.connect" not in src
    assert "sqlite3" not in src


def test_되읊는_줄을_인용으로_세지_않는다():
    """★ 자가 자기 자신을 재던 자리다 (이 도구를 만들다 걸렸다).

    우리 템플릿은 "'수강신청'에 대한 안내예요." 로 시작한다.
    그 줄에는 학생이 물은 말이 **항상** 들어 있어서,
    '기대값이 인용문 안에 있나' 가 언제나 참이 된다.
    """
    body = ("'수강신청' 에 대한 안내예요.\n"
            "📄 전북대학교 본부 · 수강신청\n"
            "가. 사회봉사 과목은 학기당 1과목만 신청할 수 있습니다.")
    q = lp.quoted_part(body)
    assert "수강신청" not in q
    assert "사회봉사" in q


def test_제목만_있는_줄은_경로다():
    assert lp.quoted_part("[조기시험]\n매학기 출석이 4분의 3에 달한 자가") \
        .startswith("매학기")
    # 내용이 붙은 줄은 남는다 — 학식 세 끼니가 이 모양이다
    assert "식단표에" in lp.quoted_part("[아침] 식단표에 '운영없음' 으로 올라와 있어요")


def test_선택지는_listCard_항목도_센다():
    """★ 도구가 quickReplies 만 세서 '선택지 1개' 로 보고했다 (2026-08-14).

    되묻기 선택지는 quickReplies 로 나갈 때도 있고 listCard 항목으로 나갈 때도 있다.
    한쪽만 세면 2~5개짜리 되묻기가 전부 '1개' 로 찍힌다 —
    그 숫자로 '고를 게 없다' 고 판단할 뻔했다. **자가 틀리면 진단이 틀린다.**
    """
    card, _ = kakao.list_card(
        "'등록금' 안내가 여러 곳에 있어요",
        [{"title": "등록안내", "link": "https://a"},
         {"title": "차등납부", "link": "https://b"}])
    r = kakao.response([card, kakao.simple_text("어느 쪽을 찾으시는지 눌러서 확인해 주세요.")],
                       [kakao.quick_reply("처음으로")])
    body, choices, _src = lp.flatten(r)
    assert len(choices) == 3, choices        # 항목 2 + 처음으로 1
    mark, why = lp.judge(body, choices, "등록금", "answer")
    assert mark.startswith("🔁") and "3" in why


def test_공지는_다른_자로_잰다():
    """★ 약속한 적 없는 걸 못 지켰다고 세고 있었다 (2026-08-14).

    공지 검색은 제목·게시일·링크만 낸다. 본문을 안 읽는 게 설계다.
    안내 인용과 같은 자로 재니 인용부에 "제목만 보고 찾은 거라…" 만 남아
    ⚠️ 반쪽으로 찍혔다. **자가 틀리면 진단이 틀린다** — 오늘 두 번째다.
    """
    # 실제 응답 모양 — 제목은 listCard 에 있어서 인용부에는 안내문만 남는다
    body = ("'모집' 이 제목에 든 공지예요: 2026학년도 근로장학생 모집\n"
            "제목만 보고 찾은 거라 자세한 내용은 눌러서 확인해 주세요.")
    # 안내 인용의 자로 재면 '모집' 이 되읊는 줄에만 있어 반쪽으로 찍힌다
    assert lp.judge(body, [], "모집", "answer")[0].startswith("⚠️"), \
        lp.quoted_part(body)
    # 공지의 자로 재면 제목에 낱말이 있으니 답한 것이다
    assert lp.judge(body, [], "모집", "answer",
                    route="notice.search")[0].startswith("✅")


def test_공지를_못_찾으면_공지에서도_못함이다():
    body = "'교육과정'이 제목에 든 공지를 찾지 못했어요."
    mark, _ = lp.judge(body, [], "교육과정", "answer", route="notice.search")
    assert mark.startswith("❌")
