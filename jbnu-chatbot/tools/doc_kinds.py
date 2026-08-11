"""정답 문서가 몇 종으로 갈리는가 — 종류를 상상하지 않고 신호로 잰다.

    python tools/doc_kinds.py --db data/jbnu.db [--short]

★ 왜 이걸 재나
  오늘 밤 실패가 전부 한 모양이었다 — 종류가 다른데 같은 규칙으로 굴렸다.
      자족성      인용엔 맞고 라벨엔 반대
      형제 노드    휴학은 사유별 블록, 복학은 한 블록
      집계 승격    졸업요건만 깨짐 — 학과마다 답이 다른 정보라서
      길이 정규화  '준공년도 : 1995년' 살리고 '여성건강간호학교실' 죽여야
      표 조각      '사유 발생시' 는 헤더 없이 뜻이 없다

  스키마에는 Procedure·Policy·Notice·Contact 가 있는데
  검색·답변에서는 전부 '섹션' 으로 뭉친다. **저장할 때만 온톨로지였다.**

★ 종류를 손으로 쓰지 않는다
  규칙을 수백 개 적으면 원칙 위반이다. 신호를 재고 몇 종으로 갈리는지 **센다.**
  가설(기준형·절차형·사실형·날짜형·공지형·표형)은 참고만 하고 판정에 안 쓴다.

★ 지금 답해야 하는 것은 하나다
  '문서까지' 가 종류별로 **뭉쳐 있나 흩어져 있나.**
  뭉쳐 있으면 종류별 규칙이 답이고, 고르게 흩어져 있으면 다른 원인이다.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import section_search as ss  # noqa: E402
from store import repo  # noqa: E402
from tools.answerability_report import QUESTIONS, judge, shorten  # noqa: E402

HQ = "www.jbnu.ac.kr"
_DATE = re.compile(r"\d{4}[.\-/년]\s*\d{1,2}[.\-/월]|\d{1,2}월\s*\d{1,2}일")
_MONEY = re.compile(r"\d[\d,]*\s*(원|만원|천원|%)")
_ORDER = re.compile(r"(^|\s)([①-⑳]|[0-9]+\.|[가-하]\.)")


def signals(conn, hit, tokens: list[str]) -> dict:
    """관측 가능한 신호만. 해석은 나중에 한다."""
    page = conn.execute(
        """SELECT host, title, section_count, table_count, leaf_count
             FROM page_registry WHERE page_url = ?""",
        (hit.page_url,)).fetchone()
    sec = conn.execute(
        "SELECT kind, raw_text, path FROM page_section WHERE section_key = ?",
        (hit.section_key,)).fetchone()
    body = " ".join(r["text"] or "" for r in conn.execute(
        "SELECT text FROM page_section WHERE page_url = ? LIMIT 40",
        (hit.page_url,)))
    n_sec = (page["section_count"] or 1) if page else 1
    txt = (sec["raw_text"] if sec else "") or ""

    # ★ '학과마다 답이 다른가' — 같은 주제 문서를 가진 호스트 수로 잰다.
    #   졸업요건이 집계 승격에서 깨진 이유가 이거였다. 규칙이 아니라 종류 문제다.
    hosts = 0
    if tokens:
        t = max(tokens, key=len)
        hosts = conn.execute(
            """SELECT COUNT(DISTINCT r.host) FROM page_registry r
                WHERE r.title LIKE ?""", (f"%{t}%",)).fetchone()[0]
    return {
        "본부": bool(page and page["host"] == HQ),
        "표비율": round((page["table_count"] or 0) / n_sec, 2) if page else 0.0,
        "표섹션": bool(sec and sec["kind"] in ("table", "table_row")),
        "날짜": bool(_DATE.search(body)),
        "금액": bool(_MONEY.search(body)),
        "순서": bool(_ORDER.search(body)),
        "학과수": hosts,
        "짧은답": len(re.sub(r"\s+", "", txt)) <= 15,
        "페이지크기": n_sec,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--short", action="store_true")
    args = ap.parse_args(argv)

    conn = repo.connect(args.db, readonly=True)
    rows = []
    try:
        for topic, q0, expect, must in QUESTIONS:
            q, m = q0, must
            if args.short:
                q = shorten(conn, q0, repo=repo)
                if q != q0:
                    if expect != "answer":
                        continue
                    m = q
            r = ss.search(conn, q, repo=repo)
            v, _ = judge(q, expect, m, r)
            if r.top is None:
                continue
            s = signals(conn, r.top, r.query_tokens or [])
            s.update({"q": q, "verdict": v, "topic": topic})
            rows.append(s)
    finally:
        conn.close()

    keys = ["본부", "표섹션", "날짜", "금액", "순서", "짧은답"]
    verdicts = ["확신", "문서까지", "쓸모없음", "모름", "보류OK", "틀림"]
    present = [v for v in verdicts if any(r["verdict"] == v for r in rows)]

    print("=" * 74)
    print("신호 × 판정 — '문서까지' 가 어느 신호에 뭉치나")
    print("=" * 74)
    print(f"  {'':10}" + "".join(f"{v:>9}" for v in present))
    for k in keys:
        line = f"  {k:10}"
        for v in present:
            n = sum(1 for r in rows if r["verdict"] == v and r[k])
            d = sum(1 for r in rows if r["verdict"] == v)
            line += f"{(f'{n}/{d}'):>9}"
        print(line)

    print()
    print("  학과수 (같은 주제 문서를 가진 호스트 수) 중앙값")
    line = f"  {'':10}"
    for v in present:
        xs = sorted(r["학과수"] for r in rows if r["verdict"] == v)
        line += f"{(str(xs[len(xs)//2]) if xs else '-'):>9}"
    print(line)
    line = f"  {'페이지크기':10}"
    for v in present:
        xs = sorted(r["페이지크기"] for r in rows if r["verdict"] == v)
        line += f"{(str(xs[len(xs)//2]) if xs else '-'):>9}"
    print(line)

    print()
    print("=" * 74)
    print("'문서까지' 문항의 신호 (뭉치는지 눈으로도 본다)")
    print("=" * 74)
    for r in rows:
        if r["verdict"] != "문서까지":
            continue
        on = [k for k in keys if r[k]]
        print(f"  {r['q']:18} 학과{r['학과수']:3} 섹션{r['페이지크기']:3}  "
              f"{' '.join(on)}")

    # 뭉침의 정도 — 각 신호가 '문서까지' 에 얼마나 쏠렸나
    print()
    pl = [r for r in rows if r["verdict"] == "문서까지"]
    other = [r for r in rows if r["verdict"] != "문서까지"]
    print("신호별 쏠림 (문서까지 비율 − 나머지 비율)")
    for k in keys:
        a = sum(1 for r in pl if r[k]) / max(len(pl), 1)
        b = sum(1 for r in other if r[k]) / max(len(other), 1)
        bar = "█" * int(abs(a - b) * 20)
        print(f"  {k:10} {a:5.0%} vs {b:5.0%}   {a-b:+.2f} {bar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
