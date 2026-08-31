"""무엇에 답할 수 있고 무엇에 답할 수 없는가 — 실제 질문으로 잰다.

커버리지 숫자만으로는 학생에게 뭘 줄 수 있는지 알 수 없다.
'페이지 3,320개 수집' 은 '수강신청 정정기간을 답할 수 있다' 와 다른 말이다.
그래서 질문을 넣어 보고, 나온 인용문이 실제로 그 질문의 답인지까지 본다.

★ 자가 못 재는 자리는 자를 고쳐도 안 보인다 — 오늘 두 번 나왔다
    must 문제   46문항이 답값형을 5건만 가진다 → '답했나' 를 못 잰다
    표 문제     46문항에 학과별 졸업요건이 없다 → 파서가 깨진 걸 못 잰다
                (코퍼스로는 학과 졸업요건 표의 75.8% 가 깨져 있다)

  46문항은 상상해서 만들어졌다. 안 떠올린 자리는 자를 아무리 고쳐도 영원히 안 잡힌다.
  그건 집행부 베타에서만 나온다 — 베타는 '있으면 좋은 것' 이 아니라
  **측정의 유일한 출구**다.

★ 기대값을 코드에 박아 둔다
  사람이 눈으로 판정하면 다음번에 재현이 안 된다.
  고칠 때마다 정확도가 오르는지 내리는지 숫자로 봐야 한다.

    expect="answer"  답해야 한다. 인용문에 must_contain 이 들어 있어야 맞다.
    expect="defer"   답하면 안 된다. 자료가 없거나 학과가 갈리는 질문.

★ 판정 칸 — 뭉쳐 세면 고칠 수 있는지 없는지 알 수가 없다
  전에는 ✅/△/❌ 셋이었고, ✅ 가 두 가지를 뭉쳐 세고 있었다.
  must 를 **인용문 또는 경로**에서 찾았기 때문이다.

      수강신청 언제야   인용문 '가. 사회봉사'   경로에 '수강신청' 있음  → ✅

  경로가 맞다고 인용이 답인 것은 아니다. 학생이 받는 건 인용문이다.
  전수로 세니 1등 인용문이 15자 이하인 문항이 42개 중 13개(31%)였다.
  '1' · '1-2' · '가. 사회봉사' · '월간 일정' — 혼자서는 뜻이 없는 조각이다.
  확신 오답이 아니라서 안 잡혔다. 안전하고, 쓸모가 없다.

      확신      섹션까지 짚었고 must 가 **인용문 안**에 있다
      문서까지   페이지 단위로 답했다 — '이 문서에 있다' 는 참이지만 어디인지는 안 말했다
      쓸모없음   섹션을 짚었는데 must 가 인용문에 없고 **경로에만** 있다 = 조각
      모름      답하지 않았다 (정직한 실패)
      틀림      답하면 안 되는데 답했다 / must 가 어디에도 없다  ← 배포 조건

  ★ 이 칸은 대리 지표가 아니다
    자족성을 고치면 인용이 '휴학 절차' 블록으로 올라가 must 를 담게 된다.
    쓸모없음 → 확신 으로 옮겨가며 숫자가 **저절로** 움직인다.
    고치기 전에 칸을 나눠야 효과가 있었는지 알 수 있다.

    python tools/answerability_report.py --db data/jbnu.db
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import manual_answers  # noqa: E402
from skill import section_search as ss  # noqa: E402
from skill import clarify  # noqa: E402
from skill import selfcontained  # noqa: E402


class _ManualHit:
    """총학 확인 답을 검색 결과와 같은 모양으로 감싼다 (측정용)."""

    def __init__(self, e):
        self.quote_text = f"{e.answer} {e.alternative}".strip()
        self.text = self.quote_text
        self.quote_path = e.key
        self.page_title = e.key
        self.site_name = "총학 확인"
        self.title = e.key
        self.board_name = ""


class _ManualResult:
    def __init__(self, e):
        self.outcome = ss.Outcome.FOUND
        self.top = _ManualHit(e)
        self.hits = [self.top]
        self.query_tokens = []
        self.defer_reason = ""
        self.page_level = False
from store import repo  # noqa: E402
from tools import answer_path  # noqa: E402

A, D = "answer", "defer"

# ★ 문항을 **실제 수요 분포**에 맞춰 배분한다.
#   62% 항목의 오답과 5% 항목의 오답을 같은 무게로 세면 안 된다.
#   출처: 총학 수요 조사
DEMAND = {
    "수강신청": 62.2,
    "장학·등록": 59.5,
    "학사일정": 56.8,
    "행사": 43.2,
    "취업·비교과": 35.1,
    "공지": 32.4,
    # 아래는 수요 조사에 없던 축. 가중에서 빠지고 균등 집계에만 들어간다.
    "학과": 0.0,
    "보류": 0.0,
}

# (주제, 질문, 기대, 인용문에 반드시 들어갈 말)
# must_contain 은 **원문에 실재하는 문구**만 쓴다. 없는 걸 기대값에 넣으면
# 그건 기준이 아니라 소원이다.
#
# ★ 지금 must 는 대부분 **주제어**다 — 이 지표가 무엇을 재는지 알고 써야 한다
#   질문에 들어 있는 말이면 주제어(관련성), 없는 말이면 답값(새 정보)이다.
#   전수로 세니 주제어 34건 · 답값 5건이었다.
#       답값형   재이수 · 4.5 · 없어요 · 등록금 전액 · 교과과정
#   즉 37% 는 '답했나' 가 아니라 **'주제가 맞나'** 에 가깝다.
#   확신 10건도 '인용에 주제어가 있다' 는 뜻이지 '답했다' 는 보증이 아니다.
#
#   ★ 이게 되묻기 넓히기를 막고 있다
#     '되물을 필요가 없었는데 되물었다'(후퇴)를 재려면
#     'off 일 때 이미 답을 받았나' 를 알아야 하고, 그건 답값 판정이 있어야 한다.
#     지금 문항으로는 A+ 하나만 잡힌다 (문서+발췌 19건 중 답값형 1건).
#     되묻기를 넓히려면 **문항의 must 를 답값으로 다시 쓰는 것**이 먼저다.
#     지표를 올리는 일이 아니라 지표가 무엇을 재는지 정하는 일이다.
QUESTIONS: list[tuple[str, str, str, str]] = [
    # ── 수강신청 62.2% (가장 많이 묻는다) ─────────────────────────
    ("수강신청", "수강신청 언제야", A, "수강신청"),
    ("수강신청", "수강신청 학점 상한", A, "학점"),
    ("수강신청", "재수강 규정", A, "재이수"),
    ("수강신청", "계절학기 수강료", A, "수강료"),
    ("수강신청", "성적 A+ 몇 점", A, "4.5"),
    ("수강신청", "성적 이의신청", A, "이의"),
    ("수강신청", "시험 언제", A, "시험"),
    ("수강신청", "수강신청 정정", D, ""),
    # 제도가 아예 없다 — T4 부정 답변으로 답한다.
    # 크롤로는 영원히 알 수 없어서 '보류' 로 세고 있었는데, 사실은 정답 가능한 문항이었다.
    ("수강신청", "학점포기", A, "없어요"),
    # ── 장학·등록 59.5% ──────────────────────────────────────────
    ("장학·등록", "1종 장학금 얼마야", A, "등록금 전액"),
    ("장학·등록", "국가장학금 신청", A, "국가장학금"),
    ("장학·등록", "교내 장학금 종류", A, "장학"),
    ("장학·등록", "등록금 납부 기간", A, "등록금"),
    ("장학·등록", "등록금 분할납부", A, "분할"),
    ("장학·등록", "근로장학생", A, "근로"),
    ("장학·등록", "학자금 대출", A, "대출"),
    ("장학·등록", "장학금 공지", A, "장학"),
    # ── 학사일정 56.8% ───────────────────────────────────────────
    ("학사일정", "휴학 신청", A, "휴학"),
    ("학사일정", "복학 신청", A, "복학"),
    ("학사일정", "자퇴 절차", A, "자퇴"),
    ("학사일정", "전과 신청", A, "전과"),
    ("학사일정", "복수전공 신청", A, "복수전공"),
    ("학사일정", "부전공 이수학점", A, "부전공"),
    ("학사일정", "조기졸업 요건", A, "조기졸업"),
    ("학사일정", "편입학 학점인정", A, "편입"),
    ("학사일정", "증명서 발급", A, "증명서"),
    ("학사일정", "졸업요건", D, ""),          # 학과마다 다름 → 되물어야
    # ── 교내 행사 43.2% ──────────────────────────────────────────
    ("행사", "동아리 등록", A, "동아리"),
    ("행사", "박물관 관람", A, "박물관"),
    ("행사", "공연 일정", A, "공연"),
    # ── 취업·비교과 35.1% ────────────────────────────────────────
    ("취업·비교과", "현장실습", A, "현장실습"),
    ("취업·비교과", "취업 상담", A, "상담"),
    ("취업·비교과", "창업 지원", A, "창업"),
    ("취업·비교과", "심리상담", A, "상담"),
    # ── 공지 32.4% (제목 검색) ───────────────────────────────────
    ("공지", "수강신청 공지", A, "수강신청"),
    ("공지", "교환학생", A, "교환학생"),
    ("공지", "모집 공고", A, "모집"),
    # ── 학과 (수요 조사에 없던 축) ───────────────────────────────
    ("학과", "기계공학과 교수", A, "교수"),
    ("학과", "간호대학 연혁", A, "간호"),
    ("학과", "컴퓨터인공지능학부 교육과정", A, "교과과정"),
    ("학과", "총장 누구야", A, "총장"),
    # ── 답하면 안 되는 것 ────────────────────────────────────────
    # ★ '기숙사 통금' 을 뺐다 (2026-08-14, 총학생회장 판단)
    #   "수강신청 정정은 있을텐데 기숙사 통금은 빼도 될 것 같아" — 이미 정해진 문항이다.
    #   자료가 없고, 후보로 잡히는 건 생활관 게시판 목록뿐이다.
    #
    #   ★ 목록에서 뺀다고 학생이 안 묻는 건 아니다.
    #     그래서 게시판 목록을 답인 척 내보내던 문구는 따로 되돌렸다
    #     (templates.render_section 의 '찾은 건 이거예요').
    #     문항을 빼는 것과 더 단정적으로 틀리는 것은 다른 문제다.
    ("보류", "분실물 찾기", D, ""),
    ("보류", "안녕하세요", D, ""),
    ("보류", "오늘 날씨", D, ""),
    ("보류", "내 성적 알려줘", D, ""),
]


def shorten(conn, q: str, *, repo) -> str:
    """질문을 학생이 실제로 치는 길이로 줄인다.

    ★ 46문항이 전부 구체적으로 쓰여 있었다 — 문항을 상상해서 만들면 그렇게 된다.
          우리가 쓴 것   '휴학 어떻게 해'
          학생이 치는 것  '휴학'
      되묻기가 필요한 자리가 통째로 측정에서 빠져 있었다.
      그리고 자족성은 짧은 질문을 원리상 못 고친다 —
      답이 여럿인 것을 하나로 만들 수는 없다.

    ★ 짧은 형태를 손으로 쓰지 않는다
      우리 토크나이저가 **가장 무겁게 보는 낱말**을 남긴다.
      드문 낱말일수록 무겁다 — 그게 곧 질문의 핵심어다.
      손으로 고르면 우리가 고치고 싶은 쪽으로 고르게 된다.
    """
    toks = ss.tokenize(q)
    if len(toks) <= 1:
        return q
    total = repo.section_total(conn)
    df = {t: repo.token_doc_freq(conn, t) for t in toks}
    w = ss._weights(toks, total, df)
    return max(toks, key=lambda t: w.get(t, 0.0))


def bottleneck(conn, q: str, must: str) -> tuple[str, str]:
    """놓친 문항의 병목이 **커버리지인지 검색인지** 가른다.

    ★ 왜 갈라야 하나
      DB 에 있는데 못 찾은 것이면 더 긁어도 나아지지 않는다. 검색을 고쳐야 한다.
      DB 에 없는 것이면 검색을 아무리 고쳐도 안 나온다. 더 긁어야 한다.
      둘을 같이 세면 어느 쪽에 힘을 쓸지 알 수 없다.
    """
    if not must:
        return "-", ""
    # ★ '본문 어딘가에 낱말이 있다' 를 '정답이 있다' 로 세면 안 된다.
    #   '근로' 가 산업안전교육 문서에 스쳐도 그건 근로장학 안내가 아니다.
    #   문서의 **제목·경로**에 있어야 그 문서가 그 주제다.
    row = conn.execute(
        """SELECT s.path, r.title, r.host FROM page_section s
             JOIN page_registry r ON r.page_url = s.page_url
            WHERE r.title LIKE ? OR s.path LIKE ?
            ORDER BY length(s.path) LIMIT 1""",
        (f"%{must}%", f"%{must}%")).fetchone()
    if row:
        return "검색", f"{row['host']} · {(row['title'] or '')[:20]}"
    n = conn.execute("SELECT COUNT(*) FROM notice_item WHERE title LIKE ?",
                     (f"%{must}%",)).fetchone()[0]
    if n:
        return "검색", f"공지 제목 {n}건"
    # 제목엔 없고 본문에만 있으면 '있다고 보기 애매하다' — 따로 센다
    t = conn.execute(
        "SELECT COUNT(*) FROM page_section WHERE text LIKE ?",
        (f"%{must}%",)).fetchone()[0]
    if t:
        return "본문만", f"본문 {t}곳에 스침 (제목엔 없음)"
    return "커버리지", "DB 어디에도 없음"


def _would_clarify(conn, q: str, r) -> bool:
    """이 질문에 되묻기가 발동하나.

    ★ 자를 학생이 받는 것에 맞춘다
      되묻기를 켠 뒤로 '문서+발췌' 중 일부는 발췌 대신 **버튼**을 받는다.
      리포트가 그걸 모르면 자와 화면이 어긋난다.
    """
    if not (r.outcome is ss.Outcome.FOUND and getattr(r, "page_level", False)
            and getattr(r, "top", None) is not None):
        return False
    tk = r.query_tokens or []
    opts = clarify.options(conn, r.top.page_url, tk)
    if not opts:
        return False
    return not (clarify.already_narrowed(q, opts, tk)
                or clarify.narrowed_by_qualifier(q, opts, tk))


# 판정 칸. 순서가 곧 학생에게 좋은 순서다.
#
# ★ '문서까지' 를 다시 쪼갠다 — 안 쪼개면 인용 승격의 효과가 안 보인다
#   인용 승격은 **표시 단계**라 마진을 안 건드린다. 그러면 page_level 이 그대로라
#   랭킹 개선 때와 똑같이 '인용문이 좋아졌다' 는 보이는데 칸이 안 움직인다.
#   그런데 학생 경험은 진짜로 달라진다.
#       지금    "휴학 페이지에 있어요" + '사유 발생시'      → 직접 찾아야 함
#       승격 후  "휴학 페이지에 있어요" + '일반 휴학' 123자   → 사실상 답을 받음
#   판정은 이미 있는 자족성 판정기로 그대로 된다.
#
# ★ '형식안내' 를 따로 센다 — 모름과 다르다
#   "졸업요건은 학과마다 달라요. '사학과 졸업요건'처럼 물어봐 주세요" 는
#   못 찾았다는 말이 아니라 **다음 수를 알려준 것**이다.
#   모름과 같이 세면 형식 안내를 붙여도 숫자가 안 움직인다.
SURE, PAGE_Q, PAGE, THIN, ATTR, MISS, WRONG, DEFER_OK = (
    "확신", "문서+발췌", "문서만", "쓸모없음", "형식안내", "모름", "틀림", "보류OK")
# ★ '문서 미확인' — 답은 했는데 **맞는 문서인지 사람이 확인 안 한 것** (2026-08-17)
#   '성적 이의신청' 이 문서+발췌로 통과 중인데 나오는 건 **정보공개 이의신청**(행정)
#   문서다. 학생이 물은 건 학사 이의신청이다.
#   must('이의') 가 인용에 있으니 통과했다 — 자가 '문서를 찾았는가' 만 봤다.
#
#   ★ 문자열로는 못 가른다. 학사 이의신청과 행정 이의신청은 낱말이 겹친다.
#     판정에 생성 모델을 쓰면 그 자를 검증할 자가 또 필요해진다 —
#     오늘 하루 종일 부딪힌 게 그것이다.
#   그래서 **사람이 확인한 것과 안 한 것을 갈라 센다.** 자를 고치는 게 아니라
#   칸을 쪼갠다. 85% → 37% 때와 같은 수법이다.
UNVERIFIED = "문서미확인"
# 정답으로 세는 칸. ★ '문서까지' 와 '쓸모없음' 은 안 센다 —
#   안전하지만 학생 질문에 답한 것이 아니다. 되묻기가 노리는 칸이 바로 여기다.
#   ★ '문서미확인' 도 안 센다 — 맞는 문서인지 모르는 걸 정답으로 세면 그게 거짓말이다.
CORRECT = (SURE, DEFER_OK)

REVIEW_PATH = ROOT / "config" / "answer_review.yaml"


def load_review() -> dict[str, dict]:
    """검수 결과 — 문항별 O/X. 없으면 빈 dict (= 전부 미확인).

    ★ 대표가 채우는 자리다. 시트에서 O/X 만 찍고 내보낸다 —
      46번 문서를 찾아 URL 을 복사하는 것보다 훨씬 싸다.
    """
    try:
        import yaml
        doc = yaml.safe_load(REVIEW_PATH.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for row in (doc.get("answers") or []):
        q = str(row.get("question") or "").strip()
        if q:
            out[q] = row
    return out


def apply_review(q: str, verdict: str, why: str,
                 review: dict[str, dict], top=None) -> tuple[str, str]:
    """검수 결과를 판정에 씌운다.

    ★ 답한 칸만 대상이다. '모름' 은 문서를 안 냈으니 확인할 문서가 없다.
    """
    if verdict not in (SURE, PAGE_Q, PAGE, THIN):
        return verdict, why
    row = review.get(q)
    if not row or row.get("ok") is None:
        return UNVERIFIED, f"{verdict} — 맞는 문서인지 확인 안 됨"

    # ★ must 를 **낱말에서 문서로** 옮긴다 (2026-08-30)
    #   오늘 낱말로 세다 다섯 번 틀렸다 — 연혁·전임교수·시험·시점, 그리고 must.
    #   '성적 이의신청' 이 **정보공개 이의신청(행정)** 문서를 내는데
    #   must('이의') 가 인용에 있어서 통과했다.
    #   한 번은 사고, 두 번은 부류, 세 번은 구조다.
    #
    #   ★ O/X 를 그대로 쓰지 않고 **매번 다시 잰다**
    #     X 를 그대로 쓰면 고쳐도 계속 빨간불이라 아무도 안 본다.
    #     사람이 정한 건 '정답 문서' 고, 지금 그 문서를 내는지는 자가 본다.
    #     고치면 저절로 초록이 된다.
    #
    #   ★ 두 가지를 다 받는다
    #     expected      정답 문서가 있다 — 그것을 내야 통과
    #     not_expected  정답이 코퍼스에 없다 — 그것만 안 내면 된다
    #                   ('성적 이의신청' 이 이쪽이다. 학사 문서가 아예 없다)
    doc = " ".join(str(x) for x in (
        getattr(top, "site_name", ""), getattr(top, "page_title", ""),
        getattr(top, "title", ""), getattr(top, "page_url", ""),
        getattr(top, "url", "")) if x)
    want = (row.get("expected") or "").strip()
    nope = (row.get("not_expected") or "").strip()
    if want:
        return ((verdict, why) if want in doc else
                (WRONG, f"문서 must — '{want}' 를 내야 하는데 '{doc.strip()}'"))
    if nope:
        return ((WRONG, f"문서 must — '{nope}' 는 답이 아니다") if nope in doc
                else (verdict, why))
    if row.get("ok") is False:
        return WRONG, f"검수 X — {row.get('note') or '엉뚱한 문서'}"
    return verdict, why


def judge(q: str, expect: str, must: str, r) -> tuple[str, str]:
    """(판정, 사유).

    ★ must 를 인용문에서 찾을 때와 경로에서 찾을 때를 **갈라 센다.**
      학생이 받는 것은 인용문이다. 경로가 맞다고 인용이 답인 것은 아니다.
    """
    answered = r.outcome is ss.Outcome.FOUND
    if expect == D:
        return (DEFER_OK, "") if not answered else (WRONG, "답하면 안 되는데 답함")
    if not answered:
        if getattr(r, "needs_attribute", ""):
            return ATTR, f"{r.needs_attribute}를 붙여 달라고 안내함"
        return MISS, f"답해야 하는데 {r.outcome.value}"
    top = getattr(r, "top", None) or (r.hits[0] if r.hits else None)
    if top is None:
        return MISS, "결과가 비었다"
    if hasattr(top, "quote_text"):
        quote = top.quote_text or top.text
        where = f"{top.quote_path or ''} {getattr(top, 'page_title', '')}"
    else:                                   # 공지 — 제목만 본다
        quote, where = top.title, top.board_name or ""
    in_quote = (not must) or must in quote
    in_where = bool(must) and must in where

    # 페이지 단위로 답할 때 우리가 주장하는 것은 '이 문서에 있다' 이지
    # '이 문단이 답이다' 가 아니다. 참이지만 확신은 아니다 — 따로 센다.
    if getattr(r, "page_level", False):
        # ★ 페이지 단위라고 한 칸에 몰면 또 뭉쳐 세는 것이다.
        #   문서를 맞게 짚었어도 같이 보여주는 인용이 조각이면 학생은 못 쓴다.
        if in_quote:
            # 발췌가 혼자 뜻이 서면 학생은 사실상 답을 받은 것이다.
            if selfcontained.is_self_contained(quote):
                return PAGE_Q, "문서 + 자족적 발췌"
            return PAGE, "문서만 짚었다 (발췌가 조각)"
        if in_where:
            return THIN, f"문서는 맞는데 인용에 '{must}' 가 없다"
        return WRONG, f"'{must}' 가 결과에 없음"
    if in_quote:
        return SURE, ""
    if in_where:
        # ★ 여기가 뭉쳐 세던 칸이다. 안전하지만 학생에겐 쓸모가 없다.
        return THIN, f"'{must}' 가 인용문에 없고 경로에만 있음"
    return WRONG, f"'{must}' 가 결과에 없음"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--json", default="")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--short", action="store_true",
                    help="학생이 실제로 치는 길이(핵심어 하나)로 잰다")
    args = ap.parse_args(argv)

    conn = repo.connect(args.db)
    try:
        summary = repo.coverage_summary(conn)
        print(f"색인 잎 {summary['indexed_leaves']:,} · 인용 가능 페이지 "
              f"{summary['by_status']['ok']:,}")
        if args.short:
            print("★ 짧은 질문 모드 — 학생이 실제로 치는 길이로 잰다")
        print()

        rows, lat = [], []
        # ★ 어느 갈래로 갔는지 세어 둔다. 새 경로가 생기면 여기서 보인다.
        routes: collections.Counter = collections.Counter()
        unreadable: list[tuple[str, str, str]] = []
        verdicts: collections.Counter = collections.Counter()
        review = load_review()
        skipped_short = []
        for topic, q0, expect, must in QUESTIONS:
            q = q0
            if args.short:
                q = shorten(conn, q0, repo=repo)
                # ★ 질문을 줄이면 기대값도 같이 줄여야 한다.
                #   '수강신청 학점 상한' 의 must 는 '학점' 인데, 짧은 질문은
                #   학점을 묻지 않는다. 자를 안 바꾸고 대상만 바꾸면
                #   측정이 인공물을 만들어낸다 — 실제로 확신 오답 4건이 전부 그거였다.
                #   짧은 질문에 요구할 수 있는 건 하나뿐이다: **그것에 대한 답인가.**
                if q != q0:
                    if expect == D:
                        # 보류해야 하는 이유가 한정어에 있었다면
                        # 한정어를 뗀 질문은 더 이상 같은 질문이 아니다.
                        skipped_short.append((q0, q))
                        continue
                    must = q
            t0 = time.perf_counter()
            # 공지는 제목 검색이 담당한다. 같은 잣대로 잰다.
            # ★ 총학이 직접 확인한 답이 먼저다 — 서버 라우팅과 같은 순서로 잰다.
            #   재는 자가 실제 경로와 다르면 측정이 거짓말을 한다.
            # ★ 갈래를 **흉내내지 않고 끌어온다** (2026-08-18)
            #   여기서 topic=="공지" 로 갈랐더니 '수강신청 언제야' 를
            #   검색으로 쟀다. 학생은 학사일정으로 간다 —
            #   받지도 않는 답을 채점하고 있었다. 같은 병이 두 번째다.
            #   이제 tools/answer_path 가 server.route_of 를 그대로 부른다.
            obs = answer_path.observe(conn, q, db_path=args.db)
            routes[(obs.route, obs.why)] += 1
            if not obs.readable:
                # ★ 자가 모르는 갈래. 검색으로 떨어뜨리지 않는다 —
                #   그러면 '재고 있다' 는 착각만 남는다.
                unreadable.append((q, obs.route, obs.why))
                continue
            r = obs.result
            ms = (time.perf_counter() - t0) * 1000
            lat.append(ms)
            v, why = judge(q, expect, must, r)
            _top = getattr(r, "top", None) or (r.hits[0] if r.hits else None)
            # ★ 사람이 문서를 확인했는지 씌운다 — 자를 고치는 게 아니라 칸을 쪼갠다
            #   문서 must 가 있으면 **지금 낸 문서**와 맞춰 본다 (매번 다시 잰다).
            v, why = apply_review(q, v, why, review, _top)
            verdicts[v] += 1
            top = getattr(r, "top", None) or (r.hits[0] if r.hits else None)
            # ★ 의심 신호 — 판정에서는 뺀다. 표시로만 남긴다.
            suspect = False
            if v == UNVERIFIED and top is not None:
                where = (f"{getattr(top, 'site_name', '')} "
                         f"{getattr(top, 'page_title', '')} "
                         f"{getattr(top, 'quote_path', '') or getattr(top, 'path', '')}")
                import re as _re
                toks = [t for t in _re.split(r"[^0-9A-Za-z가-힣+]+", q)
                        if len(t) >= 2]
                suspect = not any(t in where for t in toks)
            rows.append({
                "suspect": suspect,
                "page_title": getattr(top, "page_title", "") if top else "",
                "topic": topic, "q": q, "q_full": q0, "expect": expect,
                "verdict": v,
                "why": why, "outcome": r.outcome.value,
                "site": getattr(top, "site_name", "") if top else "",
                "path": (getattr(top, "quote_path", "")
                         or getattr(top, "title", "")) if top else "",
                "quote": ((getattr(top, "quote_text", "")
                           or getattr(top, "title", ""))[:120]
                          .replace("\n", " ") if top else ""),
                "ms": round(ms), "defer": getattr(r, "defer_reason", ""),
                "truncated": getattr(r, "candidates_truncated", False),
                "matched": getattr(r, "candidates_matched", 0),
                "depth": getattr(r, "answer_depth", 0),
                "margin": round(getattr(r, "section_margin", 0.0) or 0.0, 2),
                "clarify": _would_clarify(conn, q, r),
                "page_level": bool(getattr(r, "page_level", False)),
            })
            if v not in CORRECT and expect == A:
                kind, where = bottleneck(conn, q, must)
                rows[-1]["bottleneck"] = kind
                rows[-1]["bottleneck_where"] = where

        icon = {SURE: "✅", DEFER_OK: "✅", PAGE_Q: "📃", PAGE: "📄",
                THIN: "🫥", ATTR: "🔎", MISS: "△", WRONG: "❌",
                UNVERIFIED: "❓"}
        if not args.quiet:
            cur = None
            for r in rows:
                if r["topic"] != cur:
                    cur = r["topic"]
                    print(f"── {cur} ──")
                note = f"  ← {r['why']}" if r["why"] else ""
                if r["verdict"] == MISS and r.get("defer"):
                    note += f"  [{r['defer']}]"
                print(f"  {icon[r['verdict']]} {r['q']:20} "
                      f"{r['site'][:11]:13} {r['path'][:30]}{note}")
                if r["verdict"] in (WRONG, THIN) and r["quote"]:
                    print(f"        인용: {r['quote'][:90]}")

        n = len(rows)
        ok = sum(verdicts[v] for v in CORRECT)
        if args.short and skipped_short:
            print(f"짧은 질문 모드에서 뺀 보류 문항 {len(skipped_short)}건 — "
                  "한정어를 떼면 같은 질문이 아니다")
            for a, b in skipped_short:
                print(f"  {a} → {b}")
        print(f"\n{'='*72}")
        print(f"균등 정확도 {ok}/{n} = {ok/n:.0%}")
        print(f"  ✅ 확신 {verdicts[SURE]} · ✅ 보류 {verdicts[DEFER_OK]}"
              f"  │  ❓ 문서미확인 {verdicts[UNVERIFIED]}"
              f" · 📃 문서+발췌 {verdicts[PAGE_Q]} · 📄 문서만 {verdicts[PAGE]}"
              f" · 🫥 쓸모없음 {verdicts[THIN]}"
              f" · 🔎 형식안내 {verdicts[ATTR]}"
              f" · △ 모름 {verdicts[MISS]} · ❌ 틀림 {verdicts[WRONG]}")
        print("  ★ 문서까지·쓸모없음은 안전하지만 정답으로 세지 않는다 — "
              "학생 질문에 답한 게 아니다")
        if verdicts[UNVERIFIED]:
            print(f"  ★ 문서미확인 {verdicts[UNVERIFIED]}건 — 답은 했는데 "
                  f"**맞는 문서인지 사람이 확인 안 했다.**")
            print(f"     '성적 이의신청' 이 정보공개(행정) 문서로 통과하던 자리다.")
            print(f"     python tools/review_sheet.py --out review.csv  → 시트에서 O/X")
            sus = [r for r in rows if r.get("suspect")]
            if sus:
                # ★ 판정이 아니라 **볼 순서**다. 이 신호는 '성적 이의신청' 을 못 잡는다.
                #   그래도 '증명서 발급 → 행동강령' 을 새로 찾아냈다.
                print(f"     ❗ 먼저 볼 것 {len(sus)}건 — "
                      f"질문 낱말이 문서 제목·경로에 하나도 없다")
                for r in sus:
                    print(f"        {r['q']:18} → {r.get('site','')} · "
                          f"{r.get('page_title','')}")
        nc = sum(1 for r in rows if r.get("clarify"))
        if nc:
            print(f"  ★ 이 중 {nc}건은 되묻기가 발동한다 — "
                  "학생은 발췌 대신 버튼을 받는다 (2턴에 답이 온다)")
        print(f"응답 중앙값 {sorted(lat)[len(lat)//2]:.0f}ms")

        # ★ 62% 항목의 오답과 5% 항목의 오답을 같은 무게로 세면 안 된다.
        #   균등 정확도는 우리가 문항을 몇 개 넣었느냐에 좌우된다.
        #   학생이 겪는 것은 수요로 가중한 쪽이다. 둘을 나란히 낸다.
        per: dict[str, list[int]] = {}
        for r in rows:
            per.setdefault(r["topic"], []).append(
                1 if r["verdict"] in CORRECT else 0)
        print("\n주제별")
        num = den = 0.0
        for topic, marks in per.items():
            w = DEMAND.get(topic, 0.0)
            acc = sum(marks) / len(marks)
            if w:
                num += w * acc
                den += w
            tag = f"수요 {w:>4}%" if w else "수요 조사 밖"
            print(f"  {topic:12} {sum(marks):2}/{len(marks):2} = {acc:4.0%}   {tag}")
        if den:
            print(f"\n★ 수요 가중 정확도 {num/den:.0%}   (균등 {ok/n:.0%})")
        # ★ 틀린 것과 놓친 것을 같은 무게로 세지 않는다.
        #   놓치면 학생이 다른 데를 찾는다. 틀리면 잘못된 곳으로 간다.
        # ★ 어느 갈래로 갔는지 **매번 찍는다** (2026-08-18)
        #   경로 어긋남을 두 번 다 사람이 눈으로 발견했다.
        #   갈래는 이제 server.route_of 에서 끌어오므로 어긋날 수 없지만,
        #   **새 경로가 생긴 것**은 여기 새 줄로 보인다. 눈으로 안 찾아도 된다.
        print("\n" + "─" * 58)
        print("어느 갈래로 갔나 — 서버 route_of 가 정한 그대로")
        for (route, why), n in routes.most_common():
            print(f"   {route:20} {n:>3}건   ({why})")
        if unreadable:
            print(f"\n   ★ 자가 못 읽는 갈래 {len(unreadable)}건 — "
                  f"**판정에서 빠졌다**")
            for q, route, why in unreadable:
                print(f"      {q:16} → {route} ({why})")
            print("      이 갈래를 재려면 tools/answer_path.py 에 한 칸 붙인다.")
        else:
            print("\n   ★ 자가 못 읽는 갈래 없음")
        print("─" * 58 + "\n")

        print(f"★ 확신하고 틀린 것 {verdicts[WRONG]}건 — 이게 0에 가까워야 배포할 수 있다")

        # ★ 안전하지만 쓸모없는 칸을 따로 본다 — 자족성이 노리는 자리다
        thin = [r for r in rows if r["verdict"] == THIN]
        if thin:
            print(f"\n🫥 안전하지만 쓸모없음 {len(thin)}건 — "
                  "경로는 맞는데 인용이 조각이다")
            for r in thin:
                print(f"  {r['q']:20} 인용 {r['quote'][:40]!r}")

        # ★ 마진 분포 — '문서까지' 가 안 줄었을 때 어디를 볼지 미리 정해 둔다
        #   동점(1.00)은 임계값으로 못 푼다. 1.5 → 1.2 → 1.1 로 낮춰도 안 통과하고,
        #   통과시키려면 1.0 이하여야 하는데 그건 안전장치를 끄는 것이다.
        #   문턱이 높은 게 아니라 **점수가 안 갈리는** 것이다.
        #   동점이 많다  → 집계 승격이 정확히 그걸 겨냥한 것
        #   동점이 적은데 문서까지가 많다 → 원인이 딴 데 있다
        pl = [r for r in rows if r["page_level"]]
        if pl:
            b: collections.Counter = collections.Counter()
            for r in pl:
                m = r["margin"]
                b["동점 1.00" if m <= 1.0 else "1.0~1.2" if m < 1.2
                  else "1.2~1.5" if m < 1.5 else "1.5+"] += 1
            print(f"\n페이지 단위로 내려간 {len(pl)}문항의 섹션 마진")
            for k in ("동점 1.00", "1.0~1.2", "1.2~1.5", "1.5+"):
                if b[k]:
                    print(f"  {k:10} {b[k]:3}")
            print(f"  → 문턱 {ss.SECTION_MARGIN} 을 낮춰서 구제되는 건 "
                  f"{b['1.0~1.2'] + b['1.2~1.5']}건뿐이다. "
                  f"동점 {b['동점 1.00']}건은 임계값으로 못 푼다.")

        # ★ 후보 절단은 두 번 다 오답이 난 뒤에야 알았다. 이제 센다.
        #   상한을 얼마로 할지 추측하지 말고 천장에 몇 번 닿는지 세면 된다.
        cut = [r for r in rows if r.get("truncated")]
        if cut:
            print(f"\n후보 절단 {len(cut)}/{n}문항 — 상한이 답을 자르고 있다")
            # ★ 잘림 여부만으로는 위험을 못 잰다. **정답이 몇 등이었나**가 답을 준다.
            #   앞쪽에 있었다면 뒤가 잘린 것과 상관이 없다.
            for r in sorted(cut, key=lambda x: -x["matched"])[:8]:
                d = r.get("depth") or 0
                room = f"여유 {600 - d}" if d else "깊이 미측정"
                print(f"  {r['matched']:6} → 600   {r['q']:18} "
                      f"정답 {d:4}등 · {room}  [{r['verdict']}]")
            deep = [r.get("depth") or 0 for r in cut if r.get("depth")]
            if deep:
                print(f"  → 정답 최악 {max(deep)}등. 상한 600 은 "
                      f"{600 // max(deep)}배 여유가 있다 — 지금은 추측이 아니라 계산이다.")
        else:
            print("\n후보 절단 0문항 — 상한이 아직 답을 자르지 않는다")

        # ★ 병목 — 더 긁을 것인가, 검색을 고칠 것인가
        #   DB 에 있는데 못 찾은 것이면 더 긁어도 나아지지 않는다.
        #   DB 에 없는 것이면 검색을 아무리 고쳐도 안 나온다.
        miss = [r for r in rows if r["verdict"] not in CORRECT and r["expect"] == A]
        if miss:
            srch = [r for r in miss if r.get("bottleneck") == "검색"]
            cov = [r for r in miss if r.get("bottleneck") == "커버리지"]
            print(f"\n{'-'*72}")
            print(f"병목: 놓친 {len(miss)}건 중 검색 {len(srch)}건 / "
                  f"커버리지 {len(cov)}건")
            for r in srch:
                print(f"  [검색]     {r['q']:22} DB에 있음 — {r['bottleneck_where']}")
            for r in cov:
                print(f"  [커버리지] {r['q']:22} {r['bottleneck_where']}")
            print("\n  → " + ("더 긁는 것보다 **검색을 고치는 것**이 먼저다."
                              if len(srch) > len(cov)
                              else "검색보다 **커버리지 확장**이 먼저다."))

        if args.json:
            pathlib.Path(args.json).write_text(
                json.dumps({"summary": summary, "score": {"ok": ok, "total": n},
                            "rows": rows}, ensure_ascii=False, indent=2),
                encoding="utf-8")
            print(f"저장: {args.json}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
