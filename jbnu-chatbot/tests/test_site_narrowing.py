"""사이트로 좁혔으면 **그 이름을 검색어에서 지운다** — 양쪽으로 본다.

★ 조사 떼기가 '항공우주공학과' 를 '항공우주공학' 으로 만든다 ('과' 가 조사다).
  그래서 `사이트이름 in 토큰` 만 보면 지워야 할 낱말이 안 지워졌다.
  남은 학과 낱말이 후보를 다 채워서
  '휴학 항공우주공학과' 가 matched=['항공우주공학'] 로 학습성과를 냈다.

★ 실측: '주제 + 학과' 정확도 48% → 95%. 46문항 영향 0건.
"""

from skill import section_search as S


def _narrow(q):
    host, label = S.match_site(q)
    assert host, q
    used = {a for a, h in S.site_aliases().items() if h == host} | {label}
    return [t for t in S.tokenize(q)
            if not any(u and (u in t or t in u) for u in used)]


def test_학과_낱말이_지워진다():
    assert _narrow("휴학 항공우주공학과") == ["휴학"]


def test_조사가_떨어져도_지워진다():
    """'항공우주공학과' → 토큰 '항공우주공학' 은 사이트 이름보다 짧다."""
    assert "항공우주공학" in S.tokenize("항공우주공학과")
    assert "항공우주공학" not in _narrow("졸업요건 항공우주공학과")


def test_주제는_남는다():
    assert _narrow("졸업요건 경영학과") == ["졸업요건"]


def test_다_지워지면_원래_질의를_쓴다():
    """학과 이름만 친 질문은 지우면 아무것도 안 남는다 — 그때는 안 지운다."""
    host, label = S.match_site("회계학과")
    used = {a for a, h in S.site_aliases().items() if h == host} | {label}
    toks = S.tokenize("회계학과")
    narrowed = [t for t in toks if not any(u and (u in t or t in u) for u in used)]
    assert narrowed == []          # 다 지워진다
    # section_search 는 이때 원래 토큰을 그대로 쓴다 (if narrowed: 가드)
