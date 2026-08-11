"""무엇에 답할 수 있고 무엇에 답할 수 없는가 — 실제 질문으로 잰다.

커버리지 숫자만으로는 학생에게 뭘 줄 수 있는지 알 수 없다.
'페이지 3,320개 수집' 은 '수강신청 정정기간을 답할 수 있다' 와 다른 말이다.
그래서 질문을 넣어 보고, 나온 인용문이 실제로 그 질문의 답인지까지 본다.

★ 기대값을 코드에 박아 둔다
  사람이 눈으로 판정하면 다음번에 재현이 안 된다.
  고칠 때마다 정확도가 오르는지 내리는지 숫자로 봐야 한다.

    expect="answer"  답해야 한다. 인용문에 must_contain 이 들어 있어야 맞다.
    expect="defer"   답하면 안 된다. 자료가 없거나 학과가 갈리는 질문.

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

from skill import section_search as ss  # noqa: E402
from store import repo  # noqa: E402

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
    ("수강신청", "학점포기", D, ""),
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
    ("보류", "기숙사 통금", D, ""),
    ("보류", "분실물 찾기", D, ""),
    ("보류", "안녕하세요", D, ""),
    ("보류", "오늘 날씨", D, ""),
    ("보류", "내 성적 알려줘", D, ""),
]


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


def judge(q: str, expect: str, must: str, r) -> tuple[str, str]:
    """(판정, 사유). 답해야 하는데 안 하면 miss, 하면 안 되는데 하면 wrong."""
    answered = r.outcome is ss.Outcome.FOUND
    if expect == D:
        return ("OK", "") if not answered else ("WRONG", "답하면 안 되는데 답함")
    if not answered:
        return "MISS", f"답해야 하는데 {r.outcome.value}"
    top = getattr(r, "top", None) or (r.hits[0] if r.hits else None)
    if top is None:
        return "MISS", "결과가 비었다"
    if hasattr(top, "quote_text"):
        quote = top.quote_text or top.text
        where = top.quote_path or ""
        # 페이지 단위로 답할 때 우리가 주장하는 것은 '이 문서에 있다' 이지
        # '이 문단이 답이다' 가 아니다. 그러면 문서 제목으로 채점해야 맞다.
        if getattr(r, "page_level", False):
            where = f"{where} {top.page_title}"
    else:                                   # 공지 — 제목만 본다
        quote, where = top.title, top.board_name or ""
    if must and must not in quote and must not in where:
        return "WRONG", f"'{must}' 가 결과에 없음"
    return "OK", ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--json", default="")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    conn = repo.connect(args.db)
    try:
        summary = repo.coverage_summary(conn)
        print(f"색인 잎 {summary['indexed_leaves']:,} · 인용 가능 페이지 "
              f"{summary['by_status']['ok']:,}\n")

        rows, lat = [], []
        verdicts: collections.Counter = collections.Counter()
        for topic, q, expect, must in QUESTIONS:
            t0 = time.perf_counter()
            # 공지는 제목 검색이 담당한다. 같은 잣대로 잰다.
            r = (ss.search_notices(conn, q, repo=repo) if topic == "공지"
                 else ss.search(conn, q, repo=repo))
            ms = (time.perf_counter() - t0) * 1000
            lat.append(ms)
            v, why = judge(q, expect, must, r)
            verdicts[v] += 1
            top = getattr(r, "top", None) or (r.hits[0] if r.hits else None)
            rows.append({
                "topic": topic, "q": q, "expect": expect, "verdict": v,
                "why": why, "outcome": r.outcome.value,
                "site": getattr(top, "site_name", "") if top else "",
                "path": (getattr(top, "quote_path", "")
                         or getattr(top, "title", "")) if top else "",
                "quote": ((getattr(top, "quote_text", "")
                           or getattr(top, "title", ""))[:120]
                          .replace("\n", " ") if top else ""),
                "ms": round(ms), "defer": getattr(r, "defer_reason", ""),
            })
            if v != "OK" and expect == A:
                kind, where = bottleneck(conn, q, must)
                rows[-1]["bottleneck"] = kind
                rows[-1]["bottleneck_where"] = where

        icon = {"OK": "✅", "MISS": "△", "WRONG": "❌"}
        if not args.quiet:
            cur = None
            for r in rows:
                if r["topic"] != cur:
                    cur = r["topic"]
                    print(f"── {cur} ──")
                note = f"  ← {r['why']}" if r["why"] else ""
                if r["verdict"] == "MISS" and r.get("defer"):
                    note += f"  [{r['defer']}]"
                print(f"  {icon[r['verdict']]} {r['q']:20} "
                      f"{r['site'][:11]:13} {r['path'][:30]}{note}")
                if r["verdict"] == "WRONG" and r["quote"]:
                    print(f"        인용: {r['quote'][:90]}")

        n = len(rows)
        ok = verdicts["OK"]
        print(f"\n{'='*72}")
        print(f"균등 정확도 {ok}/{n} = {ok/n:.0%}   "
              f"(✅ {ok} · △놓침 {verdicts['MISS']} · ❌틀림 {verdicts['WRONG']})")
        print(f"응답 중앙값 {sorted(lat)[len(lat)//2]:.0f}ms")

        # ★ 62% 항목의 오답과 5% 항목의 오답을 같은 무게로 세면 안 된다.
        #   균등 정확도는 우리가 문항을 몇 개 넣었느냐에 좌우된다.
        #   학생이 겪는 것은 수요로 가중한 쪽이다. 둘을 나란히 낸다.
        per: dict[str, list[int]] = {}
        for r in rows:
            per.setdefault(r["topic"], []).append(1 if r["verdict"] == "OK" else 0)
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
        print(f"★ 확신하고 틀린 것 {verdicts['WRONG']}건 — 이게 0에 가까워야 배포할 수 있다")

        # ★ 병목 — 더 긁을 것인가, 검색을 고칠 것인가
        #   DB 에 있는데 못 찾은 것이면 더 긁어도 나아지지 않는다.
        #   DB 에 없는 것이면 검색을 아무리 고쳐도 안 나온다.
        miss = [r for r in rows if r["verdict"] != "OK" and r["expect"] == A]
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
