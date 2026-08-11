"""조사를 받침에 맞춘다.

    '휴학' 는 이 문서에 있어요   →   '휴학'은 이 문서에 있어요

질문의 말을 그대로 문장에 끼워 넣는데 그 말이 무엇일지 미리 알 수 없어서
'는' 하나로 통일해 두었었다. 학생이 보는 문장이 매번 어색했다.
"""

from __future__ import annotations

import pytest

from skill import josa


@pytest.mark.parametrize("word,expect", [
    ("휴학", "은"), ("복학", "은"), ("장학금", "은"), ("증명서 발급", "은"),
    ("수강신청", "은"),          # 청 — ㅇ 받침이다
    ("기숙사", "는"), ("공지", "는"), ("학교", "는"),
])
def test_받침에_맞춘다(word, expect):
    assert josa.attach(word, "은/는") == expect


def test_따옴표_뒤에서도_앞말을_본다():
    """'휴학' 뒤의 조사는 따옴표가 아니라 **휴학**의 받침을 따른다."""
    assert josa.attach("'휴학'", "은/는") == "은"
    assert josa.attach("「기숙사」", "은/는") == "는"


@pytest.mark.parametrize("word,expect", [
    ("A+", "는"),      # 에이플러스
    ("GPA", "는"),     # 지피에이
    ("OASIS", "는"),   # 오아시스
    ("MT", "는"),      # 엠티
    ("LMS", "는"),     # 엘엠에스
])
def test_로마자는_읽는_소리로_본다(word, expect):
    assert josa.attach(word, "은/는") == expect


@pytest.mark.parametrize("word,expect", [
    ("1", "은"),   # 일
    ("3", "은"),   # 삼
    ("6", "은"),   # 육
    ("2", "는"),   # 이
    ("4", "는"),   # 사
    ("5", "는"),   # 오
])
def test_숫자도_읽는_소리로_본다(word, expect):
    assert josa.attach(word, "은/는") == expect


def test_으로_로는_ㄹ이_예외다():
    """받침 유무만 보면 '서울으로' 가 된다."""
    assert josa.attach("서울", "으로/로") == "로"
    assert josa.attach("학교생활", "으로/로") == "로"
    assert josa.attach("휴학", "으로/로") == "으로"   # ㄱ 받침
    assert josa.attach("학교", "으로/로") == "로"     # 받침 없음


def test_모르면_받침_없는_쪽으로_간다():
    """'는·가·를' 이 어색해도 뜻은 통한다. 모르면 덜 튀는 쪽으로."""
    for w in ("", "   ", "?!", "★"):
        assert josa.attach(w, "은/는") == "는"


def test_문장_치환():
    assert josa.sentence("'{s}'{s:은/는} 이 문서에 있어요", s="휴학") \
        == "'휴학'은 이 문서에 있어요"
    assert josa.sentence("'{s}'{s:이/가} 들어간 안내", s="증명서 발급") \
        == "'증명서 발급'이 들어간 안내"


def test_조사는_바로_앞말을_따른다():
    """실수한 자리다.

    "'{subject}' 안내가 여러 곳에" 에서 조사는 subject 가 아니라 '안내' 를
    따른다. subject 를 따르게 걸었더니 "'통금' 안내이" 가 나왔다 —
    원래 맞던 문장을 깬 것이다.
    """
    assert josa.attach("안내", "이/가") == "가"
    assert josa.attach("통금", "이/가") == "이"   # 앞말이 다르면 답도 다르다
