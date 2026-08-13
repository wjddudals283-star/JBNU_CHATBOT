"""페이지가 얼마나 자주 바뀌나 — 신선도 N 을 계산으로 뽑는다.

    python tools/change_cadence.py --db data/jbnu.db

★ N 을 손으로 정하지 않는다
  날짜형 답('수강신청 언제야')은 값으로 못 잰다 — 학기마다 바뀐다.
  형태 + 출처 + **신선도** 로 재기로 했는데, 그 'N일' 을 또 손으로 정하면 같은 실수다.
  600 상한·본부 유무 때처럼, 재보면 정할 게 없을 수도 있다.

★ 관측이 쌓여야 나온다
  page_change 에 **바뀐 것만** 한 줄씩 쌓인다. 수집이 몇 회차 돌아야 간격이 생긴다.
  지금 돌리면 '아직 이르다' 고 말한다 — 그게 맞는 답이다.
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    args = ap.parse_args(argv)
    conn = repo.connect(args.db, readonly=True)
    try:
        try:
            rows = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT page_url) FROM page_change"
            ).fetchone()
        except Exception:  # noqa: BLE001
            print("page_change 가 아직 없다. 수집을 한 번 돌리면 생긴다.")
            return 0
        print(f"기록 {rows[0]:,}줄 · 페이지 {rows[1]:,}개")
        iv = repo.change_intervals(conn)
        if len(iv) < 30:
            print(f"간격 표본 {len(iv)}개 — 아직 이르다. "
                  "N 을 뽑으려면 수집이 여러 회차 돌아야 한다.")
            print("★ 표본이 적을 때 N 을 정하면 그건 관측이 아니라 추측이다.")
            return 0
        iv.sort()
        d = collections.Counter()
        for h in iv:
            d["1일 이내" if h <= 24 else "1~7일" if h <= 168
              else "1~4주" if h <= 672 else "4주+"] += 1
        print("\n변경 간격 분포")
        for k in ("1일 이내", "1~7일", "1~4주", "4주+"):
            if d[k]:
                print(f"  {k:8} {d[k]:6}  {d[k] / len(iv):5.1%}")
        p50 = iv[len(iv) // 2]
        p90 = iv[int(len(iv) * 0.9)]
        print(f"\n중앙값 {p50 / 24:.1f}일 · 90퍼센타일 {p90 / 24:.1f}일")
        print(f"★ 신선도 N 후보 = {p90 / 24:.0f}일 "
              "(열에 아홉은 이 안에 한 번은 바뀐다 — 그보다 오래 안 바뀌었으면 의심한다)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
