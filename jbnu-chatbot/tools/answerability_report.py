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

# (주제, 질문, 기대, 인용문에 반드시 들어갈 말)
# must_contain 은 **원문에 실재하는 문구**만 쓴다. 없는 걸 기대값에 넣으면
# 그건 기준이 아니라 소원이다.
QUESTIONS: list[tuple[str, str, str, str]] = [
    ("학사", "수강신청 언제야", A, "수강신청"),
    ("학사", "수강신청 정정", D, ""),        # 원문에 정정 안내가 없다
    ("학사", "재수강 규정", A, "재이수"),
    ("학사", "계절학기 수강료", A, "수강료"),
    ("학사", "성적 A+ 몇 점", A, "4.5"),
    ("학사", "성적 이의신청", A, "이의"),
    ("학사", "휴학 신청", A, "휴학"),
    ("학사", "복학 신청", A, "복학"),
    ("학사", "자퇴 절차", A, "자퇴"),
    ("학사", "전과 신청", A, "전과"),
    ("학사", "복수전공 신청", A, "복수전공"),
    ("학사", "부전공 이수학점", A, "부전공"),
    ("학사", "조기졸업 요건", A, "조기졸업"),
    ("학사", "학점포기", D, ""),            # 원문에 없다
    ("학사", "편입학 학점인정", A, "편입"),
    ("증명", "증명서 발급", A, "증명서"),
    ("장학", "1종 장학금 얼마야", A, "등록금 전액"),
    ("장학", "국가장학금 신청", A, "국가장학금"),
    ("장학", "교내 장학금 종류", A, "장학"),
    ("장학", "근로장학생", A, "근로"),
    ("등록", "등록금 납부 기간", A, "등록금"),
    ("등록", "등록금 분할납부", A, "분할"),
    ("생활", "기숙사 신청", A, "생활관"),
    ("생활", "차량 등록", A, "차량"),
    ("상담", "취업 상담", A, "상담"),
    ("상담", "현장실습", A, "현장실습"),
    ("학과", "기계공학과 교수", A, "교수"),
    ("학과", "간호대학 연혁", A, "간호"),
    ("학과", "컴퓨터인공지능학부 교육과정", A, "교과과정"),
    ("기타", "총장 누구야", A, "총장"),
    # ── 답하면 안 되는 것 ──────────────────────────────────────────
    ("보류", "기숙사 통금", D, ""),          # 자료 없음
    ("보류", "분실물 찾기", D, ""),          # 자료 없음
    ("보류", "졸업요건", D, ""),             # 학과마다 다름 → 되물어야
    ("보류", "안녕하세요", D, ""),           # 인사말
    ("보류", "오늘 날씨", D, ""),            # 학교와 무관
    ("보류", "내 성적 알려줘", D, ""),        # 로그인 뒤 개인정보
]


def judge(q: str, expect: str, must: str, r) -> tuple[str, str]:
    """(판정, 사유). 답해야 하는데 안 하면 miss, 하면 안 되는데 하면 wrong."""
    answered = r.outcome is ss.Outcome.FOUND
    if expect == D:
        return ("OK", "") if not answered else ("WRONG", "답하면 안 되는데 답함")
    if not answered:
        return "MISS", f"답해야 하는데 {r.outcome.value}"
    quote = (r.top.quote_text or r.top.text) if r.top else ""
    if must and must not in quote and must not in (r.top.quote_path or ""):
        return "WRONG", f"'{must}' 가 인용문에 없음"
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
            r = ss.search(conn, q, repo=repo)
            ms = (time.perf_counter() - t0) * 1000
            lat.append(ms)
            v, why = judge(q, expect, must, r)
            verdicts[v] += 1
            top = r.top
            rows.append({
                "topic": topic, "q": q, "expect": expect, "verdict": v,
                "why": why, "outcome": r.outcome.value,
                "site": top.site_name if top else "",
                "path": (top.quote_path or top.path) if top else "",
                "quote": ((top.quote_text or top.text)[:120]
                          .replace("\n", " ") if top else ""),
                "ms": round(ms), "defer": r.defer_reason,
            })

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
        print(f"정확도 {ok}/{n} = {ok/n:.0%}   "
              f"(✅ {ok} · △놓침 {verdicts['MISS']} · ❌틀림 {verdicts['WRONG']})")
        print(f"응답 중앙값 {sorted(lat)[len(lat)//2]:.0f}ms")
        # ★ 틀린 것과 놓친 것을 같은 무게로 세지 않는다.
        #   놓치면 학생이 다른 데를 찾는다. 틀리면 잘못된 곳으로 간다.
        print(f"★ 확신하고 틀린 것 {verdicts['WRONG']}건 — 이게 0에 가까워야 배포할 수 있다")

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
