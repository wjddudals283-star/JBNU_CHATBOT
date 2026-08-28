"""학생이 **실제로 친 말** 101종으로 잰다 — 우리가 지어낸 변형이 아니다.

    python tools/variant_probe.py --db data/jbnu.db

★ 자료의 출처가 이 도구의 전부다 (2026-08-28)
  카카오 오픈빌더 `학습 → 학습대기` 에 7/29~8/27 폴백으로 간 발화가
  **원문 그대로** 쌓여 있었다. 101종 · 230건.
  같은 기간 블록 호출 303건 중 폴백 191건(63.04%).

  우리가 만든 변형('휴학하려면' 같은)을 쓰지 않는다.
  상상한 변형으로 재면 학생이 안 하는 말을 재게 된다 —
  46문항이 전부 띄어쓴 형태라 분해 코드가 안 켜지던 것과 같은 함정이다.

★ 판정 자는 새로 만들지 않는다
  glued_probe 의 것을 그대로 쓴다 — **원래 질문의 답과 같은 문서인가.**
  46문항에 대응하는 질문이 있으면 그 답을 기준선으로 삼고,
  없으면 '답이 나오는가' 만 본다. 자를 두 벌 만들면 갈라진다.

★ 네 갈래로 나눈다. 각각 **다른 일**이다
    ③ 우리 문구      우리가 화면에 낸 말을 학생이 그대로 쳤는데 폴백으로 왔다
                    ← 제일 아프다. 우리가 만든 말이 우리 코드에 안 걸린다.
    ① 46문항        우리가 이미 아는 질문인데 블록에 등록이 안 됐다 (카카오 쪽 일)
    ② 변형          같은 것을 다르게 말했다
    ④ 새 질문        46문항에 없던 것 — 자가 못 재던 자리
  ③ 은 우리 코드 문제고 ① 은 오픈빌더 설정 문제다. 뭉치면 고칠 데를 못 찾는다.
"""

from __future__ import annotations

import argparse
import collections
import logging
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import section_search as S  # noqa: E402
from store import repo                 # noqa: E402
from tools.answerability_report import QUESTIONS  # noqa: E402
from tools.button_probe import collect  # noqa: E402
from tools.live_probe import flatten, payload  # noqa: E402
from tools import answer_path  # noqa: E402

# ★ 관측 자료를 저장소 안에 둔다 — 자와 자료가 떨어져 있으면
#   다음 사람이 무엇으로 쟀는지 모른다.
DEFAULT_FILE = pathlib.Path(__file__).with_name(
    "fallback_utterances_0827.txt")
LOST = ("못 찾았어요", "찾지 못했어요", "잘 모르겠어요", "준비되지 않았어요",
        "확인하지 못했어요", "관련 안내는 못 찾았어요")
ASK = ("골라 주시면", "여러 갈래로 나뉘어", "안내가 여러 곳에 있어요",
       "어느 쪽인지", "어느 쪽을 찾으시는지", "어느 식당을 볼까요")


def norm(s: str) -> str:
    """공백·구두점을 지운 비교용 형태. 학생은 띄어쓰기를 아무렇게나 한다."""
    return re.sub(r"[^0-9A-Za-z가-힣]+", "", s or "")


def load_utterances(path: pathlib.Path) -> list[tuple[str, int]]:
    out = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        n = int(parts[1]) if len(parts) > 1 and parts[1].strip().isdigit() else 1
        out.append((parts[0].strip(), n))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    args = ap.parse_args(argv)
    logging.disable(logging.CRITICAL)
    db = pathlib.Path(args.db)
    conn = repo.connect(db, readonly=True)

    utts = load_utterances(pathlib.Path(args.file))
    print("=" * 96)
    print(f"학생이 실제로 친 말 {len(utts)}종 · "
          f"{sum(n for _, n in utts)}건 — 폴백으로 온 것 전부")
    print("=" * 96)

    # ── 기준선: 46문항의 답 (glued_probe 와 같은 자) ────────────────
    # ★ 기준선도 **실제 경로**로 낸다. S.search 로 냈다가 '시험언제' 가
    #   틀린 것으로 잡혔다 — 그 질문은 학사일정으로 가는데 검색으로 쟀다.
    #   오늘 그 병을 잡으려고 만든 answer_path 를 정작 안 쓰고 있었다.
    base: dict[str, tuple[str, str]] = {}
    defer_q: set[str] = set()
    for _t, q, e, _m in QUESTIONS:
        if e != "answer":
            defer_q.add(norm(q))      # 답하면 안 되는 문항은 '못함' 이 정답이다
            continue
        obs = answer_path.observe(conn, q, db_path=db)
        top = getattr(obs.result, "top", None) if obs.result else None
        if obs.result and obs.result.outcome is S.Outcome.FOUND and top:
            base[norm(q)] = (getattr(top, "page_url", "") or getattr(top, "url", ""),
                             f"{top.site_name} · {top.page_title}")

    # ── ③ 우리가 화면에 낸 말 (button_probe 가 모은다) ──────────────
    seeds = [q for _t, q, _e, _m in QUESTIONS]
    ours = {norm(b) for b in collect(db, seeds)}
    print(f"\n우리가 화면에 내보내는 말 {len(ours)}종을 모았다 (button_probe)")

    qnorms = {norm(q) for _t, q, _e, _m in QUESTIONS}
    rows = []
    for u, n in utts:
        nu = norm(u)
        if nu in qnorms:
            kind = "① 46문항"
        elif nu in ours:
            kind = "③ 우리문구"
        elif any(nu and (nu in qn or qn in nu) for qn in qnorms):
            kind = "② 변형"
        else:
            kind = "④ 새질문"

        body, _ch, _s = flatten(__import__(
            "skill.server", fromlist=["server"]).handle(db, None, payload(u)))
        body = " ".join(body.split())
        asked = any(m in body for m in ASK)

        # ★ 판정도 **실제 경로**로. answer_path 가 route_of 를 그대로 부른다.
        obs = answer_path.observe(conn, u, db_path=db)
        top = getattr(obs.result, "top", None) if obs.result else None
        found = bool(obs.result and obs.result.outcome is S.Outcome.FOUND and top)
        got = (getattr(top, "page_url", "") or getattr(top, "url", "")) if top else ""

        # ★ 웰컴·인사·안전은 '찾았나' 로 재는 자리가 아니다 (자를 세 번째로 고침)
        #   '처음으로'(27건)가 ❌ 로 잡혔다 — 웰컴 화면은 정상 동작이다.
        #   answer_path 가 웰컴·인사를 NOT_FOUND 로 두는 건 리포트에서는 맞다
        #   (사실을 주장하지 않았으므로). 여기서는 '학생이 원한 화면이 왔나' 다.
        #   같은 자라도 무엇을 묻느냐에 따라 읽는 법이 다르다.
        if obs.route in ("welcome", "smalltalk", "safety"):
            rows.append((u, n, kind, "✅제자리", body[:44]))
            continue

        if nu in defer_q:
            # 답하면 안 되는 질문 — 안 답한 게 맞다
            verdict = "✅안답함(맞음)" if not found else "⚠️답하면안됨"
            rows.append((u, n, kind, verdict, body[:44]))
            continue

        want = next((base[qn] for qn in base
                     if nu and (nu == qn or nu in qn or qn in nu)), None)
        if want is None:
            verdict = ("✅답함" if found else
                       ("🔁되물음" if asked else "❌못함"))
        elif not found:
            verdict = "🔁되물음" if asked else "❌못함"
        elif got == want[0]:
            verdict = "✅같은문서"
        else:
            verdict = "⚠️다른문서"
        rows.append((u, n, kind, verdict, body[:44]))

    # ── 집계 ────────────────────────────────────────────────────────
    print()
    by_kind: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    weight: dict[str, collections.Counter] = collections.defaultdict(
        collections.Counter)
    for _u, n, kind, v, _b in rows:
        by_kind[kind][v] += 1
        weight[kind][v] += n
    order = ["③ 우리문구", "① 46문항", "② 변형", "④ 새질문"]
    print(f"{'갈래':12} {'종':>4}  판정별 (괄호는 실제 건수)")
    print("─" * 96)
    for k in order:
        c = by_kind.get(k)
        if not c:
            continue
        tot = sum(c.values())
        bits = " · ".join(f"{v} {c[v]}({weight[k][v]})"
                          for v in sorted(c, key=lambda x: -c[x]))
        print(f"{k:12} {tot:>4}종  {bits}")

    good = sum(sum(v for k2, v in c.items() if k2.startswith("✅"))
               for c in by_kind.values())
    print(f"\n★ {len(rows)}종 중 제대로 답하는 것 **{good}종** "
          f"({good * 100 // max(len(rows), 1)}%)")

    for k in order:
        bad = [(u, n, v, b) for u, n, kk, v, b in rows
               if kk == k and not v.startswith("✅")]
        if not bad:
            continue
        print(f"\n── {k} — 제대로 못 답하는 {len(bad)}종 " + "─" * 40)
        for u, n, v, b in sorted(bad, key=lambda x: -x[1])[:14]:
            print(f"   {n:>3}건 {v:9} {u[:26]:28} {b[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
