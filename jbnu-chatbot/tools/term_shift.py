"""이름이 갈렸을 때 우리 사본이 따라왔나 — 낡은 인용을 세는 도구.

    python tools/term_shift.py --db data/jbnu.db --old OASIS --new JUMP

★ 왜 만들었나 (2026-08-12 사고)
  전북대가 학사시스템을 OASIS → JUMP 로 갈아탔다. 우리 사본은 그 전 것이었고
  챗봇은 "OASIS에서 자퇴 신청..." 이라고 답했다.

  ★ '확신 오답 0건' 이 이걸 못 잡았다
    must 가 주제어('자퇴')라 인용에 '자퇴' 만 있으면 통과한다.
    'OASIS' 가 죽은 이름인지는 안 본다.
        확신 오답   우리 추론이 틀림   ← 관리 중
        낡은 인용   원문이 바뀜       ← 칸이 없었다

★ 죽은 이름을 자동으로 찾는 건 아직 못 한다
  상보분포로 찾자는 안이 있었지만, 동의어 판정 때 배운 대로 상보분포만으로는
  부족하다 — 그냥 다른 주제인 쌍도 많이 걸린다. 진짜 신호는 **시간**인데
  우리 크롤은 매일 전량이라 fetched_at 이 다 같아져서 시간 신호가 없다.
  page_change 이력이 쌓여야 볼 수 있다.
  그때까지는 사람이 이름 쌍을 알려주면 이 도구가 세어 준다.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from store import repo  # noqa: E402

# 사고가 난 페이지들. 여기가 바뀌었는지가 검증의 핵심이다.
WATCH = [
    "https://www.jbnu.ac.kr/web/academic/information/sub01.do",   # 휴학 / 복학
    "https://www.jbnu.ac.kr/web/academic/information/sub02.do",   # 자퇴 / 제적
]


def counts(conn, term: str) -> tuple[int, int]:
    s = conn.execute("SELECT COUNT(*) FROM page_section WHERE text LIKE ?",
                     (f"%{term}%",)).fetchone()[0]
    p = conn.execute(
        "SELECT COUNT(DISTINCT page_url) FROM page_section WHERE text LIKE ?",
        (f"%{term}%",)).fetchone()[0]
    return s, p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--old", default="OASIS")
    ap.add_argument("--new", default="JUMP")
    args = ap.parse_args(argv)

    conn = repo.connect(args.db, readonly=True)
    try:
        os_, op = counts(conn, args.old)
        ns, np_ = counts(conn, args.new)
        print("=" * 66)
        print(f"이름 갈림  {args.old} → {args.new}")
        print("=" * 66)
        print(f"  {args.old:8} 섹션 {os_:5,} · 페이지 {op:4,}   ← 줄어야 한다")
        print(f"  {args.new:8} 섹션 {ns:5,} · 페이지 {np_:4,}   ← 늘어야 한다")
        both = conn.execute(
            """SELECT COUNT(DISTINCT page_url) FROM page_section
                WHERE text LIKE ? AND page_url IN
                  (SELECT page_url FROM page_section WHERE text LIKE ?)""",
            (f"%{args.old}%", f"%{args.new}%")).fetchone()[0]
        print(f"  둘 다 있는 페이지 {both}쪽 (학교가 갈아타는 중이면 늘어난다)")

        print()
        print("사고가 난 페이지")
        for u in WATCH:
            r = conn.execute(
                "SELECT title, last_attempt_at FROM page_registry WHERE page_url = ?",
                (u,)).fetchone()
            if r is None:
                print(f"  {u[-24:]:26} (DB 에 없음)")
                continue
            o = conn.execute(
                "SELECT COUNT(*) FROM page_section WHERE page_url=? AND text LIKE ?",
                (u, f"%{args.old}%")).fetchone()[0]
            n = conn.execute(
                "SELECT COUNT(*) FROM page_section WHERE page_url=? AND text LIKE ?",
                (u, f"%{args.new}%")).fetchone()[0]
            ok = "✅ 따라옴" if n and not o else ("⚠ 아직 옛 이름" if o else "· 둘 다 없음")
            print(f"  {(r['title'] or '')[:14]:16} {args.old} {o:2} · "
                  f"{args.new} {n:2}   수집 {(r['last_attempt_at'] or '')[:16]}  {ok}")

        print()
        print(f"{args.old} 를 아직 담고 있는 호스트")
        for h, k in conn.execute(
                """SELECT r.host, COUNT(*) FROM page_section s
                     JOIN page_registry r ON r.page_url = s.page_url
                    WHERE s.text LIKE ? GROUP BY r.host ORDER BY 2 DESC LIMIT 8""",
                (f"%{args.old}%",)):
            print(f"  {h:28} {k:4}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
