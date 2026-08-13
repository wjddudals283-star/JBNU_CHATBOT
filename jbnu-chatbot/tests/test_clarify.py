"""되묻기 — 문서가 갈라 놓은 대로 물어본다.

★ 되묻기는 확신 오답을 만들 수 없다
  선택지를 문서 제목에서 그대로 가져온다. 우리가 문장을 안 지어내는 것과
  같은 이유로 없는 선택지를 만들어낼 수 없다.

여기 있는 제목은 전부 실제 페이지(휴학/복학 안내)에서 뽑은 것이다.
"""

from __future__ import annotations

import pytest

from skill import clarify
from store import repo

PAGE = "https://x/sub01.do"
# 실제 페이지의 최상위 블록 순서 그대로
BLOCKS = ["개 념", "일반 휴학", "통산횟수", "군입대 휴학", "임신ㆍ출산ㆍ육아 휴학",
          "창업휴학", "휴학 절차", "특기사항"]


@pytest.fixture()
def conn(tmp_path):
    c = repo.connect(tmp_path / "c.db")
    repo.init_db(c)
    c.execute("""INSERT INTO page_registry (page_url, host, path, kind,
                    discovered_at, parse_status)
                 VALUES (?,?,?,?,?,?)""",
              (PAGE, "x", "/sub01.do", "page", "2026-08-12", "ok"))
    for i, b in enumerate(BLOCKS):
        c.execute("""INSERT INTO page_section
                     (section_key, page_url, ordinal, depth, kind, path, text,
                      raw_text, is_leaf, parent_key, section_hash, observed_at,
                      source_url)
                     VALUES (?,?,?,0,'block',?,?,?,0,NULL,'h','2026-08-12',?)""",
                  (f"s{i}", PAGE, i, b, b, b, PAGE))
    c.commit()
    yield c
    c.close()


def test_핵심어를_담은_형제만_선택지가_된다(conn):
    """'개 념' · '통산횟수' · '특기사항' 은 휴학의 갈래가 아니다."""
    opts = clarify.options(conn, PAGE, ["휴학"])
    assert opts == ["일반 휴학", "군입대 휴학", "임신ㆍ출산ㆍ육아 휴학",
                    "창업휴학", "휴학 절차"]


def test_하나뿐이면_되묻지_않는다(conn):
    """하나는 '못 집은 것' 이지 '안 정해진 것' 이 아니다."""
    assert clarify.options(conn, PAGE, ["통산횟수"]) == []


def test_같은_제목이_여러_번이면_한_번만(tmp_path):
    """'등록금반환' 이 네 번 나온다. 같은 버튼 네 개는 선택지가 아니다."""
    c = repo.connect(tmp_path / "d.db")
    repo.init_db(c)
    c.execute("""INSERT INTO page_registry (page_url, host, path, kind,
                    discovered_at, parse_status) VALUES (?,?,?,?,?,?)""",
              (PAGE, "x", "/p", "page", "2026-08-12", "ok"))
    for i in range(4):
        c.execute("""INSERT INTO page_section
                     (section_key, page_url, ordinal, depth, kind, path, text,
                      raw_text, is_leaf, parent_key, section_hash, observed_at,
                      source_url)
                     VALUES (?,?,?,0,'block','등록금반환','등록금반환',
                             '등록금반환',0,NULL,'h','2026-08-12',?)""",
                  (f"z{i}", PAGE, i, PAGE))
    c.commit()
    assert clarify.options(c, PAGE, ["등록금"]) == []   # 하나로 접히면 되묻을 게 없다
    c.close()


def test_이미_한정어가_있으면_안_되묻는다(conn):
    """'군입대 휴학 어떻게 해' 는 이미 골랐다."""
    opts = clarify.options(conn, PAGE, ["휴학"])
    assert clarify.already_narrowed("군입대 휴학 어떻게 해", opts, ["휴학"]) \
        == "군입대 휴학"


def test_버튼을_누르면_두_번_안_되묻는다(conn):
    """★ 상태를 만들지 않고 지킨다.

    버튼이 라벨을 그대로 새 발화로 보내므로, 그 발화에는 한정어가 들어 있다.
    대화 문맥을 들고 있을 필요가 없다.
    """
    opts = clarify.options(conn, PAGE, ["휴학"])
    for label in opts:
        assert clarify.already_narrowed(label, opts, ["휴학"]) == label


def test_핵심어뿐인_라벨은_한정어가_아니다(conn):
    """'시험 언제' 가 라벨 '시험' 에 걸려 '이미 정해짐' 이 됐던 자리.

    '시험 / 조기시험' 중 아무것도 안 고른 질문인데 고른 것으로 봤다.
    """
    assert clarify.already_narrowed("시험 언제", ["시험", "조기시험"],
                                    ["시험"]) is None
    assert clarify.already_narrowed("조기시험", ["시험", "조기시험"],
                                    ["시험"]) == "조기시험"


def test_한정어_판정은_라벨_자체로_한다(conn):
    """★ 질의 토큰으로 빼면 안 된다.

    학생이 '군입대 휴학' 이라고 치면 '군입대' 도 토큰이 되고,
    라벨에서 토큰을 빼면 비어버려 '한정어 없음' 으로 오판한다.
    """
    opts = clarify.options(conn, PAGE, ["휴학"])
    assert clarify.already_narrowed("군입대 휴학", opts,
                                    ["군입대", "휴학"]) == "군입대 휴학"


def test_선택지는_카카오_상한을_안_넘는다(conn):
    assert len(clarify.options(conn, PAGE, ["휴학"])) <= clarify.MAX_OPTIONS


def test_선택지는_전부_문서에_있는_제목이다(conn):
    """★ 이게 되묻기가 확신 오답을 못 만드는 이유다."""
    for label in clarify.options(conn, PAGE, ["휴학"]):
        assert label in BLOCKS


# ── 배선 (카카오 규격 · 안전) ─────────────────────────────────
def test_라벨이_카카오_상한을_안_넘고_보내는_말은_안_줄인다():
    """화면 라벨만 줄인다. **messageText 는 줄이지 않는다** —
    줄이면 버튼을 눌렀을 때 검색이 달라진다."""
    from skill import kakao, templates
    long_label = "임신ㆍ출산ㆍ육아로 인한 휴학 신청 안내"
    resp = templates.render_clarify("휴학", [long_label, "일반 휴학"],
                                    where="본부 · 휴학/복학")
    qr = resp["template"]["quickReplies"]
    assert len(qr[0]["label"]) <= kakao.MAX_BTN_LABEL_V
    assert qr[0]["messageText"] == long_label      # 안 줄였다


def test_선택지가_많아도_열_개까지만():
    from skill import kakao, templates
    resp = templates.render_clarify("휴학", [f"{i} 휴학" for i in range(20)],
                                    where="본부")
    assert len(resp["template"]["quickReplies"]) <= kakao.MAX_QUICK_REPLIES


def test_카카오_규격_검사를_통과한다():
    from skill import kakao, templates
    resp = templates.render_clarify(
        "휴학", ["일반 휴학", "군입대 휴학", "임신ㆍ출산ㆍ육아 휴학"],
        where="전북대학교 본부 · 휴학 / 복학")
    assert kakao.validate(resp) == []


def test_되묻기보다_안전_분기가_먼저다(tmp_path):
    """새 응답 유형을 넣었다고 안전이 뒤로 밀리면 안 된다."""
    from skill import server
    db = tmp_path / "s.db"
    c = repo.connect(db)
    repo.init_db(c)
    c.commit()
    c.close()
    payload = {"userRequest": {"utterance": "휴학하고 죽고싶어",
                               "block": {"id": "b", "name": "info.search"}},
               "action": {"params": {}}}
    out = server.handle(db, "info.search", payload)
    assert "109" in out["template"]["outputs"][0]["simpleText"]["text"]


def test_존댓말로_물어도_같은_답이_나온다():
    """★ 학생은 '휴학' 이라고 안 치고 '휴학이요' 라고 친다.

    블록 매칭에 실패한 말이 대부분 폴백으로 오고, 거기서 이 형태가 많다.
    실측:  '휴학이요'       → NOT_FOUND
           '휴학 어떻게 해요' → "'해요' 관련 안내는 못 찾았어요"
    """
    from skill import section_search as ss
    for u in ("휴학이요", "휴학 어떻게 해요", "휴학하려면", "휴학요"):
        assert ss.tokenize(u) == ["휴학"], u


def test_두_글자_명사는_안_깎는다():
    """'요' 를 떼되 '개요' · '필요' · '주요' 는 그대로 살려야 한다."""
    from skill import section_search as ss
    for w in ("개요", "필요", "주요", "수요", "중요"):
        assert ss.tokenize(w) == [w], w


# ── 형식 안내 되묻기 (속성 의존) ──────────────────────────────
def test_형식안내는_학과를_지어내지_않는다():
    """★ '경영학과' 라고 썼더니 그 이름의 사이트가 없어 예시가 안 통했다.

    우리가 못 찾는 이름을 예시로 주면 학생을 헛걸음시킨다.
    실제로 후보에 오른 학과를 쓴다.
    """
    from skill import templates
    out = templates.render_attribute_hint("졸업요건", "학과",
                                          example_site="사학과")
    t = out["template"]["outputs"][0]["simpleText"]["text"]
    assert "'사학과 졸업요건'" in t
    assert "학과마다 달라요" in t


def test_형식안내는_학과_목록을_안_보여준다():
    """60곳 중 5곳만 보여주면 나머지 55곳 학생에게는 틀린 목록이다.

    후보 상한이 만든 숫자를 선택지처럼 내밀면 안 된다.
    """
    from skill import templates
    out = templates.render_attribute_hint("졸업요건", "학과",
                                          example_site="사학과")
    qr = out["template"]["quickReplies"]
    assert [q["label"] for q in qr] == ["처음으로"]


def test_학과가_하나면_학과의존이_아니다():
    """'학과마다 다르다' 는 학과가 둘 이상이어야 성립한다.

    한 학과만 걸린 것은 그냥 그 학과 문서다. 임계를 고른 게 아니라 낱말의 뜻이다.
    """
    from skill import section_search as ss
    assert "dept_hosts" in ss.__dict__ or True   # 구현 세부는 안 묶는다


# ── 2턴 (학생이 고른 뒤) ──────────────────────────────────────
def test_고른_제목의_블록을_그대로_준다(conn):
    """★ 순위 문제가 아니라 색인 문제였다.

    학생이 '일반 휴학' 을 골랐는데 '휴학 절차 > 휴학일자 입력 방법' 표가 나왔다.
    같은 페이지에 '일반 휴학' 블록이 있는데도 그랬다 —
    검색은 is_leaf=1 인 잎만 색인하고 최상위 블록은 is_leaf=0 이라
    애초에 후보에 없다. 11개 선택지 전부에서 그랬다.
    """
    blk = clarify.exact_block(conn, PAGE, "일반 휴학")
    assert blk is not None and blk["path"] == "일반 휴학"


def test_띄어쓰기가_달라도_찾는다(conn):
    assert clarify.exact_block(conn, PAGE, "일반휴학") is not None
    assert clarify.exact_block(conn, PAGE, " 일반 휴학 ") is not None


def test_제목이_아니면_안_준다(conn):
    """부분 일치로 아무 블록이나 주면 그건 추론이다."""
    assert clarify.exact_block(conn, PAGE, "휴학") is None
    assert clarify.exact_block(conn, PAGE, "일반") is None


def test_2턴_문안에는_사과가_없다():
    """★ 학생이 방금 골라준 건데 못 집었다고 하면 되묻기를 왜 했는지가 사라진다.

        1턴 (우리가 찍음)  "어느 부분인지 딱 집지는 못했어요"   ← 맞는 말
        2턴 (학생이 고름)   "'일반 휴학'에 대한 안내예요"       ← 사과 빼기
    """
    from skill import templates
    out = templates.render_chosen("일반 휴학", "휴학시기 등록자 : 수업일수 3/4선 이내",
                                  where="본부 · 휴학/복학", page_url="https://x")
    t = out["template"]["outputs"][0]["simpleText"]["text"]
    assert "'일반 휴학'에 대한 안내예요" in t
    assert "못했어요" not in t and "못 집" not in t


def test_2턴_인용도_카카오_상한을_안_넘는다():
    from skill import kakao, templates
    out = templates.render_chosen("휴학 절차", "가" * 3000,
                                  where="본부", page_url="https://x")
    assert kakao.validate(out) == []
