"""무엇에 답할 수 있고 무엇에 답할 수 없는가 — 실제 질문으로 잰다.

커버리지 숫자만으로는 학생에게 뭘 줄 수 있는지 알 수 없다.
'페이지 3,164개 수집' 은 '수강신청 정정기간을 답할 수 있다' 와 다른 말이다.
그래서 질문을 넣어 보고, 나온 답을 그대로 센다.

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

# 총학에 실제로 들어올 법한 질문. 주제별로 고르게 깔았다.
QUESTIONS: list[tuple[str, str]] = [
    ("학사", "수강신청 언제야"),
    ("학사", "수강신청 정정기간"),
    ("학사", "재수강 규정"),
    ("학사", "계절학기 수강료"),
    ("학사", "성적 A+ 몇 점"),
    ("학사", "성적 이의신청"),
    ("학사", "휴학 신청"),
    ("학사", "복학 신청"),
    ("학사", "자퇴 절차"),
    ("학사", "전과 신청"),
    ("학사", "복수전공 신청"),
    ("학사", "부전공 이수학점"),
    ("학사", "졸업요건"),
    ("학사", "조기졸업 요건"),
    ("학사", "학점포기 제도"),
    ("학사", "편입학 학점인정"),
    ("증명", "증명서 발급"),
    ("증명", "졸업증명서 인터넷 발급"),
    ("장학", "1종 장학금 얼마야"),
    ("장학", "국가장학금 신청"),
    ("장학", "교내 장학금 종류"),
    ("장학", "근로장학생 신청"),
    ("등록", "등록금 납부 기간"),
    ("등록", "등록금 분할납부"),
    ("생활", "기숙사 신청"),
    ("생활", "기숙사 통금"),
    ("생활", "학생식당 위치"),
    ("생활", "도서관 이용시간"),
    ("생활", "동아리 등록"),
    ("생활", "주차 요금"),
    ("생활", "분실물 찾기"),
    ("상담", "심리상담 신청"),
    ("상담", "취업 상담"),
    ("상담", "현장실습 신청"),
    ("학과", "기계공학과 교수"),
    ("학과", "간호대학 연혁"),
    ("학과", "경영학과 졸업요건"),
    ("학과", "컴퓨터공학부 교육과정"),
    ("학과", "의과대학 입학정원"),
    ("기타", "총장 누구야"),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--json", default="")
    args = ap.parse_args(argv)

    conn = repo.connect(args.db)
    try:
        summary = repo.coverage_summary(conn)
        print(f"색인 잎 {summary['indexed_leaves']:,} · 페이지 "
              f"{summary['total_pages']:,} · 인용 가능 {summary['by_status']['ok']:,}")
        print()

        rows, lat = [], []
        by_topic: dict[str, collections.Counter] = {}
        for topic, q in QUESTIONS:
            t0 = time.perf_counter()
            r = ss.search(conn, q, repo=repo)
            ms = (time.perf_counter() - t0) * 1000
            lat.append(ms)
            top = r.top
            by_topic.setdefault(topic, collections.Counter())[r.outcome.value] += 1
            rows.append({
                "topic": topic, "q": q, "outcome": r.outcome.value,
                "site": (top.site_name if top else ""),
                "path": (top.quote_path or top.path) if top else "",
                "missing": r.missing_tokens, "ms": round(ms),
                "url": top.page_url if top else "",
            })

        mark = {"found": "✅ 답함", "ambiguous": "△ 보류",
                "not_found": "✗ 없음", "no_data": "✗ 미수집",
                "no_query": "? 못알아들음"}
        cur = None
        for r in rows:
            if r["topic"] != cur:
                cur = r["topic"]
                print(f"\n── {cur} ──")
            miss = f"  (못찾은 말: {','.join(r['missing'])})" if r["missing"] else ""
            print(f"  {mark[r['outcome']]:8} {r['q']:18} "
                  f"{r['site'][:12]:14} {r['path'][:34]}{miss}")

        print(f"\n{'='*72}")
        tot = collections.Counter(r["outcome"] for r in rows)
        n = len(rows)
        print(f"전체 {n}문항 — " + " · ".join(
            f"{mark[k]} {v} ({v/n:.0%})" for k, v in tot.most_common()))
        print(f"응답시간 중앙값 {sorted(lat)[len(lat)//2]:.0f}ms")

        print("\n주제별")
        for topic, c in by_topic.items():
            ok = c.get("found", 0)
            print(f"  {topic:6} {ok}/{sum(c.values())} 답함   "
                  + " ".join(f"{k}:{v}" for k, v in c.items() if k != "found"))

        if args.json:
            pathlib.Path(args.json).write_text(
                json.dumps({"summary": summary, "rows": rows},
                           ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n저장: {args.json}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
