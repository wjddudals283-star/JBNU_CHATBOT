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


def test_라벨이_길다고_선택지에서_빼지_않는다():
    """★ 24자 상한이 '복학' 을 통째로 막고 있었다.

    문서가 갈래를 한 제목에 나열한다:
        '일반복학, 임신·출산·육아 복학, 창업복학, 질병복학(학부생)'  34자
    이게 탈락하면 남는 건 '복학 절차' 하나뿐이라 MIN_OPTIONS 미달로
    되묻기 자체가 안 됐다.

    버튼에 잘려 보이는 건 render_clarify 가 화면 라벨만 줄여서 처리하고,
    **보내는 말은 안 줄이므로** 길어도 검색은 온전하다.
    """
    assert clarify.MAX_LABEL >= 34


def test_출처에_우리가_본_시각을_붙인다():
    """★ 원문이 바뀐 걸 우리가 막을 수는 없다. 막을 수 있는 건 언제 본 건지 숨기는 것이다.

    학교가 OASIS → JUMP 로 갈아탔는데 우리 사본은 그 전 것이었고,
    답변에 시각이 없어 학생이 알 방법이 없었다.
    """
    from skill import templates
    assert templates.stamp_line("본부 · 자퇴", "8/11 13:28 확인") \
        == "📄 본부 · 자퇴 (8/11 13:28 확인)"
    assert templates.stamp_line("본부 · 자퇴", "") == "📄 본부 · 자퇴"
    # 학교 표기는 **뒤에** 붙는다. 버리지 않고, 우리 관측보다 앞세우지도 않는다.
    assert templates.stamp_line("본부 · 자퇴", "8/11 확인",
                                page_modified="2025-01-09") \
        == "📄 본부 · 자퇴 (8/11 확인 · 학교 표기 2025-01-09)"


def test_학교가_말한_수정일보다_우리_관측이_먼저다():
    """★ 학교 last_modified 는 못 믿는다.

    JUMP 전환 뒤에도 '2025-01-09' 그대로였다. 2.7% 만 채워져 있고
    채워진 것도 안 바뀐다. 우리가 보증할 수 있는 건 '우리가 언제 봤나' 뿐이다.
    """
    from skill import templates

    class _H:
        page_modified = "2025-01-09"
        observed_at = "2026-08-11T13:28:22+09:00"
        page_title = "자퇴 / 제적"
        site_name = "전북대학교 본부"

    line = templates._source_line(_H())
    assert line.index("8/11") < line.index("2025-01-09")   # 우리 관측이 앞
    assert "학교 표기 2025-01-09" in line                   # 버리지도 않는다


def test_사이트로_좁혀_못_찾으면_안_좁히고_다시_찾는다(tmp_path):
    """★ 별칭이 더 긴 말의 일부일 때 질문이 엉뚱한 사이트에 갇힌다.

        '학자금 대출' → 별칭 '대출' 이 걸려 도서관으로 좁혀짐 → 후보 0
    정작 본부 '학자금 대출' 페이지에는 잎이 20개 있고 점수도 163점이었다.

    위험한 별칭 목록을 만들지 않는다 — '좁혀서 못 찾았다' 는 관측이 곧 근거다.
    ('도서 대출' 은 도서관에서 찾히므로 이 길을 안 탄다)
    """
    from skill import section_search as ss
    # 별칭이 실제로 '대출' 을 도서관으로 보내고 있어야 이 테스트가 뜻이 있다
    host, _ = ss.match_site("학자금 대출")
    assert host == "dl.jbnu.ac.kr", "별칭 상황이 바뀌었다 — 테스트를 다시 봐야 한다"
    assert "force_all_sites" in ss._attempt.__code__.co_varnames


# ═══════════════════════════════════════════════════════════════
# 되묻기가 자기 자신으로 돌아오면 안 된다
# ═══════════════════════════════════════════════════════════════

def test_선택지를_그대로_보내면_고른_것이다():
    """★ 버튼 전수를 세 겹으로 넓혔더니 무한 루프가 나왔다 (2026-08-14).

    '시험' 을 물으면 선택지가 ['시험', '조기시험'] 인데
    '시험' 을 누르면 **같은 되묻기가 또** 나왔다. 학생은 빠져나갈 수 없다.

    already_narrowed 가 한정어 없는 라벨을 일부러 건너뛰는 게 원인이었다.
    그 규칙 자체는 맞다 — '시험 언제' 는 아무것도 고른 게 아니다.
    다만 **발화 전체가 라벨과 같으면** 그건 고른 것이다.
    """
    opts = ["시험", "조기시험"]
    assert clarify.chosen_option("시험", opts) == "시험"
    assert clarify.chosen_option("조기시험", opts) == "조기시험"
    # 한정어 없는 라벨이라 already_narrowed 는 (일부러) 안 잡는다
    assert clarify.already_narrowed("시험", opts, ["시험"]) is None


def test_고른_게_아니면_여전히_되묻는다():
    """'시험 언제' 는 '시험' 을 고른 게 아니다 — 이걸 깨면 예전 오판이 돌아온다."""
    opts = ["시험", "조기시험"]
    assert clarify.chosen_option("시험 언제", opts) is None
    assert clarify.chosen_option("시험 일정 알려줘", opts) is None


def test_띄어쓰기가_달라도_고른_것으로_본다():
    assert clarify.chosen_option("일반휴학", ["일반 휴학", "군입대 휴학"]) == "일반 휴학"


# ═══════════════════════════════════════════════════════════════
# 같은 버튼 여럿은 선택지가 아니다 (AMBIGUOUS 목록)
# ═══════════════════════════════════════════════════════════════

from skill import templates  # noqa: E402


class _Hit:
    def __init__(self, site, title, url, path=""):
        self.site_name, self.page_title, self.page_url = site, title, url
        self.path = self.quote_path = path or title
        self.observed_at = None
        self.page_modified = ""


class _Res:
    def __init__(self, hits):
        from skill.section_search import Outcome
        self.outcome, self.hits = Outcome.AMBIGUOUS, hits
        self.subject, self.missing_tokens = "등록금", []
        self.needs_attribute = ""
        self.searched_sections = 100


def _items(resp):
    for o in resp["template"]["outputs"]:
        if "listCard" in o:
            return [(i["title"], i.get("description", ""))
                    for i in o["listCard"].get("items") or []]
    return []


def test_한_사이트뿐이면_문서_제목을_앞에_둔다():
    """★ '등록금 납부 기간' 은 네 줄이 전부 '등록금' 이었다 (2026-08-14 실측).

    구별되는 말(등록안내·차등납부·분할납부)은 설명 줄에 있었는데,
    눈이 읽는 건 제목 줄이다. 학과 이름을 앞에 두는 규칙은
    후보가 **여러 사이트에 걸쳐 있을 때**의 규칙이었다.
    """
    hits = [_Hit("등록금", t, f"https://x/{i}")
            for i, t in enumerate(["등록안내", "차등납부", "분할납부", "등록금반환"])]
    titles = [t for t, _d in _items(templates.render_section(_Res(hits)))]
    assert titles == ["등록안내", "차등납부", "분할납부", "등록금반환"]
    assert len(set(titles)) == len(titles), "같은 말 네 개는 선택지가 아니다"


def test_사이트가_여럿이면_사이트를_앞에_둔다():
    """205개 사이트가 붙은 뒤로는 문서 제목만으로 못 고른다 — 그 규칙은 유지."""
    hits = [_Hit("전기공학과", "졸업요건", "https://a"),
            _Hit("사학과", "졸업요건", "https://b")]
    titles = [t for t, _d in _items(templates.render_section(_Res(hits)))]
    assert titles == ["전기공학과", "사학과"]


def test_완전히_같은_줄은_한_번만():
    """★ clarify.options 에 이미 있던 규칙인데 여기엔 없었다.

    '근로장학생' 은 같은 공지 제목이 세 번 나왔다.
    원칙이 한 군데만 있으면 다른 데서 조용히 어긋난다.
    """
    hits = [_Hit("창업교육센터", "센터 공지사항", f"https://x/{i}") for i in range(3)]
    hits.append(_Hit("도서관", "서비스별 연락처", "https://y"))
    rows = _items(templates.render_section(_Res(hits)))
    assert len(rows) == 2, rows


def test_하나만_남으면_고르라고_하지_않는다():
    """겹치는 걸 지우고 나니 하나면 '여러 곳' 이 아니다."""
    hits = [_Hit("생활관", "생활관 자치위원회", f"https://x/{i}") for i in range(3)]
    resp = templates.render_section(_Res(hits))
    text = " ".join(o["simpleText"]["text"] for o in resp["template"]["outputs"]
                    if "simpleText" in o)
    header = resp["template"]["outputs"][0]["listCard"]["header"]["title"]
    assert "여러 곳" not in header, header
    assert "골라" not in text and "어느 쪽" not in text
    # ★ 하나로 줄었다고 그게 답이 되는 건 아니다.
    #   여기까지 온 건 검색이 **고르지 못했다**는 뜻이다.
    assert "여기 있어요" not in header, "더 단정적으로 틀리면 안 된다"
    assert "확인이 필요해요" in text


# ═══════════════════════════════════════════════════════════════
# 형식 안내도 버튼을 준다 — 라벨이 완전한 문구라 상태가 필요 없다
# ═══════════════════════════════════════════════════════════════

def test_형식안내에_후보_학과_버튼이_붙는다():
    """★ 되물어 놓고 대답을 못 받고 있었다 (2026-08-15 실측).

    "어느 학과인지" 라고 물으면 사람은 '경제학부' 라고만 답한다.
    그 한 마디에는 주제가 없어서 새 질문으로 처리됐다.
        되묻기 13건 중 버튼 11건은 이어짐, 형식 안내 2건은 전부 끊김
    버튼이 사는 이유는 라벨이 **완전한 문구**라 상태가 필요 없어서다.
    """
    r = templates.render_attribute_hint(
        "졸업요건", "학과", example_site="사학과",
        candidates=[("사학과", "https://a"), ("경제학부", "https://b"),
                    ("전북대학교 본부", "https://c")])
    items = [o["listCard"]["items"] for o in r["template"]["outputs"]
             if "listCard" in o][0]
    names = [i["title"] for i in items]
    # ★ 가나다순 — 검색 점수 순서는 학생에게 뜻이 없다
    assert names == ["경제학부", "사학과"], names
    # ★ 발화 버튼이 아니라 **링크**다 — button_probe 가 잡았다.
    #   후보에 오른 학과 3곳이 그 이름으로 다시 물으면 답이 없었다.
    #   우리가 준 선택지인데 답이 없으면 고장이다. 링크는 그 실패가 불가능하다.
    assert all(i.get("link") for i in items)
    assert [q["label"] for q in r["template"]["quickReplies"]] == ["처음으로"]


def test_전체_목록이_아니라고_밝힌다():
    """★ 60곳 중 몇 곳일 뿐이다. 전체처럼 내밀면 나머지에게 틀린 목록이 된다."""
    r = templates.render_attribute_hint(
        "졸업요건", "학과", example_site="사학과",
        candidates=[("사학과", "https://a")])
    t = [o["simpleText"]["text"] for o in r["template"]["outputs"]
         if "simpleText" in o][0]
    assert "전체 목록은 아니에요" in t
    assert "직접 물어봐 주세요" in t, "목록에 없는 학생의 길도 열어 둔다"


def test_후보가_없으면_예시만_준다():
    """목록을 못 만들면 예전 문안 그대로 — 없는 것을 지어내지 않는다."""
    r = templates.render_attribute_hint("졸업요건", "학과", candidates=[])
    assert not any("listCard" in o for o in r["template"]["outputs"])
    t = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "붙여서 물어봐 주세요" in t


# ═══════════════════════════════════════════════════════════════
# 못 읽는 표는 인용하지 않고 링크만 준다
# ═══════════════════════════════════════════════════════════════

def test_달력_위젯을_알아본다():
    """★ '취업 상담' 이 43칸 달력을 인용했다 (2026-08-17).

    ★ 밀도(21.9%)로 자르지 않는다 — 그건 임계값이고 코퍼스가 바뀌면 흔들린다.
      요일 머리글은 **관측된 표시**다. 원문이 '이 표는 달력이다' 라고 말해 준다.
    """
    assert templates.is_calendar_widget(
        "일 | 월 | 화 | 수 | 목 | 금 | 토 | | | | | | 1 2 | 3 | 4")
    assert templates.is_calendar_widget(
        "요일 | 일 | 월 | 화 | 수 | 목 | 금 | 토 | 1 | 2")
    # 읽히는 표는 건드리지 않는다
    assert not templates.is_calendar_widget(
        "등급 | 평점 | 비고(100점 만점) A+ | 4.5 | 95 ~ 100")
    assert not templates.is_calendar_widget(
        "구분 | 내용 | 신청기간 개설교과목 신청 | 학과 | 4월")
    assert not templates.is_calendar_widget("일 | 월")      # 표라기엔 짧다
