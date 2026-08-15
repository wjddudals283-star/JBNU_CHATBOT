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
V = {"성적": 900, "이의": 120, "신청": 800, "성적이": 12, "의신청": 5,
     "수강신청": 700, "학점": 600, "상한": 40, "졸업요건": 300,
     "졸업": 500, "요건": 200, "등록금": 400, "분할": 60, "납부": 150}


def test_조사가_붙은_조각은_진다():
    """★ 이게 (A) 를 고른 이유다. 최장일치는 '성적이|의신청' 을 낸다."""
    assert glued.split("성적이의신청", V) == ["성적", "이의", "신청"]


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
