"""되묻기 — 답이 여럿일 때 문서가 갈라 놓은 대로 물어본다.

★ 되묻기는 확신 오답을 만들 수 없다
  선택지를 **문서 제목에서 그대로** 가져온다. 우리가 문장을 안 지어내는 것과
  같은 이유로 없는 선택지를 만들어낼 수 없다.
  최악은 '관련 없는 선택지가 섞임' 이고 그건 짜증이지 오답이 아니다.
  학생이 잘못된 정보를 갖고 가지는 않는다.

★ 상태를 만들지 않는다
  버튼이 새 발화('군입대 휴학')를 보내게 한다. 기존 경로가 그대로 처리한다.
  대화 문맥을 들고 있을 필요가 없다.
  '한 대화에 두 번 안 되묻기' 도 저절로 지켜진다 — 두 번째 발화에는
  한정어가 들어 있어서 already_narrowed 가 걸린다.

★ 선택지는 **핵심어를 공유하는 형제**만
  우리가 이미 쓰는 '핵심어가 제목·첫머리에 있으면 올린다' 를 형제 판정으로 옮긴 것이다.

      '휴학'     ✓ 일반 휴학 · 군입대 휴학 · 임신ㆍ출산ㆍ육아 휴학 · 창업휴학 · 휴학 절차
                 ✗ 개 념 · 통산횟수 · 특기사항

★ 자족성은 걸지 않는다 — 재보고 뺐다
  자족성을 함께 걸었더니 되묻기 자리가 46문항 전수에서 **0건**이 됐다.
      일반 휴학  자족=False  [이름뿐 (5자 2어절)]
  자족성은 '인용문이 혼자 뜻이 서는가' 를 재는 잣대다. 라벨은 짧은 게 정상이다.
  **인용은 길어야 뜻이 서고 라벨은 짧아야 읽힌다** — 요구가 반대라 같은 잣대를 못 쓴다.

★ '단일 주제 페이지면 모든 d0 를 갈래로' 를 재보고 **안 넣었다**
  규칙: 질문 핵심어를 제목에 가진 d0 가 0개면 단일 주제 → 전부 갈래.
  '휴학 / 복학' 반례를 원리적으로 피하는 좋은 발상이었는데, 재보니 실효가 얇았다.

    막힌 19건 중 단일 주제 판정 7건 · 그중 선택지 2개 이상은 4건
    폭발은 없었다 (최대 8개)

  1. A+ 는 **후퇴**다. 지금 이미 답을 받고 있다.
         등급 | 평점 | 비고(100점 만점 기준)
         A+ | 4.5 | 95 ~ 100          ← 이게 답이다
     이걸 버리고 버튼 8개를 내미는 건 학생을 한 번 더 두드리는 것이다.
  2. 전과는 **안 열린다**. 페이지 제목이 '전학 / 전과' 라 다주제 판정인데,
     실제로는 6개 d0 가 전부 전과 절차의 단계다. 규칙이 여기서 틀린다.
  3. 남는 실효는 이의신청·자퇴 정도 2건이고, 학점인정은 '정의 · 업무 흐름도' 라 약하다.

★ 그리고 지금 자로는 1번을 못 잡는다 — 기록해 둔다
  후퇴 판정이 'off 가 확신일 때' 만 본다. 그런데 '문서+발췌' 인데 인용이 이미
  답을 담은 경우도 후퇴다. must 가 주제어('휴학')인지 답값('4.5')인지
  섞여 있어서 갈라지지 않는다. 되묻기를 넓히려면 이 자부터 고쳐야 한다.

★ 형제는 parent_key 가 아니라 **최상위 블록**이다
  parent_key 형제로 봤다가 한 번 틀렸다 — 표의 행·조항 번호가 나왔다.
      기숙사 통금 → 13 / 12 / 11 / 10
  휴학 갈래는 같은 페이지의 depth 0 블록이다.
"""

from __future__ import annotations

import re

# 카카오 quickReplies 상한
MAX_OPTIONS = 10
# 하나뿐이면 되물을 게 없다. 그건 '못 집은 것' 이지 '안 정해진 것' 이 아니다.
MIN_OPTIONS = 2
# 선택지로 삼을 제목의 길이 상한.
# ★ 24 로 두었더니 '복학' 이 통째로 막혔다 — 문서가 갈래를 한 제목에 나열한다.
#     '일반복학, 임신·출산·육아 복학, 창업복학, 질병복학(학부생)'  34자 → 탈락
#     남은 게 '복학 절차' 하나뿐이라 MIN_OPTIONS 미달로 되묻기 자체가 안 됐다.
#   버튼에 잘려 보이는 건 render_clarify 가 화면 라벨만 줄여서 이미 처리한다.
#   **보내는 말은 안 줄이므로** 길어도 검색은 온전하다. 여기서 막을 이유가 없다.
MAX_LABEL = 48


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def top_blocks(conn, page_url: str) -> list[str]:
    """그 페이지의 최상위 블록 제목들 (문서가 갈라 놓은 단위)."""
    return [r["path"] or "" for r in conn.execute(
        """SELECT path FROM page_section
            WHERE page_url = ? AND parent_key IS NULL
            ORDER BY ordinal""", (page_url,))]


def _split_listed(label: str) -> list[str]:
    """제목이 쉼표로 갈래를 나열하면 쪼갠다.

    ★ 문서가 이미 갈라 놓은 것을 우리가 붙여 놓고 있었다
        '일반복학, 임신·출산·육아 복학, 창업복학, 질병복학(학부생)'
      이걸 버튼 하나로 주면 눌러도 못 찾는다 — 검색어로는 안 먹힌다.
      쪼개면 각각 찾힌다 (실측 3/4). 쪼개는 근거는 **원문의 쉼표**다.
    """
    if "," not in label:
        return [label]
    parts = [x.strip(" ,·") for x in label.split(",")]
    parts = [x for x in parts if 2 <= len(x) <= MAX_LABEL]
    return parts if len(parts) >= 2 else [label]


def options(conn, page_url: str, tokens: list[str]) -> list[str]:
    """되물을 선택지. 2개 미만이면 빈 목록 (= 되묻지 않는다)."""
    if not tokens:
        return []
    out: list[str] = []
    seen: set[str] = set()
    raw: list[str] = []
    for path in top_blocks(conn, page_url):
        raw.extend(_split_listed(path.split(">")[-1].strip()))
    for label in raw:
        if not (2 <= len(label) <= MAX_LABEL):
            continue
        if not any(t in label for t in tokens):
            continue
        key = _norm(label)
        if key in seen:
            # 같은 제목이 여러 번 나온다 ('등록금반환' 이 4번).
            # 같은 버튼을 네 개 보여주는 건 선택지가 아니다.
            continue
        seen.add(key)
        out.append(label)
        if len(out) >= MAX_OPTIONS:
            break
    return out if len(out) >= MIN_OPTIONS else []


def exact_block(conn, page_url: str, utterance: str) -> dict | None:
    """제목이 정확히 일치하는 최상위 블록.

    ★ 순위 문제가 아니라 **색인 문제**였다
      학생이 '일반 휴학' 을 골랐는데 '휴학 절차 > 휴학일자 입력 방법' 표가 나왔다.
      같은 페이지에 '일반 휴학' 123자 블록이 있는데도 그랬다.
      재보니 11개 선택지 전부에서 제목 일치 블록이 **순위 밖**이었다 —
      검색은 is_leaf=1 인 잎만 색인하고 최상위 블록은 is_leaf=0 이라
      애초에 후보에 없었다. 순위를 고쳐서 될 일이 아니다.

    ★ 이건 랭킹 우회가 아니라 **다른 질문**이다
      '휴학' 은 찾아 달라는 말이고, '일반 휴학' 은 그 제목의 것을 달라는 말이다.
      학생이 우리가 보여준 제목을 그대로 고른 것이므로 추론이 없다.
      확신 오답이 날 수 없다 — 우리가 고른 게 아니라 학생이 고른 제목이다.
    """
    want = _norm(utterance)
    if not want:
        return None
    for r in conn.execute(
            """SELECT section_key, path, raw_text, text FROM page_section
                WHERE page_url = ? AND parent_key IS NULL ORDER BY ordinal""",
            (page_url,)):
        if _norm((r["path"] or "").split(">")[-1]) == want:
            return {"section_key": r["section_key"], "path": r["path"] or "",
                    "text": (r["raw_text"] or r["text"] or "").strip()}
    return None


def already_narrowed(utterance: str, labels: list[str],
                     tokens: list[str] | None = None) -> str | None:
    """질문에 이미 한정어가 있나. 있으면 되묻지 않는다.

    '군입대 휴학 어떻게 해' 는 이미 골랐다. 되물으면 학생을 두 번 일하게 한다.
    ★ 이게 '한 대화에 두 번 안 되묻기' 도 겸한다 —
      버튼을 누르면 라벨이 그대로 발화로 오므로 여기서 걸린다.

    ★ 라벨이 핵심어 그 자체면 아무것도 못 가른다
      '시험 언제' 가 라벨 '시험' 에 걸려 '이미 정해짐' 이 됐다.
      '시험 / 조기시험' 중에 아무것도 안 고른 질문인데도 그랬다.
      한정어가 없는 라벨은 판정에서 뺀다.
    """
    u = _norm(utterance)
    bare = {_norm(t) for t in (tokens or [])}
    for lab in labels:
        n = _norm(lab)
        if not n or n not in u:
            continue
        # ★ 한정어 유무는 **라벨 자체**로 본다. 질의 토큰으로 빼면 안 된다 —
        #   학생이 '군입대 휴학' 이라고 치면 '군입대' 도 토큰이 되고,
        #   라벨에서 토큰을 빼면 비어버려 '한정어 없음' 으로 오판한다.
        #   라벨이 낱말 하나와 정확히 같을 때만 '가르는 말이 없다' 고 본다.
        if n in bare:
            continue
        return lab
    return None


def qualifier(label: str, tokens: list[str]) -> str:
    """라벨에서 질문의 핵심어를 뺀 나머지 — 그게 이 선택지를 가르는 말이다.

    '군입대 휴학' 에서 '휴학' 을 빼면 '군입대'.
    질문이 이미 그 말을 담고 있으면 되물을 이유가 없다.
    """
    s = label
    for t in sorted(tokens, key=len, reverse=True):
        s = s.replace(t, " ")
    return re.sub(r"\s+", " ", s).strip()


def narrowed_by_qualifier(utterance: str, labels: list[str],
                          tokens: list[str]) -> str | None:
    """한정어만으로도 이미 정해졌는지 본다 ('군휴학' 처럼 붙여 쓴 경우)."""
    u = _norm(utterance)
    for lab in labels:
        q = _norm(qualifier(lab, tokens))
        if len(q) >= 2 and q in u:
            return lab
    return None
