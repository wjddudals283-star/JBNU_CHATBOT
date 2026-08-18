"""검수 시트에 넣을 CSV — 봇이 지금 내는 문서를 미리 채운다.

    python tools/review_sheet.py --db data/jbnu.db --out review.csv

★ 왜 이 모양인가 (2026-08-17)
  46문항의 '맞는 문서인가' 를 우리는 자동으로 못 잰다.
  '성적 이의신청' 이 **정보공개 이의신청(행정)** 문서로 통과하고 있었다 —
  학사 이의신청과 낱말이 겹쳐서 문자열로는 안 갈린다.
  판정에 생성 모델을 쓰면 그 자를 검증할 자가 또 필요해진다.

★ 그래서 사람이 본다. 다만 **싸게** 본다
      무거움   대표가 46번 문서를 찾아 URL 을 복사한다
      가벼움   봇이 낸 문서를 미리 채워 두고 **O/X 만 찍는다**  ← 이 도구
  X 인 것만 올바른 문서를 적으면 된다 (아마 5~10건).
  시트가 총학 계정에 있어서 집행부가 나눠 찍을 수도 있다.

★ '의심' 칸은 판정이 아니라 **볼 순서**다
  질문 낱말이 문서 제목·경로에 하나도 없으면 ❗ 를 찍는다.
  판정으로 쓰면 거짓 안심이 되지만(이 신호는 '성적 이의신청' 을 못 잡는다),
  표시로 두면 어디부터 볼지 공짜로 알려준다.
  실제로 이 신호가 '증명서 발급 → 행동강령' 을 새로 찾아냈다.
"""

from __future__ import annotations

import argparse
import csv
import logging
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import section_search as ss  # noqa: E402
from store import repo                  # noqa: E402
from tools.answerability_report import QUESTIONS, load_review  # noqa: E402

HEADER = ["질문", "봇이 낸 문서", "URL", "인용 앞부분", "의심",
          "검수 OX", "올바른 문서(X일 때만)", "메모"]


def suspicious(q: str, where: str) -> str:
    """질문 낱말이 문서 제목·경로에 하나도 없으면 ❗.

    ★ 판정이 아니다 — 볼 순서다. 이 신호는 '성적 이의신청' 을 못 잡는다
      (경로에 '이의신청' 이 있어서). 그래도 '증명서 발급 → 행동강령' 을 찾았다.
    """
    toks = [t for t in re.split(r"[^0-9A-Za-z가-힣+]+", q) if len(t) >= 2]
    return "" if any(t in where for t in toks) else "❗"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--out", default="review.csv")
    args = ap.parse_args(argv)
    logging.disable(logging.CRITICAL)
    conn = repo.connect(pathlib.Path(args.db), readonly=True)
    review = load_review()

    rows = []
    for _t, q, expect, _must in QUESTIONS:
        if expect != "answer":
            continue          # 답하면 안 되는 문항은 검수할 문서가 없다
        r = ss.search(conn, q, repo=repo)
        top = getattr(r, "top", None)
        if r.outcome is not ss.Outcome.FOUND or top is None:
            rows.append([q, "(답 못 함)", "", "", "", "", "", ""])
            continue
        doc = " · ".join(x for x in (top.site_name, top.page_title) if x)
        where = f"{top.site_name} {top.page_title} {top.quote_path or top.path}"
        quote = " ".join((top.quote_text or top.text or "").split())[:80]
        prev = review.get(q) or {}
        ox = "O" if prev.get("ok") is True else ("X" if prev.get("ok") is False else "")
        rows.append([q, doc, top.page_url, quote, suspicious(q, where),
                     ox, prev.get("expected") or "", prev.get("note") or ""])
    conn.close()

    out = pathlib.Path(args.out)
    # ★ BOM 을 붙인다 — 구글시트·엑셀이 UTF-8 을 알아보게. 안 그러면 한글이 깨진다.
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)

    n_sus = sum(1 for r in rows if r[4])
    print(f"→ {out}  ({len(rows)}행)")
    print(f"   ❗ 먼저 볼 것 {n_sus}건")
    for r in rows:
        if r[4]:
            print(f"      {r[0]:18} → {r[1][:40]}")
    print("\n시트에 넣고 '검수 OX' 칸에 O/X 만 찍으면 됩니다.")
    print("X 인 것만 '올바른 문서' 를 적어 주세요.")
    print(f"돌려받은 결과는 {ROOT / 'config' / 'answer_review.yaml'} 로 옮깁니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
