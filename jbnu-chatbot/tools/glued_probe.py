"""붙여 쓴 질문이 **맞는 문서**를 찾는지 잰다.

    python tools/glued_probe.py --db data/jbnu.db

★ 46문항으로는 이 길을 못 잰다 (2026-08-15)
  46문항은 전부 띄어쓴 형태라 **분해 코드가 켜지지도 않는다.**
  그런데 나는 '사전 있든 없든 46문항 똑같다' 로 확신 오답 0 을 확인했다고 했다.
  **안 켜지는 길을 재고 안전하다고 결론 낸 것이다.**
  그래서 분해 경로 전용 자를 따로 둔다.

★ 자는 '찾았는가' 가 아니라 '맞는 문서인가' 다
  '성적이의신청' 이 답을 하긴 했다 — 정보공개 이의신청(행정) 문서였다.
  학생이 물은 건 성적 이의신청(학사)이다.
  찾았다고 세면 이런 게 회복으로 잡힌다.

★ 기준선은 **띄어 쓴 질문의 답**이다
  우리가 정답을 정하는 게 아니라, 같은 질문을 띄어 썼을 때 나오는 문서를
  기준으로 삼는다. 분해의 목적이 '띄어 쓴 것처럼 만들어 주기' 이므로
  그 목적을 그대로 자로 쓴다.

    같은 문서    ✅ 분해가 제 일을 했다
    다른 문서    ⚠️ 답은 나오는데 엉뚱한 데서 맞췄다  ← '성적이의신청' 이 여기
    못 찾음      ❌
    기준선 없음   —  띄어 써도 못 찾는 문항이라 잴 수가 없다
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import section_search as S  # noqa: E402
from store import repo                 # noqa: E402
from tools.answerability_report import QUESTIONS  # noqa: E402


def _doc(r) -> tuple[str, str]:
    """(문서 URL, 사람이 읽는 이름). 문서가 같은지는 URL 로 본다."""
    t = getattr(r, "top", None)
    if t is None:
        return ("", "")
    name = " · ".join(x for x in (getattr(t, "site_name", ""),
                                  getattr(t, "page_title", "")) if x)
    return (getattr(t, "page_url", ""), name)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    args = ap.parse_args(argv)
    logging.disable(logging.CRITICAL)
    conn = repo.connect(pathlib.Path(args.db), readonly=True)

    print("=" * 92)
    print("붙여 쓴 질문이 맞는 문서를 찾는가 — 기준선은 띄어 쓴 질문의 답")
    print("=" * 92)

    rows = []
    for _t, q, _e, _m in QUESTIONS:
        tight = q.replace(" ", "")
        if tight == q:
            continue
        base = S.search(conn, q, repo=repo)
        got = S.search(conn, tight, repo=repo)
        base_url, base_name = _doc(base)
        got_url, got_name = _doc(got)
        split = getattr(got, "via_split", "")

        if base.outcome is not S.Outcome.FOUND or not base_url:
            verdict = "—기준선없음"
        elif got.outcome is not S.Outcome.FOUND or not got_url:
            verdict = "❌못찾음"
        elif got_url == base_url:
            verdict = "✅같은문서"
        else:
            verdict = "⚠️다른문서"
        rows.append((q, tight, verdict, split, base_name, got_name))

    tally: dict[str, int] = {}
    for _q, _t, v, _s, _b, _g in rows:
        tally[v] = tally.get(v, 0) + 1

    print(f"\n{'질문':18} {'판정':12} {'분해':22} 나온 문서")
    print("─" * 92)
    for q, tight, v, sp, base_name, got_name in rows:
        if v == "—기준선없음":
            continue
        print(f"{tight[:17]:18} {v:12} {(sp or '-')[:21]:22} {got_name[:38]}")
        if v == "⚠️다른문서":
            print(f"{'':18} {'':12} {'기준선(띄어씀)':22} {base_name[:38]}")

    print("\n" + "─" * 44)
    for k in ("✅같은문서", "⚠️다른문서", "❌못찾음", "—기준선없음"):
        if tally.get(k):
            print(f"  {k:12} {tally[k]:>3}건")
    gradable = sum(tally.get(k, 0) for k in ("✅같은문서", "⚠️다른문서", "❌못찾음"))
    ok = tally.get("✅같은문서", 0)
    bad = tally.get("⚠️다른문서", 0)
    print(f"\n★ 잴 수 있는 {gradable}건 중 맞는 문서 {ok}건 · **엉뚱한 문서 {bad}건**")
    if bad:
        print("  ⚠️ 는 학생 눈에 답으로 보인다. 찾았다고 세면 안 되는 자리다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
