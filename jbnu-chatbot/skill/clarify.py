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
# 버튼에 들어가는 길이. 넘으면 잘려서 무슨 말인지 모르게 된다.
MAX_LABEL = 24


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def top_blocks(conn, page_url: str) -> list[str]:
    """그 페이지의 최상위 블록 제목들 (문서가 갈라 놓은 단위)."""
    return [r["path"] or "" for r in conn.execute(
        """SELECT path FROM page_section
            WHERE page_url = ? AND parent_key IS NULL
            ORDER BY ordinal""", (page_url,))]


def options(conn, page_url: str, tokens: list[str]) -> list[str]:
    """되물을 선택지. 2개 미만이면 빈 목록 (= 되묻지 않는다)."""
    if not tokens:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for path in top_blocks(conn, page_url):
        label = path.split(">")[-1].strip()
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
