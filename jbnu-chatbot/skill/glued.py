"""붙여 쓴 말을 **관측된 낱말**로 쪼갠다.

★ 왜 필요한가 (2026-08-15 실측)
  카카오에 등록한 발화 19개가 전부 띄어쓴 형태다. 그래서
  '졸업 요건' 은 블록으로, '졸업요건' 은 폴백으로 간다.
  그보다 큰 건 **검색 토큰화가 공백으로 자른다**는 점이다 —
  붙여 쓰면 통째로 한 토큰이 되고 코퍼스에 그 문자열이 없으면 0건이다.
  46문항을 붙여 써 보니 35/38 건에서 답이 달라졌고 대부분 '못 찾았어요' 였다.

★ 최장일치가 아니라 DF 가중이다 — 조사 때문이다
      최장일치     성적이 | 의신청       앞에서부터 긴 것을 떼니 조사가 붙는다
      DF 가중      성적 | 이의 | 신청    흔한 낱말이 이긴다
  가설이 아니라 실측이다. 사전을 만들어 28건을 다시 돌려서 확인했다.

★ 넓히는 방향이므로 **확신을 낮춘다**
  분해는 검색을 넓히는 것이지 새 사실을 만드는 게 아니다.
  넓혀서 찾은 것은 넓히기 전 결과보다 뒤에 두고, 분해로만 찾았다는 걸 표시한다.
"""

from __future__ import annotations

import math
import re
import sqlite3

# 이보다 짧으면 붙여 쓴 게 아니라 그냥 낱말이다.
MIN_GLUED = 5
# 조각의 최소 길이. 한 글자는 아무 데나 걸린다.
MIN_PIECE = 2
# 한 조각의 최대 길이. 사전에 아주 긴 말이 있어도 통째로는 안 본다.
MAX_PIECE = 12
HANGUL = re.compile(r"^[가-힣]+$")


_CACHE: dict[str, tuple[int, dict[str, int]]] = {}


def load_vocab(conn: sqlite3.Connection) -> dict[str, int]:
    """term 표를 통째로 읽는다. **한 번만.**

    ★ 답변 경로는 조회만 한다. 만드는 건 크롤 단계다.
      표가 없으면 빈 사전을 돌려준다 — 분해가 안 될 뿐 답변은 그대로 나간다.

    ★ 매번 읽으면 7만 행이라 199ms 가 붙었다 (T7 상한은 300ms)
      프로세스마다 한 번만 읽고 들고 있는다. 사전이 다시 만들어지면
      built_at 이 바뀌므로 그걸로 갈아끼운다 — 크롤이 새 낱말을 넣었는데
      서버가 옛 사전을 들고 있으면 그게 조용한 어긋남이다.
    """
    try:
        row = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(built_at),'') FROM term").fetchone()
    except sqlite3.OperationalError:
        return {}
    key = f"{row[0]}|{row[1]}"
    hit = _CACHE.get(key)
    if hit is not None:
        return hit[1]
    vocab = {t: d for t, d in conn.execute("SELECT term, df FROM term")}
    _CACHE.clear()          # 옛 판은 버린다. 두 판을 들고 있을 이유가 없다.
    _CACHE[key] = (row[0], vocab)
    return vocab


def split(token: str, vocab: dict[str, int]) -> list[str] | None:
    """붙여 쓴 토큰 → 낱말들. 쪼갤 이유가 없으면 None.

    ★ 사전에 있는 말은 안 쪼갠다
      '졸업요건' 은 그 자체가 코퍼스에 있는 말이다. 쪼개면 오히려 넓어져서
      엉뚱한 게 붙는다. 통째로 있는 말은 통째로 둔다.
    """
    if not token or len(token) < MIN_GLUED or not vocab:
        return None
    if token in vocab:
        return None                      # 통째로 있는 말은 건드리지 않는다
    if not HANGUL.match(token):
        return None                      # 영문·숫자 섞인 것은 다른 문제다

    n = len(token)
    total = max(sum(vocab.values()), len(vocab), 2)
    best: list[tuple[float, int | None]] = [(-1e18, None)] * (n + 1)
    best[0] = (0.0, None)
    for i in range(1, n + 1):
        for j in range(max(0, i - MAX_PIECE), i):
            if i - j < MIN_PIECE:
                continue
            d = vocab.get(token[j:i])
            if not d:
                continue
            # ★ **드문 조각**을 선호한다 (IDF). 흔한 조각이 아니다.
            #   흔한 쪽을 선호했더니 '이의신청'(DF 10) 이 사전에 있는데도
            #   '이의'(46) + '신청'(1,134) 로 쪼갰다 — 9.2 vs 21.7 로 졌다.
            #   그러면 띄어 쓴 경우와 **토큰이 달라져서** 다른 검색이 된다.
            #       띄어 씀   ['성적', '이의신청']   → found
            #       쪼갬      ['성적','이의','신청'] → '신청' 이 흔해 순위 밖
            #   검색에서 값진 건 드문 낱말이다. 우리 _weights 도 IDF 를 쓴다.
            #   ★ 조사 문제는 IDF 로도 안전하다 — '의신청' 은 DF 0 이라
            #     애초에 후보가 아니고, '의' 는 두 글자 미만이라 안 잡힌다.
            score = best[j][0] + math.log(total / d) * (i - j)
            if score > best[i][0]:
                best[i] = (score, j)
    if best[n][1] is None:
        return None                      # 전부 사전 낱말로는 못 덮는다

    out, i = [], n
    while i > 0:
        j = best[i][1]
        if j is None:
            return None
        out.append(token[j:i])
        i = j
    out.reverse()
    return out if len(out) > 1 else None
