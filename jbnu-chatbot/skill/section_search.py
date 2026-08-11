"""안내 페이지 섹션 검색 — 잎으로 찾고 부모를 인용한다.

★ 요약하지 않는다
  찾은 문단을 **그대로** 옮긴다. 규정 요약은 특히 위험하다.
  길면 앞부분 + 전문 링크로 자르되, 잘랐다는 사실을 반드시 표시한다.

★ 답에 질문 대상을 넣는다
  경로(교내 장학금 > 금액별 분류)를 함께 낸다.
  답에 질문 대상이 없으면 학생이 '내 질문에 답한 게 맞나' 를 판단할 수 없고,
  판단할 수 없는 답은 틀린 답과 구별되지 않는다.

★ 애매하면 고르지 않는다
  비슷한 후보가 여럿이면 하나를 찍지 말고 선택지를 보여준다.
  찍는 것은 추론이고, 추론으로 고른 인용은 관련 없는 문서를 사실처럼 보이게 한다.

★ '없다' 를 가른다
  NO_QUERY   무엇을 묻는지 못 알아들었다
  NO_DATA    아직 수집한 페이지가 없다      ← 우리 잘못
  NOT_FOUND  찾아봤는데 그런 내용이 없다    ← 조회는 했다
  이 셋을 뭉치면 고칠 수 없다.
"""

from __future__ import annotations

import functools
import math
import pathlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Sequence

from skill import selfcontained

# 질문을 이루지만 내용은 없는 말. 이것만 남으면 무엇을 묻는지 모르는 것이다.
STOPWORDS = {
    "뭐야", "뭐", "머야", "알려줘", "알려", "어떻게", "어떡해", "언제", "어디",
    "얼마", "얼마나", "누구", "왜", "무엇", "무슨", "어떤", "인가요", "인가",
    "있나요", "있어", "없어", "해줘", "해", "하나요", "하는지", "하려면", "되나요",
    "돼", "되나", "좀", "제발", "궁금", "궁금해", "질문", "문의", "정보", "안내",
    "방법", "관련", "대해", "대한", "그리고", "근데", "혹시", "전북대", "전북대학교",
    "우리학교", "학교", "챗봇",
    # 인사말은 검색어가 아니다. 이걸 검색하면 총장 연설문이 나온다 — 실제로 그랬다.
    "안녕", "안녕하세요", "안녕하십니까", "하이", "반가워", "고마워", "감사합니다",
    "잘가", "수고", "ㅎㅇ", "ㅋㅋ", "ㅎㅎ",
}
# 뒤에 붙어 뜻을 바꾸지 않는 조사·어미. 3글자 이상일 때만 떼어 본다.
# '자퇴하려면' 에서 '자퇴' 를 얻지 못하면 있는 페이지를 못 찾는다.
JOSA = ("하려면", "하려고", "하고싶어", "하는법", "합니까", "인가요", "이야",
        "예요", "이에요", "하나요", "할까요", "해야해", "해야", "하기",
        "으로부터", "에서부터", "이라고", "라고", "으로", "에서", "부터", "까지",
        "에게", "한테", "이랑", "랑", "은", "는", "이", "가", "을", "를", "의",
        "도", "만", "와", "과", "로", "에", "야")

MIN_TOKEN_LEN = 2
MAX_TOKENS = 8
# 이 점수 아래면 답하지 않는다. 억지로 맞추면 관련 없는 규정을 사실처럼 보여준다.
MIN_SCORE = 1.2
# 1등과 이만큼 가까우면 경쟁 후보로 본다.
# ★ 위험한 것은 '섹션을 잘못 고르는 것' 이 아니라 '학과를 잘못 고르는 것' 이다.
#   같은 사이트 안의 후보들은 어차피 같은 문서군이고 링크로 확인할 수 있다.
#   다른 학과의 규정을 내밀면 학생이 그 사실을 알 방법이 없다.
#   그래서 사이트가 갈릴 때만 답을 보류한다.
AMBIGUOUS_RATIO = 0.70
MAX_CANDIDATES = 5
PATH_BOOST = 1.6          # 경로에서 맞으면 본문보다 무겁게 본다
# ★ 문서 제목은 **페이지 순위에만** 쓴다. 섹션 선택에는 쓰지 않는다.
#   한 페이지의 모든 섹션이 제목 점수를 똑같이 받으면, 그 안에서 엉뚱한 문단이
#   1등이 된다 — 처음에 그렇게 넣었다가 확신 오답이 0 → 2 로 늘었다.
#   '어느 문서인가' 와 '그 안 어디인가' 는 다른 질문이고, 다른 근거를 쓴다.
TITLE_BOOST = 2.4
# 섹션 1등이 2등보다 이만큼 앞서야 그 섹션을 인용한다.
# 못 미치면 고르지 않고 **페이지 단위로** 답한다 —
# 틀린 문단을 확신 있게 인용하는 것보다 맞는 페이지를 통째로 보여주는 게 낫다.
SECTION_MARGIN = 1.5
# ★ 후보는 **드문 낱말**로 뽑는다. 순위는 전체 낱말로 매긴다.
#   '복학 신청' 에서 '신청' 은 4.2% 섹션에 나온다. 그걸로 후보를 뽑으면
#   목록이 넘쳐서 정답이 잘린다 — 실제로 '휴학 / 복학' 페이지에
#   '복학' 이 든 잎이 22개인데 후보 600개에 **0개**였다.
#   흔한 낱말은 후보를 넓히기만 하고 변별력이 없다.
PROBE_DF_MAX = 0.02
# 후보가 잘렸으면 섹션을 찍는 문턱을 높인다. 확신의 근거가 한 조각 빠졌으므로.
# ★ 다만 **잘림 여부만 보면 안 된다.**
#   정답이 후보 앞쪽에 있었다면 뒤가 잘린 것과 상관이 없다.
#   실측(46문항): 잘린 5문항의 정답 순위가 6·13·13·27·33 등이었다.
#   상한 600 대비 최악 5.5% 지점이다. 그걸 '확신 못 함' 으로 강등하면
#   가장 많이 묻는 질문일수록 뭉뚱그린 답을 받게 된다 —
#   안전해 보이지만 가장 아픈 곳에서 성능이 깎인다.
TRUNCATED_MARGIN_FACTOR = 2.0
# 정답이 후보의 앞 이 비율 안에 있으면 잘림을 무시한다
TRUNCATION_SAFE_DEPTH = 0.25
# ★ 길이 정규화를 넣어 봤다가 **되돌렸다**.
#   진단은 맞았다 — 섹션 165개짜리 '수강신청(학부/대학원)' 이 복학·전과·휴학
#   질문에서 전부 1등이었고, 전체 중앙값은 8개다. 커서 이긴 것이다.
#   그런데 페이지 점수를 잎 수로 나누니 0.6 / 1.2 / 2.0 어느 강도에서도
#   정확도가 떨어지고 확신 오답이 1건 생겼다 (80%/0 → 78%/1 → 76%/1 → 72%/1).
#   큰 페이지에는 진짜 답도 많이 들어 있어서, 크기로 벌점을 주면 그것까지 죽는다.
#   길이가 아니라 **주제 일치**로 갈라야 한다. 다음 시도의 방향이다.
PATH_ALL_BOOST = 2.2      # 질문의 낱말이 경로에 **전부** 있으면 그게 그 문서다
# 낱말이 멀리 떨어져 있으면 같은 이야기가 아니다.
# '휴학' 과 '신청' 이 한 문단 양 끝에 있다고 휴학 신청 안내는 아니다.
PROXIMITY_WINDOW = 60
PROXIMITY_PENALTY = 0.55
# 표의 한 칸에 낱말이 스쳐 지나갈 뿐인 경우.
# '기숙사는 본인이 신청' 이 학술교류 협정대학 표 안에 있다고
# 그 표가 기숙사 안내인 것은 아니다. 표는 제목이 주제를 정한다.
TABLE_OFFTOPIC_PENALTY = 0.35
# 학과 사이트가 이만큼 동시에 걸리면 그 주제는 학과마다 다른 것이다
DEPT_SPECIFIC_SITES = 4
# 문서의 주제가 드러나는 자리 — 제목과 첫머리
TOPIC_ZONE_CHARS = 140
# 핵심 낱말의 이 비율 이상 무게를 가진 낱말은 함께 필수로 본다
CORE_MARGIN = 0.7
# 동의어로 맞은 것은 원래 낱말로 맞은 것보다 근거가 한 단계 약하다
SYNONYM_DISCOUNT = 0.85
# 이 비율 이상의 섹션에 나오는 낱말은 '군더더기' 로 본다.
# '규정' '방법' '절차' 는 질문을 이루지만 주제를 좁히지 않는다.
# 목록을 코드에 박지 않고 **실제 출현 수**로 가린다 — 사이트가 바뀌면 값도 바뀐다.
WEAK_TOKEN_DF = 0.03
# 사이트 이름이 질문의 말을 담고 있으면 그 사이트가 주제의 주인이다
SITE_TOPIC_BOOST = 1.4
# 학과를 안 밝힌 질문에는 본부 문서가 답이다. 학과 페이지 205곳이 표를 갈라
# 정작 규정 원문이 밀려나는 것을 막는다.
HQ_HOST = "www.jbnu.ac.kr"
HQ_BOOST = 2.0
_CFG = pathlib.Path(__file__).resolve().parents[1] / "config"
SITES_PATH = _CFG / "sites.yaml"
ALIASES_PATH = _CFG / "site_aliases.yaml"
SYNONYMS_PATH = _CFG / "title_synonyms.yaml"


@functools.lru_cache(maxsize=1)
def site_names() -> dict[str, str]:
    """호스트 → 학과·기관 이름. 없으면 빈 사전 (기능이 꺼질 뿐 죽지 않는다)."""
    try:
        import yaml
        doc = yaml.safe_load(SITES_PATH.read_text(encoding="utf-8"))
        return {h: v["name"] for h, v in (doc.get("sites") or {}).items()
                if v.get("name")}
    except Exception:  # noqa: BLE001
        return {}


@functools.lru_cache(maxsize=1)
def site_aliases() -> dict[str, str]:
    """별칭 → 호스트. 사이트 이름은 학교 말이고 질문은 학생 말이다.

    '기숙사' 라고 묻는데 사이트 이름은 '생활관' 이라 글자가 하나도 안 겹친다.
    그래서 본부 문서가 이기고 정작 생활관 안내가 밀렸다.
    """
    try:
        import yaml
        doc = yaml.safe_load(ALIASES_PATH.read_text(encoding="utf-8"))
        out: dict[str, str] = {}
        for host, names in (doc.get("aliases") or {}).items():
            for n in names or []:
                out[str(n).strip()] = host
        return out
    except Exception:  # noqa: BLE001
        return {}


@functools.lru_cache(maxsize=1)
def title_synonyms() -> dict[str, frozenset[str]]:
    """제목 동의어 — 학과마다 같은 것을 다르게 부른다.

    '졸업요건' 을 '졸업기준' 이라 쓰는 학과가 8곳이다. 손으로 적은 목록이 아니라
    crawler/synonyms.py 가 관측으로 뽑은 것이다 (어휘·상보분포·본문 겹침).

    ★ 이건 **검색 확장**이지 사실 결합이 아니다.
      후보를 넓힐 뿐이고, 인용은 여전히 원문 그대로다.
      넓힌 뒤에도 제목·첫머리 검증은 그대로 통과해야 답한다.
    """
    try:
        import yaml
        doc = yaml.safe_load(SYNONYMS_PATH.read_text(encoding="utf-8")) or {}
        off = set(doc.get("disabled") or [])
        out: dict[str, set[str]] = {}
        for g in doc.get("groups") or []:
            if g.get("label") in off:
                continue          # 사람이 끈 묶음은 쓰지 않는다
            members = [m for m in g.get("members") or [] if m]
            for m in members:
                out.setdefault(m, set()).update(members)
        return {k: frozenset(v) for k, v in out.items()}
    except Exception:  # noqa: BLE001
        return {}


def expand_token(token: str) -> frozenset[str]:
    """이 낱말과 같은 자리를 뜻하는 다른 표현들."""
    syn = title_synonyms()
    if token in syn:
        return syn[token]
    # 제목이 통째로 안 맞아도, 질문 낱말을 품은 제목이면 그 그룹을 쓴다
    #   '졸업요건' 질의 ↔ '학번별졸업요건' 제목
    for key, group in syn.items():
        if len(token) >= 3 and token in key:
            return group
    return frozenset()


def match_site(utterance: str) -> tuple[str | None, str]:
    """질문에 학과·기관 이름이 들어 있으면 그 사이트로 좁힌다.

    '기계공학과 교수' 는 기계공학과 사이트를 봐야 한다. 이름을 안 쓰면
    '기계공학' 이라는 글자가 든 아무 페이지나 올라온다 — 실제로 그랬다.
    가장 **긴** 이름을 고른다. '공학과' 가 '기계공학과' 를 이기면 안 된다.
    """
    best_host, best_name = None, ""
    for host, name in site_names().items():
        if host == HQ_HOST or len(name) < 3:
            continue
        if name in utterance and len(name) > len(best_name):
            best_host, best_name = host, name
    # 별칭도 본다. 정식 이름보다 짧아도 학생이 실제로 쓰는 말이다.
    for alias, host in site_aliases().items():
        if len(alias) >= 2 and alias in utterance and len(alias) > len(best_name):
            best_host = host
            best_name = site_names().get(host, alias)
    return best_host, best_name


# 개인 기록 조회 — 크롤로는 영원히 못 넘는 벽이다.
# 비슷한 낱말이 든 규정을 인용하면 학생은 자기 성적을 물었는데 학칙을 받는다.
PERSONAL_MARKERS = ("내 ", "제 ", "나의", "본인", "저의", "내가")
PERSONAL_TOPICS = ("성적", "학점", "수강신청", "장학금", "등록금", "출결",
                   "졸업", "이수", "고지서", "시간표")


def is_personal_lookup(utterance: str) -> bool:
    u = (utterance or "").strip()
    return (any(u.startswith(m) or f" {m.strip()} " in f" {u} "
                for m in PERSONAL_MARKERS)
            and any(t in u for t in PERSONAL_TOPICS))


class Outcome(str, Enum):
    FOUND = "found"
    PERSONAL = "personal"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    NO_DATA = "no_data"
    NO_QUERY = "no_query"


@dataclass
class Hit:
    section_key: str
    page_url: str
    page_title: str
    path: str
    text: str                 # 색인 단위 — 매칭된 잎
    quote_key: str | None
    host: str = ""
    site_name: str = ""       # 어느 학과·기관 사이트인가
    quote_text: str = ""      # 인용 단위 — 부모 블록·표 전체
    quote_path: str = ""      # 인용 블록의 경로 — 화면에 보여줄 것
    page_score: float = 0.0   # 페이지 순위용 (제목 가산 포함)
    page_modified: str | None = None
    observed_at: str = ""
    score: float = 0.0
    matched: list[str] = field(default_factory=list)


@dataclass
class SearchResult:
    outcome: Outcome
    query_tokens: list[str] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    searched_sections: int = 0
    site_host: str | None = None      # 질문이 특정 학과를 가리켰나
    site_name: str = ""
    missing_tokens: list[str] = field(default_factory=list)  # 못 찾은 낱말
    via_synonym: str = ""     # 다른 이름으로 찾았으면 그 이름 (답에 밝힌다)
    page_level: bool = False  # 섹션을 못 고르겠어서 페이지 단위로 답한다
    section_margin: float = 0.0
    # ★ 후보가 상한에 닿았나. 잘린 상태에서 나온 1등은 안 잘린 1등과
    #   같은 확신을 가질 수 없다 — 더 나은 답이 잘려 나갔을 수 있으므로.
    candidates_truncated: bool = False
    candidates_matched: int = 0
    candidates_returned: int = 0
    answer_depth: int = 0     # 답이 후보 목록에서 몇 번째였나 (잘림의 영향 판단)
    defer_reason: str = ""    # 왜 보류했나 — 진단용. 조용히 접으면 못 고친다

    @property
    def top(self) -> Hit | None:
        return self.hits[0] if self.hits else None

    @property
    def subject(self) -> str:
        """답문에 넣을 '질문 대상'. 없으면 빈 문자열."""
        return " ".join(self.query_tokens[:3])


def _strip_josa(token: str) -> str:
    if len(token) < 3:
        return token
    for j in JOSA:
        if token.endswith(j) and len(token) - len(j) >= MIN_TOKEN_LEN:
            return token[: -len(j)]
    return token


def tokenize(utterance: str) -> list[str]:
    """형태소 분석기 없이, 설명 가능한 규칙으로만 자른다.

    조사를 떼되 2글자 밑으로는 내려가지 않는다. 너무 짧은 토큰은
    아무 데나 걸려서 관련 없는 문단을 끌고 온다.
    """
    # 'A+' 같은 등급 표기가 살아남아야 한다. 쪼개면 표에 있는 답을 못 찾는다.
    raw = [w.strip("+") if w.strip("+").isdigit() else w
           for w in re.split(r"[^0-9A-Za-z가-힣+]+", utterance or "")]
    out: list[str] = []
    for w in raw:
        if not w or w in STOPWORDS:
            continue
        t = _strip_josa(w)
        if t in STOPWORDS:
            continue
        # 'A+' 처럼 짧아도 뜻이 분명한 것은 살린다
        if len(t) < MIN_TOKEN_LEN and not re.fullmatch(r"[A-Za-z][+-]", t):
            continue
        if t not in out:
            out.append(t)
    return out[:MAX_TOKENS]


def _weights(tokens: Sequence[str], total: int, df: dict[str, int]) -> dict[str, float]:
    """흔한 말은 가볍게, 드문 말은 무겁게. 근거는 실제 출현 수다."""
    w = {}
    for t in tokens:
        n = max(df.get(t, 0), 1)
        w[t] = math.log((total + 1) / n) * min(len(t) / 2.0, 2.0)
    return w


# 메뉴·목차 라벨 — 답이 아니라 이름표다.
# '증명서발급' 한 낱말이 '증명서 발급' 질문에 가장 잘 맞아 목차가 1등이 됐다.
#
# ★ 이 규칙(12자·공백 없음·숫자 없음)은 같은 생각의 약한 판본이었다
#   '사유 발생시'(공백 있음) · '월간 일정' · '가. 사회봉사' 를 못 잡았고,
#   그것들이 1등이 되어 학생에게 조각을 보여줬다.
#   잣대를 selfcontained 로 합친다 — 인용과 랭킹이 같은 기준을 써야 한다.
LABEL_MAX_CHARS = 12

# 조각은 버리지 않고 **감점**한다.
# ★ 버리면 그 페이지를 아예 못 찾을 수 있다.
#   조각뿐인 페이지도 '이 문서에 있어요' 로는 답이 된다.
#   원칙 그대로다 — 섹션을 못 고르겠으면 고르지 말고 페이지로.
FRAGMENT_PENALTY = 0.5


def is_label(text: str) -> bool:
    """혼자 떼어놨을 때 뜻이 안 서는 말인가. selfcontained 의 반대."""
    return not selfcontained.is_self_contained(text)


def score_rows(rows: list[dict[str, Any]], tokens: Sequence[str],
               weights: dict[str, float], *, hq_boost: bool = True,
               expand: dict[str, frozenset[str]] | None = None) -> list[Hit]:
    hits: list[Hit] = []
    for r in rows:
        text = r.get("text") or ""
        path = r.get("path") or ""
        fragment = is_label(text)
        matched, s = [], 0.0
        for t in tokens:
            forms = [t] + sorted((expand or {}).get(t, ()))
            in_text = any(f in text for f in forms)
            in_path = any(f in path for f in forms)
            if not (in_text or in_path):
                continue
            matched.append(t)
            # 동의어로 맞은 것은 원래 낱말로 맞은 것보다 조금 가볍게 본다
            same = (t in text) or (t in path)
            s += (weights.get(t, 0.0) * (PATH_BOOST if in_path else 1.0)
                  * (1.0 if same else SYNONYM_DISCOUNT))
        if not matched:
            continue
        # 질문의 여러 낱말이 한 문단에 같이 나오면 그만큼 더 맞는 답이다
        s *= 1.0 + 0.35 * (len(matched) - 1)

        # 경로에 전부 들어 있으면 그 문서의 제목이 곧 질문이다
        if len(matched) > 1 and all(t in path for t in matched):
            s *= PATH_ALL_BOOST
        elif len(matched) > 1:
            # 본문에서만 맞았다면 낱말끼리 얼마나 붙어 있는지 본다
            pos = [text.find(t) for t in matched if t in text]
            if len(pos) > 1 and (max(pos) - min(pos)) > PROXIMITY_WINDOW:
                s *= PROXIMITY_PENALTY
        # 표 행인데 표 제목에 질문의 말이 하나도 없으면 스쳐 지나간 것이다
        if r.get("kind") == "table_row" and not any(t in path for t in matched):
            s *= TABLE_OFFTOPIC_PENALTY
        # ★ 혼자서 뜻이 안 서는 조각은 1등이 될 수 없다.
        #   길이가 아니라 자족성이다 — '준공년도 : 1995년'(11자)은 살고
        #   '여성건강간호학교실'(9자)은 죽는다. 길이 정규화가 세 번 진 이유다.
        if fragment:
            s *= FRAGMENT_PENALTY

        # ★ 제목 가산은 **페이지 점수**에만 얹는다. 섹션 점수(s)는 건드리지 않는다.
        title = r.get("page_title") or ""
        title_hit = any(any(f in title for f in [t, *sorted((expand or {}).get(t, ()))])
                        for t in tokens)
        host = r.get("host") or ""
        if hq_boost and host == HQ_HOST:
            s *= HQ_BOOST
        # '취업 상담' 이면 '취업진로지원과' 가 '항공우주공학과' 보다 맞다.
        sname = site_names().get(host, "")
        topical = (sname and any(t in sname for t in matched)) or any(
            site_aliases().get(t) == host for t in matched)
        if topical:
            s *= SITE_TOPIC_BOOST
        hits.append(Hit(
            section_key=r["section_key"], page_url=r["page_url"], host=host,
            site_name=site_names().get(host, ""),
            page_title=r.get("page_title") or "", path=path, text=text,
            quote_key=r.get("quote_key"), page_modified=r.get("page_modified"),
            observed_at=r.get("observed_at") or "", score=round(s, 3),
            page_score=round(s * (TITLE_BOOST if title_hit else 1.0), 3),
            matched=matched))
    hits.sort(key=lambda h: (-h.score, len(h.text)))
    return hits


def rank_pages(hits: list[Hit]) -> list[tuple[Hit, list[Hit], float]]:
    """★ 두 단계로 나눈다.

      1. 어느 **문서**인가 — 제목까지 근거로 쓴다 (page_score)
      2. 그 안 **어디**인가 — 본문·경로만 근거로 쓴다 (score)

    두 질문은 다르고, 다른 근거를 쓴다. 섞으면 한 페이지의 모든 섹션이
    제목 점수를 똑같이 받아 그 안에서 엉뚱한 문단이 1등이 된다.

    돌려주는 것: (대표 섹션, 그 페이지의 섹션들, 1등/2등 점수비)
    """
    by_page: dict[str, list[Hit]] = {}
    for h in hits:
        by_page.setdefault(h.page_url, []).append(h)

    out = []
    for url, secs in by_page.items():
        secs.sort(key=lambda h: (-h.score, len(h.text)))
        margin = (secs[0].score / secs[1].score
                  if len(secs) > 1 and secs[1].score > 0 else float("inf"))
        rep = secs[0]
        rep.page_score = max(h.page_score for h in secs)
        out.append((rep, secs, margin))
    out.sort(key=lambda x: (-x[0].page_score, len(x[0].text)))
    return out


def _dedupe_by_page(hits: list[Hit]) -> list[Hit]:
    """페이지마다 가장 잘 맞는 것 하나만. 같은 페이지를 여러 줄 보여줄 이유가 없다."""
    return [rep for rep, _, _ in rank_pages(hits)]


def search(conn, utterance: str, *, repo) -> SearchResult:
    """★ 동의어는 **대체 가능**이 아니라 **확장 후보**다.

    '졸업요건 ≡ 졸업기준' 은 같은 것이지만 '졸업요건 ≡ 졸업자격인증제' 는
    가까운 것일 뿐이다. 본문 겹침 점수는 이 둘을 잘 못 가른다.
    관련어를 동의어처럼 쓰면 '졸업요건' 을 물은 학생에게 다른 제도를 답하게 된다.

    그래서 두 번 돌린다.
      1차 — 질문 그대로. 여기서 찾으면 그대로 답한다.
      2차 — 못 찾았을 때만 동의어로 넓힌다. 그리고 **어느 이름에서 나왔는지
             답에 표시한다**. 그러면 관련어여도 학생이 스스로 판단할 수 있다.
    """
    if is_personal_lookup(utterance):
        # 우리는 로그인 뒤를 못 본다. 비슷한 규정을 내미는 것은 답이 아니다.
        return SearchResult(Outcome.PERSONAL)
    tokens = tokenize(utterance)
    if not tokens:
        return SearchResult(Outcome.NO_QUERY)

    first = _attempt(conn, utterance, tokens, repo=repo, expand={})
    if first.outcome is Outcome.FOUND:
        return first

    expand = {t: (expand_token(t) - {t}) for t in tokens}
    expand = {t: v for t, v in expand.items() if v}
    if not expand:
        return first
    second = _attempt(conn, utterance, tokens, repo=repo, expand=expand)
    if second.outcome is not Outcome.FOUND:
        return first          # 넓혀도 못 찾으면 원래 판정을 그대로 쓴다
    # 어느 이름으로 올라와 있었는지 밝힌다
    top = second.top
    other = ""
    for t, forms in expand.items():
        if t in (top.quote_path + top.page_title):
            continue
        hit = next((f for f in sorted(forms)
                    if f in (top.quote_path + " " + top.page_title)), "")
        if hit:
            other = hit
            break
    second.via_synonym = other
    return second


def _attempt(conn, utterance: str, tokens: list[str], *, repo,
             expand: dict[str, frozenset[str]]) -> SearchResult:

    total = repo.section_total(conn)
    if total == 0:
        # 조회할 것이 아예 없다. '없다' 가 아니라 '아직 안 긁었다' 다.
        return SearchResult(Outcome.NO_DATA, query_tokens=tokens)

    site_host, site_label = match_site(utterance)
    if site_host:
        # ★ 사이트를 가리킨 낱말은 **어디**를 정했지 **무엇**을 정하지 않았다.
        #   '기숙사 신청' 에서 '기숙사' 는 생활관 사이트를 뜻한다. 그 사이트 본문은
        #   스스로를 '생활관' 이라 부르므로 '기숙사' 를 계속 요구하면 영영 못 찾는다.
        used = {a for a, h in site_aliases().items() if h == site_host}
        used |= {site_label}
        narrowed = [t for t in tokens if not any(u and u in t for u in used)]
        if narrowed:          # 다 지워지면 원래 질의를 그대로 쓴다
            tokens = narrowed
    df = {t: repo.token_doc_freq(conn, t) for t in tokens}
    # ★ 질문의 뜻을 지고 있는 낱말 하나를 고른다.
    #   '재수강 규정' 의 뜻은 '재수강' 에 있지 '규정' 에 있지 않다.
    #   드문 정도만 보면 '등록금 납부 기간' 에서 '기간' 이 뽑힌다 —
    #   드물지만 아무 뜻도 없는 말이다. 그래서 점수 가중치를 그대로 쓴다
    #   (드문 정도 × 낱말 길이). 순위를 매길 때 믿는 값을 여기서도 믿는다.
    weights = _weights(tokens, total, df)
    core = max(tokens, key=lambda t: weights.get(t, 0.0))
    # ★ 무게가 엇비슷한 낱말은 **둘 다** 뜻을 지고 있다.
    #   '공연 일정' 에서 삼성문화회관 200페이지를 긁은 뒤 '공연' 이 흔해져
    #   '일정' 이 핵심으로 뽑혔고, 학사일정 문서가 답으로 나갔다.
    #   하나만 고르면 코퍼스가 바뀔 때마다 뜻이 바뀐다.
    top_w = weights.get(core, 0.0)
    required = [t for t in tokens
                if weights.get(t, 0.0) >= top_w * CORE_MARGIN]
    # 드문 낱말만으로 후보를 뽑는다. 하나도 없으면(전부 흔하면) 전부 쓴다.
    probe = [t for t in tokens if df.get(t, 0) <= total * PROBE_DF_MAX] or list(tokens)
    lookup = probe + sorted({a for t in probe for a in expand.get(t, ())})
    cand: dict = {}
    rows = repo.search_sections(conn, lookup, host=site_host, stats=cand)
    # 후보 목록에서의 자리 — 잘림이 이 답에 영향을 줬는지 판단하는 근거
    depth_of = {r["section_key"]: i for i, r in enumerate(rows)}
    scored = score_rows(rows, tokens, weights, hq_boost=site_host is None,
                        expand=expand)
    ranked = rank_pages(scored)
    hits = [rep for rep, _, _ in ranked]
    margin_of = {rep.page_url: m for rep, _, m in ranked}

    result = SearchResult(Outcome.NOT_FOUND, query_tokens=tokens,
                          searched_sections=total,
                          site_host=site_host, site_name=site_label)
    result.candidates_truncated = bool(cand.get("truncated"))
    result.candidates_matched = int(cand.get("matched") or 0)
    result.candidates_returned = int(cand.get("returned") or 0)
    if not hits or hits[0].score < MIN_SCORE:
        result.defer_reason = ("후보 없음" if not hits
                               else f"점수 미달 {hits[0].score:.1f} < {MIN_SCORE}")
        return result

    # 인용 단위를 붙인다 — 잎으로 찾고 부모를 인용한다
    for h in hits[:MAX_CANDIDATES]:
        q = repo.get_section(conn, h.quote_key) if h.quote_key else None
        h.quote_text = (q or {}).get("raw_text") or h.text
        # 경로는 **인용 블록의 것**을 쓴다. 잎의 경로는 마지막 칸이 문장 전체라
        # 화면에 문단이 두 번 나오고, 경로 구실을 못 한다.
        h.quote_path = (q or {}).get("path") or h.path

    top = hits[0]
    # ★ 계측은 판정과 무관하게 남긴다. 보류한 질문일수록 왜 그런지 알아야 한다.
    result.answer_depth = depth_of.get(top.section_key, len(rows)) + 1
    rivals = [h for h in hits[1:MAX_CANDIDATES]
              if h.score >= top.score * AMBIGUOUS_RATIO]
    result.hits = hits[:MAX_CANDIDATES]
    result.missing_tokens = [t for t in required if t not in top.matched]

    # ★ 긍정 단정에는 높은 근거를 요구한다.
    #   질문의 낱말 하나가 어디에도 안 맞았는데 확신하면 엉뚱한 문서를 답으로 준다.
    #   '기숙사 통금' 에서 '통금' 을 놓치고 학술교류 협정문을 내놓은 적이 있다.
    if result.missing_tokens:
        result.outcome = Outcome.AMBIGUOUS
        result.defer_reason = f"못 찾은 낱말 {result.missing_tokens}"
        return result

    # ★ 핵심 낱말이 **어디에** 있는지가 중요하다.
    #   제목이나 첫머리에 있으면 그 문서의 주제고, 본문 깊숙이 한 번 스치면
    #   남의 이야기다. '기숙사' 가 학술교류 협정대학 표 안쪽 셀에 있다고
    #   그 표가 기숙사 안내는 아니다. '분실물' 도 건지광장 규정 속에 있었다.
    #   ★ 잎 자신의 경로(top.path)는 쓰지 않는다 — 마지막 칸이 잎 텍스트라
    #     "내 제목에 내 말이 있다" 는 순환 논리가 된다. 부모 경로만 본다.
    topic_zone = (f"{top.quote_path} {top.page_title} "
                  f"{top.quote_text[:TOPIC_ZONE_CHARS]}")
    # 되돌림: 핵심 낱말 하나만 보게 완화했더니 확신 오답이 0 → 2 로 늘었다.
    # '절차' 가 제목에 없는 정답을 몇 건 살리는 대가로 틀린 답 두 건이 나갔다.
    # 놓치면 학생이 다른 데를 찾고, 틀리면 잘못된 곳으로 간다 — 교환이 성립하지 않는다.
    off = [t for t in required
           if not any(f in topic_zone for f in [t, *sorted(expand.get(t, ()))])]
    # ★ 한 단계 올라가는 것과 모른다고 하는 것을 가른다.
    #   핵심 낱말이 제목·첫머리에 **있으면** 페이지는 맞다. 섹션만 못 고른 것이다.
    #     '자퇴 절차' — '자퇴' 는 제목에 있고 '절차' 만 없다. 문서는 '절차' 라는
    #     말을 안 쓰고 절차를 적는다. 페이지로 답하면 학생이 찾을 수 있다.
    #   핵심 낱말이 **없으면** 페이지도 아니다. 그때는 모른다고 해야 한다.
    #     '기숙사 통금' — '통금' 이 어디에도 없다. 생활관 게시판을 보여주면
    #     학생은 답을 받은 줄 알고 없는 것을 찾게 된다.
    core_off = core in off
    page_only = bool(off) and not core_off
    if core_off:
        result.outcome = Outcome.AMBIGUOUS
        result.defer_reason = f"'{core}' 가 제목·첫머리에 없음 (본문에서 스침)"
        return result
    if page_only:
        result.defer_reason = (f"'{' '.join(off)}' 가 제목·첫머리에 없어 "
                               f"섹션을 못 고름 → 페이지로 답함")

    # 제목에 질문의 말이 없어도 답이 아닌 것은 아니다.
    # '재수강' 을 문서는 '교과의 재이수' 라고 쓴다. 낱말이 다르다고 틀린 답은 아니다.
    # 목차가 이기는 문제는 색인 단계에서 라벨을 빼서 푼다 (parser 의 is_index).

    # 경쟁 후보가 **다른 사이트**에 있을 때 보류한다.
    # 다만 1등이 본부 문서면 보류하지 않는다 — 본부 규정은 전교 공통이라
    # 학과 페이지가 여럿 걸려도 답은 본부 것이 맞다.
    cross_site = (top.host != HQ_HOST
                  and any(h.host != top.host for h in rivals))

    # ★ 여러 학과가 저마다 답을 갖고 있으면, 그 주제는 학과마다 다른 것이다.
    #   '졸업요건' 이 그렇다. 주제 목록을 코드에 박지 않고 관측으로 가린다 —
    #   학과 사이트 여럿이 동시에 걸린다는 사실 자체가 근거다.
    dept_rivals = {h.host for h in rivals if h.host != HQ_HOST}
    if len(dept_rivals) >= DEPT_SPECIFIC_SITES:
        cross_site = True
        result.defer_reason = f"학과 {len(dept_rivals)}곳이 저마다 답을 가짐"
    elif cross_site:
        result.defer_reason = (
            f"다른 사이트 후보가 근접 ({rivals[0].site_name} "
            f"{rivals[0].score:.0f} vs {top.score:.0f})")

    result.outcome = Outcome.AMBIGUOUS if cross_site else Outcome.FOUND

    # ★ 섹션을 못 고르겠으면 고르지 않는다 — 한 단계 올라간다.
    #   틀린 문단을 확신 있게 인용하는 것보다 맞는 페이지를 통째로 보여주는 게 낫다.
    #   학생이 스스로 찾을 수 있으니까.
    if result.outcome is Outcome.FOUND:
        m = margin_of.get(top.page_url, float("inf"))
        result.section_margin = round(m, 2) if m != float("inf") else 0.0
        # 잘렸어도 정답이 앞쪽에 있었으면 뒤가 잘린 것과 상관이 없다
        cut_matters = (result.candidates_truncated
                       and result.answer_depth > len(rows) * TRUNCATION_SAFE_DEPTH)
        need = SECTION_MARGIN * (TRUNCATED_MARGIN_FACTOR if cut_matters else 1.0)
        if page_only or m < need:
            result.page_level = True
        if cut_matters and result.page_level:
            result.defer_reason = (
                f"후보가 상한에 닿아 잘렸다 "
                f"({result.candidates_matched} → {result.candidates_returned}). "
                f"섹션을 찍지 않고 페이지로 답함")
    return result


# ─────────────────────────────────────────────────────────────────────────
# 공지 검색 — 제목만 본다. 본문을 안 읽었으니 본문을 아는 척하지 않는다.

@dataclass
class NoticeHit:
    title: str
    url: str
    published_at: str | None
    board_name: str
    site_name: str
    category: str = ""


@dataclass
class NoticeResult:
    outcome: Outcome
    query_tokens: list[str] = field(default_factory=list)
    hits: list[NoticeHit] = field(default_factory=list)
    searched_total: int = 0
    site_name: str = ""


MAX_NOTICE_HITS = 5


def search_notices(conn, utterance: str, *, repo) -> NoticeResult:
    """공지 제목 검색.

    ★ 제목이 맞으면 보여준다. 본문은 읽지 않았으므로 '무슨 내용이다' 라고
      말하지 않는다. '이런 공지가 있고 여기서 볼 수 있다' 까지가 정직한 답이다.
    """
    if is_personal_lookup(utterance):
        return NoticeResult(Outcome.PERSONAL)
    tokens = tokenize(utterance)
    if not tokens:
        return NoticeResult(Outcome.NO_QUERY)

    total = repo.notice_total(conn)
    if total == 0:
        return NoticeResult(Outcome.NO_DATA, query_tokens=tokens)

    site_host, site_label = match_site(utterance)
    if site_host:
        used = {a for a, h in site_aliases().items() if h == site_host}
        used |= {site_label}
        narrowed = [t for t in tokens if not any(u and u in t for u in used)]
        if narrowed:
            tokens = narrowed

    rows = repo.search_notices(conn, tokens, host=site_host)
    # 제목에 질문의 말이 실제로 들어 있는 것만 남긴다.
    # 색인이 느슨하게 걸어준 것을 그대로 믿지 않는다.
    hits = [NoticeHit(title=r["title"], url=r["url"],
                      published_at=r["published_at"],
                      board_name=r["board_name"] or "",
                      site_name=r["site_name"] or "",
                      category=r["category"] or "")
            for r in rows if any(t in r["title"] for t in tokens)]
    res = NoticeResult(Outcome.NOT_FOUND if not hits else Outcome.FOUND,
                       query_tokens=tokens, searched_total=total,
                       site_name=site_label)
    res.hits = hits[:MAX_NOTICE_HITS]
    return res
