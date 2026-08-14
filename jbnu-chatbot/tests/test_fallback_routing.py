"""폴백 블록 → 검색. 도달률이 정확도만큼 중요하다.

배경 (실측)
  오픈빌더 봇테스트에서 '복학 신청' 이 폴백으로 갔고 스킬을 부르지도 않았다.
  info.search 블록의 발화가 6개뿐이었고 거기 '복학' 이 없었다.
  우리가 재는 85%는 **서버에 도달한 질문** 기준이고 도달률은 따로다.
  카카오 NLU 가 못 잡으면 6,985페이지가 있어도 안 쓰인다.

여기서 지키는 것
  1. 폴백으로 온 말도 답한다 (밋밋한 폴백이 아니라 검색 결과·못 찾음 안내)
  2. 폴백 경로에서도 **안전 분기가 먼저다** — 이건 절대 안 밀린다
  3. 인사·잡담은 검색에 안 태운다. 단 '밥 뭐야' 같은 진짜 질문은 통과한다
  4. 총학이 만든 매핑 안 된 블록은 폴백과 다르게 보수적으로 간다
"""

from __future__ import annotations

import pathlib

import pytest

from skill import routing, server, smalltalk
from store import repo


def _pay(utterance: str, block_name: str = "폴백 블록") -> dict:
    return {"userRequest": {"utterance": utterance,
                            "block": {"id": "bid1", "name": block_name}},
            "action": {"params": {}, "detailParams": {}}}


def _text(resp: dict) -> str:
    out = resp["template"]["outputs"][0]
    if "simpleText" in out:
        return out["simpleText"]["text"]
    if "listCard" in out:
        return out["listCard"]["header"]["title"]
    return str(out)


# ── 폴백 인식 ────────────────────────────────────────────────
@pytest.mark.parametrize("name", ["폴백 블록", "폴백블록", "fallback", "Fallback Block"])
def test_폴백_블록을_알아본다(name):
    assert routing.is_fallback(_pay("복학", name))


@pytest.mark.parametrize("name", ["상담신청", "info.search", ""])
def test_폴백이_아닌_블록(name):
    assert not routing.is_fallback(_pay("복학", name))


# ── 갈래 정하기 ──────────────────────────────────────────────
@pytest.mark.parametrize("utterance,expected", [
    ("오늘 학식", "food.menu.today"),
    ("학식 메뉴 뭐야", "food.menu.today"),
    ("오늘 급식", "food.menu.today"),
    # ★ 총학 공지는 T4 로 간다 (2026-08-14). 학교 공지 검색이 아니다 —
    #   총학이 시트에 직접 넣은 것이라 크롤 결과보다 근거가 세다.
    ("총학 공지", "council.notice"),
    ("학사일정 알려줘", "deadline.upcoming"),
    ("복학 신청", None),          # 아무것도 안 걸리면 안내 검색이 기본
    ("자퇴 절차", None),
])
def test_폴백_갈래는_별칭으로_정한다(utterance, expected):
    assert routing.by_utterance(utterance)[0] == expected


def test_가장_긴_별칭이_이긴다():
    # '학식' 과 '학식 메뉴' 가 둘 다 걸린다 → 긴 쪽
    assert routing.by_utterance("학식 메뉴")[1] == "alias:학식 메뉴"


def test_한글자_별칭은_안_쓴다():
    # 한 글자는 아무 문장에나 걸려서 갈래를 망친다
    handler, why = routing.by_utterance("가")
    assert handler is None and why == "no-alias"


# ── 안전이 먼저다 ────────────────────────────────────────────
@pytest.mark.parametrize("block", ["폴백 블록", "info.search", "상담신청", "오늘 학식"])
def test_어느_문으로_들어와도_안전_분기가_먼저다(tmp_path, block):
    """새 입구를 열었다고 안전이 뒤로 밀리면 안 된다.

    폴백을 검색에 연결하면 학생이 아무 말이나 보낼 수 있게 된다 —
    그게 목적이고, 그래서 위험한 말도 들어온다.
    """
    db = tmp_path / "x.db"
    resp = server.handle(db, None, _pay("자살하고 싶어", block))
    assert "109" in _text(resp)


def test_잡담보다_안전이_먼저다(tmp_path):
    # 인사 판정이 안전 분기를 가리면 안 된다
    db = tmp_path / "x.db"
    assert "109" in _text(server.handle(db, None, _pay("안녕 나 죽고싶어")))


# ── 인사·잡담 ────────────────────────────────────────────────
@pytest.mark.parametrize("u,kind", [
    ("안녕하세요", "greeting"), ("안녕", "greeting"), ("hi", "greeting"),
    ("ㅋㅋㅋ", "chitchat"), ("ㅎㅎ", "chitchat"), ("오늘 뭐해", "chitchat"),
    ("고마워", "thanks"), ("감사합니다", "thanks"),
])
def test_잡담을_알아본다(u, kind):
    assert smalltalk.classify(u) == kind


@pytest.mark.parametrize("u", [
    "밥 뭐야",            # ★ 진짜 질문이다. '뭐야' 가 들었다고 먹으면 안 된다
    "학식 뭐야",
    "졸업 요건 뭐야",
    "안녕하세요 휴학 어떻게 해요",   # 인사로 시작해도 질문이 붙으면 질문이다
    "복학 신청",
])
def test_질문은_잡담으로_먹히지_않는다(u):
    assert smalltalk.classify(u) is None


def test_인사에_사용법을_같이_알려준다():
    """인사만 받고 끝내면 학생은 뭘 물어도 되는지 모른 채 나간다."""
    text = _text(smalltalk.response("greeting"))
    assert "휴학" in text and "모른다고" in text


# ── 매핑 안 된 총학 블록은 보수적 ─────────────────────────────
def test_모르는_블록은_확신할_때만_답한다(tmp_path):
    """총학이 만든 상담 블록이 검색 결과를 뱉으면 조용히 엉뚱한 답이 된다.

    폴백과 다르다 — 폴백은 '분류 실패' 이고, 이쪽은 '분류는 됐는데 우리가 모름' 이다.
    """
    db = tmp_path / "x.db"
    c = repo.connect(db)
    repo.init_db(c)
    c.commit()
    c.close()
    resp = server.handle(db, None, _pay("아무 말", "총학이만든새블록"))
    assert _text(resp)  # 폴백 문안이 나가고 죽지 않는다
    names = [b["block_name"] for b in routing.unmapped_blocks()]
    assert "총학이만든새블록" in names   # 이름이 기록돼야 고칠 수 있다
