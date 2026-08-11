"""'문서까지' 를 두 갈래로 쪼갠다 — 이게 다음 3주를 정한다.

    답이 하나인데 못 집음    → 자족성·집계 승격이 고칠 수 있는 것
    답이 여럿인데 안 정해짐   → 되묻기가 필요한 것

★ 판별은 관측으로 한다
  그 페이지의 **최상위 블록**(depth 0) 중 질문의 핵심어를 제목에 담은 게 몇 개인가.
    1개  → 답이 하나다. 우리가 못 집은 것이다.
    2개+ → 문서가 실제로 갈라 놓은 것이다. 질문이 덜 정해졌다.

  형제(parent_key)가 아니라 최상위 블록을 본다 — 이미 한 번 틀렸다.
  휴학 갈래(일반/군입대/임신출산육아/창업)는 형제가 아니라 최상위 블록이었고,
  parent_key 형제는 표의 행·조항 번호였다.

    python tools/split_pagelevel.py --db data/jbnu.db
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import section_search as ss  # noqa: E402
from store import repo  # noqa: E402
from tools.answerability_report import QUESTIONS  # noqa: E402


def top_blocks(conn, page_url: str) -> list[dict]:
    """그 페이지의 최상위 블록들 (부모 없음)."""
    return [{"path": r["path"] or "", "chars": len(r["text"] or ""),
             "leaf": r["is_leaf"]}
            for r in conn.execute(
                """SELECT path, text, is_leaf FROM page_section
                    WHERE page_url = ? AND parent_key IS NULL
                    ORDER BY ordinal""", (page_url,))]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--only", default="")
    args = ap.parse_args(argv)

    conn = repo.connect(args.db, readonly=True)
    single, multi = [], []
    try:
        for topic, q, expect, must in QUESTIONS:
            if args.only and args.only not in q:
                continue
            if expect != "answer":
                continue
            r = ss.search(conn, q, repo=repo)
            if not (r.outcome is ss.Outcome.FOUND and r.page_level and r.top):
                continue
            toks = r.query_tokens or []
            blocks = top_blocks(conn, r.top.page_url)
            # 질문의 낱말을 **제목**에 담은 최상위 블록
            hitting = [b for b in blocks
                       if any(t in b["path"] for t in toks)]
            row = {"q": q, "topic": topic, "n": len(hitting),
                   "titles": [b["path"][:24] for b in hitting[:6]],
                   "url": r.top.page_url, "blocks": len(blocks)}
            (multi if len(hitting) >= 2 else single).append(row)
    finally:
        conn.close()

    print("=" * 72)
    print(f"답이 하나인데 못 집음  {len(single)}건  → 자족성·집계 승격이 고칠 자리")
    print("=" * 72)
    for r in single:
        t = r["titles"][0] if r["titles"] else "(핵심어 담은 블록 없음)"
        print(f"  {r['q']:20} 최상위 {r['blocks']:3}개 중 1개: {t}")

    print()
    print("=" * 72)
    print(f"답이 여럿인데 안 정해짐 {len(multi)}건  → 되묻기가 필요한 자리")
    print("=" * 72)
    for r in multi:
        print(f"  {r['q']:20} {r['n']}갈래")
        print(f"      {' / '.join(r['titles'])}")

    n = len(single) + len(multi)
    if n:
        print()
        print(f"페이지 단위 {n}건 = 못 집음 {len(single)} ({len(single)/n:.0%}) "
              f"+ 안 정해짐 {len(multi)} ({len(multi)/n:.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
