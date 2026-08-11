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
AMBIGUOUS_RATIO = 0.85
MAX_CANDIDATES = 5
PATH_BOOST = 1.6          # 경로(제목)에서 맞으면 본문보다 무겁게 본다
# 학과를 안 밝힌 질문에는 본부 문서가 답이다. 학과 페이지 205곳이 표를 갈라
# 정작 규정 원문이 밀려나는 것을 막는다.
HQ_HOST = "www.jbnu.ac.kr"
HQ_BOOST = 2.0
SITES_PATH = pathlib.Path(__file__).resolve().parents[1] / "config" / "sites.yaml"


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
    return best_host, best_name


class Outcome(str, Enum):
    FOUND = "found"
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


def score_rows(rows: list[dict[str, Any]], tokens: Sequence[str],
               weights: dict[str, float], *, hq_boost: bool = True) -> list[Hit]:
    hits: list[Hit] = []
    for r in rows:
        text = r.get("text") or ""
        path = r.get("path") or ""
        matched, s = [], 0.0
        for t in tokens:
            in_text = t in text
            in_path = t in path
            if not (in_text or in_path):
                continue
            matched.append(t)
            s += weights.get(t, 0.0) * (PATH_BOOST if in_path else 1.0)
        if not matched:
            continue
        # 질문의 여러 낱말이 한 문단에 같이 나오면 그만큼 더 맞는 답이다
        s *= 1.0 + 0.35 * (len(matched) - 1)
        host = r.get("host") or ""
        if hq_boost and host == HQ_HOST:
            s *= HQ_BOOST
        hits.append(Hit(
            section_key=r["section_key"], page_url=r["page_url"], host=host,
            site_name=site_names().get(host, ""),
            page_title=r.get("page_title") or "", path=path, text=text,
            quote_key=r.get("quote_key"), page_modified=r.get("page_modified"),
            observed_at=r.get("observed_at") or "", score=round(s, 3),
            matched=matched))
    hits.sort(key=lambda h: (-h.score, len(h.text)))
    return hits


def _dedupe_by_page(hits: list[Hit]) -> list[Hit]:
    """페이지마다 가장 잘 맞는 것 하나만. 같은 페이지를 여러 줄 보여줄 이유가 없다."""
    seen, out = set(), []
    for h in hits:
        if h.page_url in seen:
            continue
        seen.add(h.page_url)
        out.append(h)
    return out


def search(conn, utterance: str, *, repo) -> SearchResult:
    tokens = tokenize(utterance)
    if not tokens:
        return SearchResult(Outcome.NO_QUERY)

    total = repo.section_total(conn)
    if total == 0:
        # 조회할 것이 아예 없다. '없다' 가 아니라 '아직 안 긁었다' 다.
        return SearchResult(Outcome.NO_DATA, query_tokens=tokens)

    site_host, site_label = match_site(utterance)
    df = {t: repo.token_doc_freq(conn, t) for t in tokens}
    rows = repo.search_sections(conn, tokens, host=site_host)
    hits = score_rows(rows, tokens, _weights(tokens, total, df),
                      hq_boost=site_host is None)
    hits = _dedupe_by_page(hits)

    result = SearchResult(Outcome.NOT_FOUND, query_tokens=tokens,
                          searched_sections=total,
                          site_host=site_host, site_name=site_label)
    if not hits or hits[0].score < MIN_SCORE:
        return result

    # 인용 단위를 붙인다 — 잎으로 찾고 부모를 인용한다
    for h in hits[:MAX_CANDIDATES]:
        q = repo.get_section(conn, h.quote_key) if h.quote_key else None
        h.quote_text = (q or {}).get("raw_text") or h.text
        # 경로는 **인용 블록의 것**을 쓴다. 잎의 경로는 마지막 칸이 문장 전체라
        # 화면에 문단이 두 번 나오고, 경로 구실을 못 한다.
        h.quote_path = (q or {}).get("path") or h.path

    top = hits[0]
    rivals = [h for h in hits[1:MAX_CANDIDATES]
              if h.score >= top.score * AMBIGUOUS_RATIO]
    result.hits = hits[:MAX_CANDIDATES]
    result.missing_tokens = [t for t in tokens if t not in top.matched]

    # ★ 긍정 단정에는 높은 근거를 요구한다.
    #   질문의 낱말 하나가 어디에도 안 맞았는데 확신하면 엉뚱한 문서를 답으로 준다.
    #   '기숙사 통금' 에서 '통금' 을 놓치고 학술교류 협정문을 내놓은 적이 있다.
    if result.missing_tokens:
        result.outcome = Outcome.AMBIGUOUS
        return result

    # 경쟁 후보가 **다른 사이트**에 있을 때만 보류한다.
    cross_site = any(h.host != top.host for h in rivals)
    result.outcome = Outcome.AMBIGUOUS if cross_site else Outcome.FOUND
    return result
