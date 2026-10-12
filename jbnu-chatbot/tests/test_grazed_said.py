"""원문이 안 쓰는 말을 **원문이 쓴 말처럼** 말하지 않는다.

★ 무엇이 거짓이었나 (2026-10-13)
  '기숙사 통금' 에 이렇게 답했다 —
      "'통금'은 이 문서들 제목에는 없고 **본문에만 나왔어요**"
  본문에도 없었다. 학교는 '출입제한시간'·'귀관 시간' 이라고 쓴다.
  어제 붙인 동의어로 걸린 것이고, 화면은 **우리가 이은 말**을
  원문이 쓴 말처럼 단정했다.

★ ⑥ 과 같은 병이다
  게시판을 본 적도 없이 '작성자·등록일·조회수' 라는 칸 이름을 지어냈고,
  커밋에 "지어내지 않았다" 고 적었다. 모양이 똑같다 —
  **우리가 만든 것을 원문의 것이라고 말한다.**
  동의어 기능을 붙일 때마다 이 칸이 비례해서 커지므로 자가 필요하다.

★ 되물으라고 할 말은 **실제로 답하는지 확인한 뒤**에만 말한다
  '출입제한시간' 으로 다시 물어도 같은 되묻기가 나온다 (실측).
  그래서 "그 말로 다시 물어보세요" 는 쓰지 않는다 — 버튼 루프와 같다.
"""

from __future__ import annotations

import pathlib

import pytest

from skill import section_search as S
from skill import templates
from store import repo

DB = pathlib.Path("data/jbnu.db")
pytestmark = pytest.mark.skipif(not DB.exists(), reason="수집한 DB 가 있을 때만")


@pytest.fixture(scope="module")
def conn():
    c = repo.connect(DB, readonly=True)
    yield c
    c.close()


def test_원문이_안_쓰는_말이면_원문의_말을_찾아_둔다(conn):
    r = S.search(conn, "기숙사 통금", repo=repo)
    assert r.grazed_token == "통금"
    # 학교가 쓰는 말이 하나 이상 잡혀야 한다 — 없으면 동의어로 걸린 걸 모른다
    assert r.grazed_said, "동의어로 걸렸는데 원문의 말을 안 남겼다"
    for w in r.grazed_said:
        assert w != "통금"


def test_후보마다_쓰는_말이_다르면_전부_모은다(conn):
    """★ 1등 것만 들고 '이 안내들은' 이라고 쓰면 또 범위를 부풀린 거짓이다.

    1등(FAQ)은 '출입제한시간', 2등(생활관생 수칙)은 '귀관 시간' 이라고 쓴다.
    """
    r = S.search(conn, "기숙사 통금", repo=repo)
    for w in r.grazed_said:
        assert any(w in (h.text or "") for h in r.hits), \
            f"'{w}' 는 보여줄 후보 어디에도 없다 — 지어낸 말이 된다"


def test_원문이_그_말을_쓰면_비워_둔다(conn):
    """★ 자가 한쪽으로만 틀리지 않게 **반대쪽도** 잡는다.

    '근로장학생' 은 도서관 연락처 본문에 그 말 그대로 있다.
    그때는 "본문에만 나왔어요" 가 참이므로 문장을 바꾸면 안 된다.
    """
    r = S.search(conn, "근로장학생", repo=repo)
    assert r.grazed_token == "근로장학생"
    assert r.grazed_said == ()


def test_화면이_원문의_말을_그대로_옮긴다(conn):
    r = S.search(conn, "기숙사 통금", repo=repo)
    out = templates.render_section(r, utterance="기숙사 통금")
    text = " ".join(
        o["simpleText"]["text"] for o in out["template"]["outputs"]
        if "simpleText" in o)
    titles = " ".join(
        o["listCard"]["header"]["title"] for o in out["template"]["outputs"]
        if "listCard" in o)
    # ① 못 찾았다고 말한다 — '없다' 가 아니라 '못 찾았다' (없다의 갈래)
    assert "못 찾았어요" in titles
    # ② 원문의 말을 전부 적는다
    for w in r.grazed_said:
        assert w in text
    # ③ 거짓 서술이 사라졌다
    assert "본문에만 나왔어요" not in text
    # ④ 되물으라고 하지 않는다 — 그 말로 물어도 또 되묻기다
    assert "다시 물어" not in text
