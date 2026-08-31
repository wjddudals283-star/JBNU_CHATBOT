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
from tools import answer_path  # noqa: E402
from tools.answerability_report import QUESTIONS, load_review  # noqa: E402

HEADER = ["질문", "봇이 낸 문서", "URL", "인용 앞부분", "의심",
          "검수 OX", "올바른 문서(X일 때만)", "메모"]


def _head() -> str:
    """지금 HEAD 의 짧은 해시. git 이 없으면 빈 값 — 파일은 그래도 만든다."""
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or "(unknown)"
    except Exception:  # noqa: BLE001
        return "(unknown)"


def _now() -> str:
    import datetime as dt
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


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
        # ★ 갈래를 **끌어온다** — 여기서 if 를 적으면 또 어긋난다 (2026-08-18)
        #   공지만 고쳤더니 이번엔 학사일정이 어긋났다. 같은 병 두 번째다.
        #   이제 tools/answer_path 가 server.route_of 를 그대로 부른다.
        obs = answer_path.observe(conn, q, db_path=args.db)
        r = obs.result
        if r is None:
            rows.append([q, f"(자가 못 읽는 갈래: {obs.route})", "", "",
                         "", "", "", f"why={obs.why}"])
            continue
        top = getattr(r, "top", None) or (r.hits[0] if getattr(r, "hits", None) else None)
        if r.outcome is not ss.Outcome.FOUND or top is None:
            rows.append([q, "(답 못 함)", "", "", "", "", "",
                         f"{obs.route} · {obs.why}"])
            continue
        if obs.kind == "notices":
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
            url = getattr(top, "page_url", "") or ""
        prev = review.get(q) or {}
        ox = "O" if prev.get("ok") is True else ("X" if prev.get("ok") is False else "")
        # ★ 안전 분기는 **이미 확인됐다** — 대표가 8/31 전화로 직접 걸었다.
        #   크롤이 아니라 사람이 확인한 값이라 다시 검수할 것이 없다.
        #   비워 두면 대표가 이미 한 일을 또 하게 된다.
        if obs.route == "safety":
            ox = "O"
            memo_extra = "대표가 8/31 전화로 확인 (크롤 아님) · verified_method: phone"
        else:
            memo_extra = ""
        sus = suspicious(q, where)
        # ★ 메모를 미리 채운다 — 대표가 O/X 찍을 때 헷갈리지 않게.
        #   공지 검색은 **제목만** 보는 자리라 문서 제목이 비어서 ❗ 가 붙는다.
        #   그건 틀렸다는 뜻이 아니다. 그 사실을 여기 적어 두지 않으면
        #   대표가 ❗ 를 보고 X 를 찍게 된다.
        memo = prev.get("note") or ""
        # ★ 어느 갈래로 갔는지 시트에 남긴다 — 대표가 "이건 검색이 아니네" 를 볼 수 있다.
        memo = (memo + " " if memo else "") + f"[{obs.route}]"
        if memo_extra:
            memo = f"{memo} {memo_extra}"
        if obs.kind == "notices":
            # ★ 공지는 **제목만** 보는 자리다. 본문을 안 읽으니
            #   '문서 제목이 질문과 맞나' 로 판단하시면 됩니다.
            memo = ((memo + " ") if memo else "") +                 "(공지 검색이라 제목·게시일·링크만 냅니다. 본문은 안 읽어요)"
        rows.append([q, doc, url, quote, sus,
                     ox, prev.get("expected") or "", memo])
    conn.close()

    out = pathlib.Path(args.out)
    # ★ BOM 을 붙인다 — 구글시트·엑셀이 UTF-8 을 알아보게. 안 그러면 한글이 깨진다.
    # ★ 맨 윗줄에 **무엇을 잰 것인지** 적는다 (2026-08-31)
    #   아침에 정한 규칙의 실행이다 — '대표가 본 화면이 최신 배포본이 아닐 수 있다'.
    #   오늘 코드가 여러 번 바뀌었는데 CSV 가 안 따라와서
    #   낡은 자료 위에 O 를 찍을 뻔했다. 하루 종일 잡은 그 병이다.
    #   해시와 시각이 없으면 이 파일이 무엇을 잰 것인지 나중에 알 수 없다.
    stamp = [f"# 커밋 {_head()} · 뽑은 시각 {_now()} · DB {args.db}",
             "# 이 줄 아래가 그 시점의 답이다. 코드가 바뀌면 다시 뽑고 O/X 를 옮긴다."]
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        for line in stamp:
            w.writerow([line])
        w.writerow(HEADER)
        w.writerows(rows)

    n_sus = sum(1 for r in rows if r[4])
    print(f"→ {out}  ({len(rows)}행)")
    print(f"   ❗ 먼저 볼 것 {n_sus}건")
    if not n_sus:
        # ★ 0건을 '다 맞다' 로 읽으면 안 된다 (2026-08-18)
        #   이 신호는 '질문 낱말이 제목·경로에 하나도 없음' 을 잡는다.
        #   그런데 동점 깨기가 **바로 그 종류를 먼저 막는다** —
        #   제목이 질문 낱말을 담은 문서를 앞에 두기 때문이다.
        #   그래서 0건은 '이 신호가 이제 못 뜬다' 는 뜻이지 안심이 아니다.
        #   맞는 문서인지는 여전히 사람이 봐야 안다.
        print("      ※ 0건은 '다 맞다' 가 아닙니다. 동점 깨기가 이 신호가 잡던")
        print("        종류를 먼저 막아서 이제 안 뜨는 겁니다 — 검수는 그대로 필요합니다.")
    for r in rows:
        if r[4]:
            print(f"      {r[0]:18} → {r[1][:40]}")
    print("\n시트에 넣고 '검수 OX' 칸에 O/X 만 찍으면 됩니다.")
    print("X 인 것만 '올바른 문서' 를 적어 주세요.")
    print(f"돌려받은 결과는 {ROOT / 'config' / 'answer_review.yaml'} 로 옮깁니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
