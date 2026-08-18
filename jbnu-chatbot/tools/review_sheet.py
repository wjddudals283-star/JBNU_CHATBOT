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
from skill import manual_answers  # noqa: E402
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
    for topic, q, expect, _must in QUESTIONS:
        if expect != "answer":
            continue          # 답하면 안 되는 문항은 검수할 문서가 없다
        # ★ 본 측정과 **같은 경로**로 물어야 한다 (2026-08-18)
        #   처음엔 전부 안내 검색으로 뽑았는데, 공지 문항은 서버가
        #   제목 검색을 쓴다. 그러면 대표가 **실제 답이 아닌 것**을 검수하게 된다.
        #   재는 자가 실제 경로와 다르면 측정이 거짓말을 한다 — 리포트에 적힌 그대로다.
        manual = manual_answers.find(q)
        if manual is not None:
            rows.append([q, "총학 확인 답 (T4)", "", manual.answer[:80],
                         "", "", "", "총학이 직접 확인한 답입니다"])
            continue
        r = (ss.search_notices(conn, q, repo=repo) if topic == "공지"
             else ss.search(conn, q, repo=repo))
        top = getattr(r, "top", None) or (r.hits[0] if getattr(r, "hits", None) else None)
        if r.outcome is not ss.Outcome.FOUND or top is None:
            rows.append([q, "(답 못 함)", "", "", "", "", "", ""])
            continue
        if topic == "공지":
            # 공지는 제목·게시판만 있다. 본문을 안 읽는 게 설계다.
            doc = " · ".join(x for x in (getattr(top, "site_name", ""),
                                         getattr(top, "board_name", "")) if x)
            where = f"{doc} {top.title}"
            quote = top.title
            url = top.url
        else:
            doc = " · ".join(x for x in (top.site_name, top.page_title) if x)
            where = f"{top.site_name} {top.page_title} {top.quote_path or top.path}"
            quote = " ".join((top.quote_text or top.text or "").split())[:80]
            url = top.page_url
        prev = review.get(q) or {}
        ox = "O" if prev.get("ok") is True else ("X" if prev.get("ok") is False else "")
        sus = suspicious(q, where)
        # ★ 메모를 미리 채운다 — 대표가 O/X 찍을 때 헷갈리지 않게.
        #   공지 검색은 **제목만** 보는 자리라 문서 제목이 비어서 ❗ 가 붙는다.
        #   그건 틀렸다는 뜻이 아니다. 그 사실을 여기 적어 두지 않으면
        #   대표가 ❗ 를 보고 X 를 찍게 된다.
        memo = prev.get("note") or ""
        if topic == "공지":
            # ★ 공지는 **제목만** 보는 자리다. 본문을 안 읽으니
            #   '문서 제목이 질문과 맞나' 로 판단하시면 됩니다.
            memo = ((memo + " ") if memo else "") +                 "(공지 검색이라 제목·게시일·링크만 냅니다. 본문은 안 읽어요)"
        rows.append([q, doc, url, quote, sus,
                     ox, prev.get("expected") or "", memo])
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
