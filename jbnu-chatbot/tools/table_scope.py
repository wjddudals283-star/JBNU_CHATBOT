"""병합 셀 때문에 칸이 어긋난 표가 얼마나 되나 — 파서를 고칠 값어치.

    python tools/table_scope.py --db data/jbnu.db

★ 원문 없이도 잰다
  colspan/rowspan 을 안 펴면 행마다 칸 수가 달라진다. 파싱 결과만으로 보인다.
      8칸  적용년도 | 학과 | 교양(42이상) | … | 계
      2칸  전필 | 전선            ← 병합된 자리
      9칸  2010년이후입학자 | 사학 | …

★ 두 숫자를 갈라 본다 — 안 가르면 판단이 안 선다
  우리 46문항에 걸리는 수는 작다. 그런데 **형식 안내가 보내는 곳**은 다르다.
  '졸업요건은 학과마다 달라요 → 사학과 졸업요건' 이라고 보내 놓고
  도착지의 표가 깨져 있으면 그 기능을 켠 의미가 거기서 사라진다.

★ 파서를 고치면 재수집이 아니라 **재파싱**이다
  다만 원문 스냅샷이 필요하다 (crawler/snapshots.py). 오늘 03:10 부터 쌓인다.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import sys

# 윈도우 콘솔은 기본이 cp949 라 '—' 하나에 죽는다. 다른 도구와 같게 맞춘다.
sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from store import repo  # noqa: E402

HQ = "www.jbnu.ac.kr"


def is_ragged(text: str) -> bool | None:
    """행마다 칸 수가 다른가. 표가 아니면 None."""
    lines = [ln for ln in (text or "").split("\n") if "|" in ln]
    if len(lines) < 2:
        return None
    return len(collections.Counter(len(ln.split("|")) for ln in lines)) > 1


def survey(conn, *, where: str = "", params: tuple = ()) -> tuple[int, int]:
    sql = ("SELECT s.raw_text FROM page_section s "
           "JOIN page_registry r ON r.page_url = s.page_url "
           "WHERE s.kind = 'table'" + (f" AND {where}" if where else ""))
    tot = bad = 0
    for (t,) in conn.execute(sql, params):
        v = is_ragged(t)
        if v is None:
            continue
        tot += 1
        bad += 1 if v else 0
    return tot, bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    args = ap.parse_args(argv)
    conn = repo.connect(args.db, readonly=True)
    try:
        print("=" * 68)
        print("칸이 어긋난 표 — 병합 셀을 안 편 결과")
        print("=" * 68)
        rows = [
            ("전체", "", ()),
            ("본부", "r.host = ?", (HQ,)),
            ("학과 사이트 전체", "r.host <> ?", (HQ,)),
            ("학과 · 졸업요건 페이지", "r.host <> ? AND r.title LIKE ?", (HQ, "%졸업%")),
            ("학과 · 교육과정 페이지", "r.host <> ? AND r.title LIKE ?", (HQ, "%교육과정%")),
        ]
        for label, where, params in rows:
            tot, bad = survey(conn, where=where, params=params)
            if tot:
                print(f"  {label:22} {tot:6,}개 중 {bad:6,}개  {bad / tot:6.1%}")
        print()
        print("  ★ '학과 · 졸업요건' 이 형식 안내가 학생을 보내는 바로 그 자리다.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
