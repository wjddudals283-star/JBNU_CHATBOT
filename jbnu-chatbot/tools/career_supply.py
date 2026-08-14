"""'취업·비교과' 를 답할 자료가 **실제로 있나** — 코퍼스에 물어본다.

    python tools/career_supply.py --db data/jbnu.db

★ 왜 재나 (2026-08-14)
  대표가 원하는 것과 나가는 것이 달랐다.

      원하는 것   특강·캠프·멘토링·자격증·인턴십·공모전·어학·현장실습·설명회·박람회
                 → "지금 신청할 수 있는 것" 의 목록
      나가는 것   career.jbnu.ac.kr 프로그램명 + **전화번호 표**

  ★ 없으면 크롤을 늘려야 하고, 있으면 검색이 못 집는 것이다.
    **둘은 대책이 완전히 다르다.** 그래서 만들기 전에 잰다.

★ 낱말은 대표가 준 정의를 그대로 쓴다
  우리가 상상한 목록이 아니다. 다만 코퍼스가 실제로 뭐라고 부르는지는
  같이 찍는다 — 학교 말과 학생 말이 다르면 그것도 답의 일부다.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from store import repo  # noqa: E402

# 대표 정의 (2026-08-14, 그대로 옮김):
#   "여기서 말한 취업은 채용도 말하는거지만 대부분 취업에 도움되는 활동들"
ACTIVITY = ["특강", "캠프", "멘토링", "자격증", "인턴", "공모전", "어학",
            "취업동아리", "현장실습", "아카데미", "워크숍", "워크샵",
            "설명회", "박람회"]
HIRING = ["채용", "구인", "모집공고"]
FRESH_DAYS = 30       # '지금 신청할 수 있는 것' 의 대리 지표


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--today", help="YYYY-MM-DD (기본: 오늘)")
    args = ap.parse_args(argv)
    today = (dt.date.fromisoformat(args.today) if args.today
             else dt.date.today())

    conn = repo.connect(pathlib.Path(args.db), readonly=True)
    try:
        total = conn.execute("SELECT COUNT(*) FROM notice_item").fetchone()[0]
        print("=" * 72)
        print(f"취업·비교과 공급량 — 모아둔 공지 {total:,}건 · 기준일 {today}")
        print("=" * 72)

        # ── 1. 낱말별 ────────────────────────────────────────
        print(f"\n{'낱말':12} {'제목에 든 공지':>12}")
        print("─" * 30)
        keys: set[str] = set()
        for w in ACTIVITY + HIRING:
            rows = conn.execute(
                "SELECT item_key FROM notice_item WHERE title LIKE ?",
                (f"%{w}%",)).fetchall()
            keys |= {r[0] for r in rows}
            print(f"{w:12} {len(rows):>12,}")
        print("─" * 30)
        pct = len(keys) / total if total else 0
        print(f"{'합집합':12} {len(keys):>12,}  (전체의 {pct:.1%})")

        if not keys:
            print("\n★ 자료가 없다 — 크롤을 늘려야 한다. 검색을 고쳐도 안 나온다.")
            return 0

        marks = ",".join("?" * len(keys))
        rows = conn.execute(
            f"""SELECT host, board_name, published_at, title
                  FROM notice_item WHERE item_key IN ({marks})""",
            list(keys)).fetchall()

        # ── 2. 어디에 있나 ───────────────────────────────────
        by = collections.Counter((r["host"], r["board_name"] or "-")
                                 for r in rows)
        print(f"\n어느 게시판에 있나 — {len(by)}종 (상위 10)")
        print("─" * 66)
        for (host, board), n in by.most_common(10):
            print(f"  {host[:30]:32} {board[:18]:20} {n:>5,}")

        # ★ 대표가 지목한 원천이 실제로 들어와 있나 — **이름으로 확인한다**
        print("\n지목된 원천이 들어와 있나")
        for host in ("www.jbnu.ac.kr", "career.jbnu.ac.kr"):
            n = repo.notice_total(conn, host=host)
            mark = "" if n else "   ★ 공지가 하나도 없다 — 크롤 대상이 아니다"
            print(f"  {host:24} 공지 {n:>5,}건{mark}")

        # ── 3. 지금 신청할 수 있나 (최신성) ──────────────────
        buckets: collections.Counter = collections.Counter()
        for r in rows:
            p = (r["published_at"] or "")[:10]
            try:
                age = (today - dt.date.fromisoformat(p)).days
            except ValueError:
                buckets["날짜 없음"] += 1
                continue
            buckets["최근 7일" if age <= 7 else
                    f"8~{FRESH_DAYS}일" if age <= FRESH_DAYS else
                    "31~90일" if age <= 90 else "90일 초과"] += 1
        print(f"\n최신성 — '지금 신청할 수 있는 것' 의 대리 지표")
        print("─" * 40)
        for k in ("최근 7일", f"8~{FRESH_DAYS}일", "31~90일", "90일 초과",
                  "날짜 없음"):
            print(f"  {k:12} {buckets[k]:>6,}건")
        fresh = buckets["최근 7일"] + buckets[f"8~{FRESH_DAYS}일"]
        print(f"\n  → {FRESH_DAYS}일 이내 {fresh:,}건이 목록에 낼 만한 후보다")

        # ★ 마감일은 우리에게 없다. 없는 걸 있는 척하지 않는다.
        print("\n★ 마감일은 공지 자료에 **없다**")
        print("  notice_item 에는 게시일만 있다. 마감은 제목·본문 안에 글로 적혀 있고")
        print("  우리는 본문을 안 읽는다. 그래서 '마감 안 지난 것' 은 못 센다 —")
        print("  게시일로 대신 재고 있다는 걸 위 표에 그대로 적었다.")
        print("  총학 시트(T4)에만 마감일 칸이 있다.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
