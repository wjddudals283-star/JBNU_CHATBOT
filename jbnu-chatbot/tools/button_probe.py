"""우리가 붙인 버튼을 **전부 눌러 본다**.

    python tools/button_probe.py --db data/jbnu.db

★ 왜 만들었나 (2026-08-13)
  '처음으로' 는 우리가 **모든 답변에 붙이는 버튼**인데 누르면
      "'처음'은 학과마다 달라요. '박물관 처음'처럼 물어봐 주세요"
  가 나왔다. 가장 많이 눌릴 버튼이 가장 엉뚱한 답을 내고 있었다.
  봇테스트로 답변을 대여섯 번 보고도 아무도 그 버튼을 안 눌렀다.

  ★ 우리는 만든 것은 테스트하는데, **항상 거기 있던 것은 테스트 안 한다.**

★ 버튼 발화는 '짜증' 으로 안 끝난다
  자유 질문이 빗나가면 학생은 다시 묻는다. 그런데 버튼은 **우리가 준 선택지**다.
  누른 대로 안 나오면 그건 애매함이 아니라 고장이다.
  ('형식 안내는 최악이 짜증' 이라는 판단은 버튼에는 해당되지 않는다)

★ 어떻게 재나 — 실제로 누른다
  답변을 렌더해서 quickReplies 의 messageText 를 모으고, 그걸 그대로
  서버에 다시 넣는다. 학생이 하는 일과 같은 순서다.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import logging  # noqa: E402

from skill import server  # noqa: E402
from tools.answerability_report import QUESTIONS  # noqa: E402

# 답변을 만들어 낼 씨앗. 46문항 + 식단·웰컴처럼 버튼이 많이 붙는 자리.
EXTRA_SEEDS = ["오늘 학식", "진수원", "생활관 식당", "학사일정", "", "처음으로"]

# ★ 세 칸으로 나눈다. 뭉쳐 세면 고칠 수 있는지 알 수가 없다.
#   고장    눌렀는데 못 찾는다 — 우리가 준 선택지인데 답이 없다
#   되물음   되묻기·형식 안내로 간다 — **고장이 아니다**. 다만 메뉴 버튼으로는
#            '누르면 답이 나온다' 는 약속을 못 지킨다. 사람이 판단할 자리다.
#   정상    답이 나온다
BAD_MARKS = ("못 찾았어요", "찾지 못했어요", "잘 모르겠어요",
             "자료가 준비되지 않았어요")
ASK_MARKS = ("학과마다 달라요", "여러 갈래로 나뉘어 있어요",
             "안내가 여러 곳에 있어요")


def press(db: pathlib.Path, text: str) -> tuple[str, list[str]]:
    """그 말을 그대로 보낸다 — 폴백 경로(블록 없음)로. 학생이 버튼을 누른 것과 같다."""
    out = server.handle(db, None,
                        {"userRequest": {"utterance": text},
                         "action": {"params": {}}})
    tpl = out.get("template", {})
    first = (tpl.get("outputs") or [{}])[0]
    body = (first.get("simpleText", {}).get("text")
            or first.get("listCard", {}).get("header", {}).get("title")
            or first.get("textCard", {}).get("title") or "?")
    qr = [q.get("messageText") or q.get("label") or ""
          for q in tpl.get("quickReplies", [])]
    return body.replace("\n", " "), qr


def collect(db: pathlib.Path, seeds: list[str]) -> dict[str, set[str]]:
    """씨앗 답변들에서 버튼을 모은다. 버튼 → 그 버튼이 붙어 있던 질문들."""
    found: dict[str, set[str]] = {}
    for s in seeds:
        try:
            _, qr = press(db, s)
        except Exception:  # noqa: BLE001
            continue
        for b in qr:
            if b:
                found.setdefault(b, set()).add(s or "(빈 발화)")
    return found


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    args = ap.parse_args(argv)
    logging.disable(logging.CRITICAL)
    db = pathlib.Path(args.db)

    seeds = [q for _t, q, _e, _m in QUESTIONS] + EXTRA_SEEDS
    buttons = collect(db, seeds)

    print("=" * 74)
    print(f"우리가 붙인 버튼 {len(buttons)}종 — 전부 눌러 본다")
    print("=" * 74)
    bad, ask, ok = [], [], []
    for b in sorted(buttons):
        body, _ = press(db, b)
        if any(m in body for m in BAD_MARKS):
            bad.append((b, body, buttons[b]))
        elif any(m in body for m in ASK_MARKS):
            ask.append((b, body, buttons[b]))
        else:
            ok.append((b, body, buttons[b]))

    if bad:
        print(f"\n★ 눌렀는데 답이 안 나오는 버튼 {len(bad)}개")
        for b, body, where in bad:
            print(f"  ❌ [{b}]")
            print(f"       → {body[:78]}")
            print(f"       붙은 곳: {', '.join(sorted(where))[:60]}")
    else:
        print("\n★ 눌렀을 때 답이 안 나오는 버튼 없음")

    if ask:
        print(f"\n되물음으로 가는 버튼 {len(ask)}개 — **고장은 아니다**")
        print("  메뉴 버튼이면 '누르면 답이 나온다' 는 약속을 못 지킨다 — 사람이 판단할 자리다.")
        for b, body, where in ask:
            print(f"  ↩ [{b}]")
            print(f"       → {body[:74]}")
            print(f"       붙은 곳: {', '.join(sorted(where))[:56]}")

    print(f"\n정상 {len(ok)}개")
    for b, body, _ in ok:
        print(f"  ✅ [{b:16}] {body[:56]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
