"""붙여 쓴 말을 관측된 낱말로 쪼갠다.

★ 왜 (2026-08-15 실측)
  카카오 등록 발화 19개가 전부 띄어쓴 형태다. 그보다 큰 건
  **검색 토큰화가 공백으로 자른다**는 점이다 — 붙여 쓰면 통째로 한 토큰이 되고
  코퍼스에 그 문자열이 없으면 0건이다.
  46문항을 붙여 써 보니 35/38 건에서 답이 달라졌고 대부분 '못 찾았어요' 였다.

★ 최장일치가 아니라 DF 가중이다 — 가설이 아니라 실측이다
      최장일치   성적이 | 의신청
      DF 가중    성적 | 이의 | 신청
"""

from __future__ import annotations

import pytest

from crawler import vocab as vocab_mod
from skill import glued, section_search as S
from store import repo

# 실제 코퍼스에서 관측될 법한 빈도. 조사 붙은 형태도 **일부러** 넣는다 —
# 그게 있어도 분해에서 져야 한다는 게 요점이다.
# ★ 실제 코퍼스의 DF 를 그대로 옮긴 값이다 (vocab 85,544문단 기준).
#   지어낸 값을 쓰면 실제로 안 일어나는 일을 재게 된다 —
#   처음엔 '의신청: 5' 를 지어 넣었는데 실제로는 **DF 0**(코퍼스에 없는 말)이다.
#   그 지어낸 값 때문에 IDF 가 '성적이 | 의신청' 을 고르는 걸 보고
#   IDF 의 약점을 알게 됐다. 아래 test_조사_붙은_희귀형이 그걸 고정한다.
V = {"성적": 378, "이의": 46, "신청": 1134, "성적이": 266, "이의신청": 10,
     "수강신청": 700, "학점": 600, "상한": 40, "졸업요건": 139,
     "졸업": 500, "요건": 200, "등록금": 400, "분할": 60, "납부": 150}


def test_조사가_붙은_조각은_진다():
    """★ 최장일치는 '성적이 | 의신청' 을 낸다. 그게 (A) 를 고른 이유였다.

    실제 코퍼스에는 '의신청' 이 없어서(DF 0) 그 분해 자체가 불가능하다.
    """
    got = glued.split("성적이의신청", V)
    assert got and "성적이" not in got


def test_조사형이_사전에_있어도_진다():
    """★ IDF 가 드문 조각을 선호하니 조사 붙은 희귀형을 고를까 걱정했는데,
    **실제 분포에서는 안 그렇다.**

    '의신청'(DF 5) 을 넣어 봐도 '성적 | 이의신청' 이 이긴다 —
    조각이 길수록 점수에 길이가 곱해지기 때문이다.
    처음엔 지어낸 값('성적이: 12')으로 재서 반대 결론을 봤다.
    ★ 지어낸 값으로 재면 실제로 안 일어나는 일을 재게 된다.
    """
    assert glued.split("성적이의신청", dict(V, **{"의신청": 5}))         == ["성적", "이의신청"]


def test_찾는_데_값진_쪽을_고른다():
    """★ 흔한 조각을 선호했더니 통째로 있는 말을 또 쪼갰다 (2026-08-15).

    '이의신청'(DF 30) 이 사전에 있는데 '이의'(120) + '신청'(800) 로 쪼갰다.
    그러면 띄어 쓴 경우와 **토큰이 달라져** 다른 검색이 된다 —
    갈리기는 갈렸는데 답이 안 나왔다.

    ★ '갈렸다' 와 '답이 나온다' 는 다른 일이다.
    ★ 검색에서 값진 건 **드문 낱말**이다. 흔한 쪽을 고르는 건
      찾는 데 쓸모없는 쪽을 고르는 것이다. 그래서 IDF 다 —
      회복 건수가 동점이어도 이 이유는 안 뒤집힌다.
    """
    assert glued.split("성적이의신청", V) == ["성적", "이의신청"]


def test_통째로_있는_말은_안_쪼갠다():
    """'졸업요건' 은 코퍼스에 있는 말이다. 쪼개면 오히려 넓어진다."""
    assert glued.split("졸업요건", V) is None


def test_짧은_말은_안_건드린다():
    assert glued.split("휴학", V) is None
    assert glued.split("졸업", V) is None


def test_사전이_없으면_아무것도_안_한다():
    """표가 없는 DB 에서도 답변은 그대로 나가야 한다."""
    assert glued.split("수강신청학점상한", {}) is None


def test_전부_덮지_못하면_안_쪼갠다():
    """모르는 낱말이 섞이면 억지로 쪼개지 않는다."""
    assert glued.split("수강신청뷁뷁뷁", V) is None


def test_영문_숫자_섞인_건_다른_문제다():
    assert glued.split("A+몇점입니까", V) is None


def test_사전은_관측이다(tmp_path):
    """우리가 적는 목록이 아니라 코퍼스에서 센 것이다."""
    p = tmp_path / "v.db"
    c = repo.connect(p)
    repo.init_db(c)
    for i in range(4):
        c.execute("""INSERT INTO page_registry (page_url, host, path, kind,
                       discovered_at) VALUES (?,'x','/','page','t')""",
                  (f"https://x/{i}",))
        c.execute("""INSERT INTO page_section
                       (section_key, page_url, ordinal, depth, kind, path, text,
                        raw_text, is_leaf, section_hash, observed_at, source_url)
                     VALUES (?,?,?,0,'paragraph','p','수강신청 학점 상한 안내',
                             '수강신청 학점 상한 안내',1,'h','t','u')""",
                  (f"s{i}", f"https://x/{i}", i))
    c.commit()
    out = vocab_mod.build(c)
    assert out["terms_kept"] > 0
    got = glued.load_vocab(c)
    assert got.get("수강신청") == 4
    assert "학점" in got
    c.close()


def test_한_번만_읽는다(tmp_path):
    """★ 매번 7만 행을 읽으면 199ms 가 붙는다 (T7 상한 300ms)."""
    p = tmp_path / "c.db"
    c = repo.connect(p)
    repo.init_db(c)
    c.execute("INSERT INTO term (term, df, built_at) VALUES ('가나',5,'t1')")
    c.commit()
    a = glued.load_vocab(c)
    b = glued.load_vocab(c)
    assert a is b, "같은 판이면 같은 객체를 돌려줘야 한다"
    # 사전이 다시 만들어지면 갈아끼운다
    c.execute("UPDATE term SET built_at='t2'")
    c.commit()
    assert glued.load_vocab(c) is not a
    c.close()


def test_분해로_찾으면_표시가_남는다(tmp_path):
    """★ 넓혀서 찾은 것은 넓히기 전과 같은 무게로 두면 안 된다."""
    p = tmp_path / "s.db"
    c = repo.connect(p)
    repo.init_db(c)
    for i in range(5):
        c.execute("""INSERT INTO page_registry (page_url, host, path, kind,
                       discovered_at) VALUES (?,'fees.jbnu.ac.kr','/','page','t')""",
                  (f"https://fees.jbnu.ac.kr/{i}",))
        c.execute("""INSERT INTO page_section
                       (section_key, page_url, ordinal, depth, kind, path, text,
                        raw_text, is_leaf, section_hash, observed_at, source_url)
                     VALUES (?,?,?,0,'paragraph','분할 납부','등록금 분할 납부는 4회로 나눠 냅니다',
                             '등록금 분할 납부는 4회로 나눠 냅니다',
                             1,'h','t','https://fees.jbnu.ac.kr/x')""",
                  (f"k{i}", f"https://fees.jbnu.ac.kr/{i}", i))
        c.execute("""INSERT INTO page_section_fts (section_key, path, text)
                     VALUES (?,?,?)""",
                  (f"k{i}", "분할 납부", "등록금 분할 납부는 4회로 나눠 냅니다"))
    c.commit()
    vocab_mod.build(c)
    r = S.search(c, "등록금분할납부", repo=repo)
    if r.outcome is S.Outcome.FOUND:
        assert r.via_split, "분해로 찾았으면 표시가 남아야 한다"
        assert "쪼개서 찾음" in r.defer_reason
    c.close()
