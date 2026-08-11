"""조사를 받침에 맞춘다.

    '휴학' 는 이 문서에 있어요      →  '휴학'은 이 문서에 있어요
    '증명서 발급' 가 들어간 안내     →  '증명서 발급'이 들어간 안내

★ 왜 지금까지 틀렸나
  질문의 말을 그대로 문장에 끼워 넣는데, 그 말이 무엇일지 미리 알 수 없다.
  그래서 '는' 하나로 통일해 두었다. 학생이 보는 문장이 매번 어색해진다.

★ 인용문은 손대지 않는다
  조사를 맞추는 건 **우리가 쓴 문장**이다. 학교 원문은 그대로 옮긴다.
  요약도 교정도 하지 않는다는 원칙은 인용에 적용되고, 여기는 우리 말이다.

★ 따옴표 뒤에 붙는다
  '휴학' 뒤의 조사는 따옴표가 아니라 **휴학**의 받침을 따른다.
  닫는 따옴표·괄호를 벗기고 마지막 글자를 본다.
"""

from __future__ import annotations

import re

_STRIP = "'\"’”)]』」》 \t"

# 숫자를 읽었을 때 받침이 있는가 — 0 영, 1 일, 3 삼, 6 육, 7 칠, 8 팔
_DIGIT_JONG = {"0": True, "1": True, "2": False, "3": True, "4": False,
               "5": False, "6": True, "7": True, "8": True, "9": False}
# 로마자를 읽었을 때 받침으로 끝나는 글자
_ALPHA_JONG = set("lmnr")          # L 엘 · M 엠 · N 엔 · R 알
_ALPHA_NG = {"g": False, "b": False}   # 나머지는 모음 끝으로 본다


def has_final(word: str) -> bool | None:
    """마지막 글자에 받침이 있나. 판단할 수 없으면 None."""
    w = (word or "").rstrip(_STRIP)
    if not w:
        return None
    ch = w[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28 != 0
    if ch.isdigit():
        return _DIGIT_JONG[ch]
    low = ch.lower()
    if low.isalpha():
        return low in _ALPHA_JONG
    return None


def pick(word: str, with_final: str, without_final: str) -> str:
    """받침에 맞는 조사. 판단 못 하면 **받침 없는 쪽**을 쓴다.

    '는·가·를' 이 어색해도 뜻은 통한다. 모르면 덜 튀는 쪽으로 간다.
    """
    f = has_final(word)
    return with_final if f else without_final


def attach(word: str, pair: str) -> str:
    """`attach("휴학", "은/는")` → `'은'`.

    쌍은 받침 있는 것을 앞에 쓴다 — 은/는, 이/가, 을/를, 과/와, 으로/로.

    ★ '으로/로' 만 예외다 — ㄹ 받침은 '로' 를 쓴다 ('서울로', '학교생활로').
      받침 유무만 보면 '서울으로' 가 된다.
    """
    a, b = pair.split("/")
    if pair == "으로/로":
        w = (word or "").rstrip(_STRIP)
        if w and "가" <= w[-1] <= "힣" and (ord(w[-1]) - 0xAC00) % 28 == 8:
            return b        # ㄹ 받침
    return pick(word, a, b)


def sentence(template: str, **words: str) -> str:
    """문장 안의 `{이름:은/는}` 자리를 채운다.

        sentence("'{s}'{s:은/는} 이 문서에 있어요", s="휴학")
        → "'휴학'은 이 문서에 있어요"

    자리표시자를 두 번 쓰는 게 눈에 거슬리지만, 조사가 **어느 낱말**을
    따르는지 문장에 드러나는 편이 낫다. 앞말이 바뀌면 조사도 바뀐다는 걸
    다음 사람이 알 수 있다.
    """
    def sub(m: re.Match) -> str:
        name, _, pair = m.group(1).partition(":")
        val = words.get(name, "")
        return attach(val, pair) if pair else val
    return re.sub(r"\{([^{}]+)\}", sub, template)
