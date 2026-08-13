"""d0 최상위 블록을 색인하면 부모-자식이 같이 후보에 오는가 — 접기 규칙이 필요한지.

    python tools/d0_overlap.py --db data/jbnu.db [--short]

★ 왜 미리 재나
  부모 본문은 자식을 통째로 품는다. 자식이 걸리는 질의에는 부모도 걸린다.
      1등  휴학 절차 (부모, 611자)
      2등  휴학 절차 > 휴학일자 입력 방법 (자식)
  같은 내용인데 마진이 작아 보이고, 그럼 page_level 로 잘못 내려간다.
  색인을 늘려서 오히려 '문서까지' 가 **늘어날** 수 있다.

  안 일어나면 접기 규칙을 안 만들면 되고, 자주 일어나면 반드시 필요하다.
  만들기 전에 센다 — 되묻기 때와 같은 순서다.

★ 색인을 실제로 바꾸지 않고 잰다
  지금 후보(잎)들의 d0 조상이 같은 질의에 걸리는지를 본다.
  걸리면 색인을 늘렸을 때 둘 다 후보에 올랐을 것이다.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import section_search as ss  # noqa: E402
from store import repo  # noqa: E402
from tools.answerability_report import QUESTIONS, shorten  # noqa: E402

TOP = 20          # 마진에 실제로 영향을 주는 앞쪽만 본다


def d0_ancestor(conn, section_key: str) -> dict | None:
    """그 잎의 최상위 조상 (없으면 자기 자신이 d0 이거나 고아)."""
    cur = section_key
    for _ in range(8):
        r = conn.execute(
            """SELECT section_key, parent_key, path, text
                 FROM page_section WHERE section_key = ?""", (cur,)).fetchone()
        if r is None:
            return None
        if not r["parent_key"]:
            return {"key": r["section_key"], "path": r["path"] or "",
                    "text": r["text"] or ""}
        cur = r["parent_key"]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--short", action="store_true")
    args = ap.parse_args(argv)

    conn = repo.connect(args.db, readonly=True)
    stat = collections.Counter()
    detail = []
    try:
        for topic, q0, expect, must in QUESTIONS:
            q = q0
            if args.short:
                q = shorten(conn, q0, repo=repo)
                if q != q0 and expect != "answer":
                    continue
            r = ss.search(conn, q, repo=repo)
            toks = r.query_tokens or []
            if not toks or not r.hits:
                continue
            # 그 페이지에 d0 가 둘 이상일 때만 색인 대상이다
            page = r.top.page_url if r.top else None
            if page is None:
                continue
            n_d0 = conn.execute(
                """SELECT COUNT(*) FROM page_section
                    WHERE page_url = ? AND parent_key IS NULL AND is_leaf = 0""",
                (page,)).fetchone()[0]
            if n_d0 < 2:
                stat["대상 아님(d0 1개)"] += 1
                continue
            stat["대상"] += 1

            # 앞쪽 후보들의 d0 조상이 같은 질의에 걸리나
            anc: dict[str, int] = {}
            for h in r.hits[:TOP]:
                a = d0_ancestor(conn, h.section_key)
                if a is None or a["key"] == h.section_key:
                    continue
                hit = any(t in a["text"] or t in a["path"] for t in toks)
                if hit:
                    anc[a["path"][:24]] = anc.get(a["path"][:24], 0) + 1
            if anc:
                stat["부모도 걸림"] += 1
                detail.append((q, sum(anc.values()), len(anc),
                               sorted(anc.items(), key=lambda x: -x[1])[:3]))
            else:
                stat["부모는 안 걸림"] += 1
    finally:
        conn.close()

    print("=" * 72)
    print("부모-자식이 동시에 후보에 오는 빈도 — 접기 규칙이 필요한가")
    print("=" * 72)
    for k in ("대상", "부모도 걸림", "부모는 안 걸림", "대상 아님(d0 1개)"):
        if stat[k]:
            print(f"  {k:18} {stat[k]:3}")
    if stat["대상"]:
        print(f"\n★ 대상 {stat['대상']}문항 중 {stat['부모도 걸림']}건 "
              f"({stat['부모도 걸림'] / stat['대상']:.0%}) 에서 부모가 같이 걸린다")
    print()
    for q, n, k, top in detail:
        print(f"  {q:18} 자식 {n}개가 부모 {k}종과 겹침")
        for path, cnt in top:
            print(f"        {path:26} 자식 {cnt}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
