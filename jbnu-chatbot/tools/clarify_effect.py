"""되묻기의 **순효과**를 잰다 — 회수 − 후퇴.

    python tools/clarify_effect.py --db data/jbnu.db --short

★ '2턴에 확신' 이 두 가지를 뭉친다
      되물을 필요가 있었고 되물어서 맞음    → 회수. 되묻기가 벌어들인 것
      되물을 필요가 없었는데 되물어서 맞음   → 후퇴. 학생을 한 번 더 두드리게 함
  지금 칸에서는 둘 다 성공으로 세어진다. 이게 없으면 되묻기를 붙일수록
  좋아 보이는데 실제로는 나빠질 수 있다.

★ 가르는 법은 간단하다 — 같은 문항을 되묻기 on/off 로 두 번 잰다
      off 확신     & on 2턴 확신   → 후퇴 (원래 바로 답할 수 있었다)
      off 문서까지  & on 2턴 확신   → 회수 (되묻기가 벌었다)

★ 2턴은 시늉이 아니라 실제로 돌린다
  버튼이 새 발화를 보내는 구조이므로, 라벨을 그대로 질의에 넣어 다시 검색한다.
  상태가 없으니 측정도 상태 없이 재현된다.

★ 배포 조건은 '틀림' 에만 건다
  '선택지에 답 없음' 은 짜증이지 오답이 아니다 — 학생이 잘못된 정보를 갖고 가지 않는다.
  다만 임계는 둔다. 선택지를 보여주면 학생은 '답이 이 중에 있다' 고 믿는다.
  없는데 자주 보여주면 짜증을 넘어 신뢰 문제가 된다. 30% 를 넘으면 경고한다.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import clarify  # noqa: E402
from skill import section_search as ss  # noqa: E402
from store import repo  # noqa: E402
from tools.answerability_report import (QUESTIONS, SURE, judge,  # noqa: E402
                                        shorten)

# 이 비율을 넘으면 경고 — 차단은 아니다
NO_ANSWER_WARN = 0.30


def fires(conn, q: str, r) -> list[str]:
    """이 질문에 되묻기가 발동하나. 발동하면 선택지를 돌려준다."""
    if r.top is None or not getattr(r, "page_level", False):
        return []
    tokens = r.query_tokens or []
    opts = clarify.options(conn, r.top.page_url, tokens)
    if not opts:
        return []
    if clarify.already_narrowed(q, opts, tokens) or \
       clarify.narrowed_by_qualifier(q, opts, tokens):
        return []       # 이미 정해진 질문 — 되물으면 두 번 일하게 한다
    return opts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--short", action="store_true")
    args = ap.parse_args(argv)

    conn = repo.connect(args.db, readonly=True)
    recovered, regressed, no_answer, unchanged = [], [], [], []
    fired = 0
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
            off, _ = judge(q, expect, m, r)

            opts = fires(conn, q, r)
            if not opts:
                unchanged.append((q, off))
                continue
            fired += 1

            # 2턴 — 버튼을 눌렀다고 치고 라벨을 그대로 발화로 보낸다
            best = None
            for label in opts:
                r2 = ss.search(conn, label, repo=repo)
                v2, _ = judge(label, expect, m, r2)
                if v2 == SURE:
                    best = label
                    break
            if best is None:
                no_answer.append((q, opts))
            elif off == SURE:
                regressed.append((q, best))     # 원래 바로 답할 수 있었다
            else:
                recovered.append((q, off, best))

        print("=" * 72)
        print("되묻기 순효과 = 회수 − 후퇴")
        print("=" * 72)
        print(f"발동 {fired}건")
        print(f"  회수 {len(recovered):3}  되물어서 벌었다")
        for q, off, lab in recovered:
            print(f"        {q:16} {off} → 2턴 확신 ('{lab}')")
        print(f"  후퇴 {len(regressed):3}  원래 바로 답할 수 있었다")
        for q, lab in regressed:
            print(f"        {q:16} 확신 → 되물음 ('{lab}')")
        print(f"  답없음 {len(no_answer):3}  되물었는데 선택지에 답이 없다")
        for q, opts in no_answer:
            print(f"        {q:16} {' · '.join(opts[:4])}")
        net = len(recovered) - len(regressed)
        print()
        print(f"★ 순효과 {net:+d}건   (회수 {len(recovered)} − 후퇴 {len(regressed)})")
        if fired:
            ratio = len(no_answer) / fired
            mark = "⚠ " if ratio > NO_ANSWER_WARN else "  "
            print(f"{mark}선택지에 답 없음 {ratio:.0%} "
                  f"(임계 {NO_ANSWER_WARN:.0%})")
            if ratio > NO_ANSWER_WARN:
                print("   선택지를 보여주면 학생은 '답이 이 중에 있다' 고 믿는다. "
                      "없는데 자주 보여주면 신뢰 문제가 된다.")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
