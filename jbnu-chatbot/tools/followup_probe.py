"""되묻고 나서 **학생의 대답을 받는지** 전수로 잰다 (2턴).

    python tools/followup_probe.py --db data/jbnu.db        로컬 코퍼스
    python tools/followup_probe.py --server                 배포본 (SKILL_TOKEN 필요)

★ 왜 live_probe 를 늘리지 않았나
  live_probe 의 계약은 '46문항 · 한 턴 · 표 하나' 다. 2턴은 입력이 다르다 —
  되묻기가 난 문항만 고르고, 화면에서 선택지를 뽑아 되돌려야 한다.
  한 도구에 섞으면 무엇을 재는 도구인지 흐려진다.
  ★ 다만 payload·flatten 은 **가져다 쓴다.** 두 벌이면 갈라진다.

★ 무엇을 2턴으로 보내나 — 화면이 준 것만
  우리가 상상한 후속 발화를 넣으면 학생이 안 하는 말을 재게 된다.
  화면에 실제로 있는 것만 쓴다.
      quickReplies      되묻기 선택지
      listCard 항목      후보 목록
      문안 속 예시        "'경제학부 졸업요건'처럼 …" 의 따옴표 안

★ 판정
    이어짐    2턴 답이 1턴과 다르고, 되묻기가 아니고, 못 찾음도 아니다
    또_되물음  같은 되묻기가 또 나왔다 — 학생은 빠져나갈 수 없다
    끊김      새 질문으로 처리됐다 (엉뚱한 답 · 못 찾음)
    선택지없음  되물어 놓고 되돌릴 것을 안 줬다
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import re
import sys
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.answerability_report import QUESTIONS       # noqa: E402
from skill.section_search import site_names            # noqa: E402

# ★ 학과 이름을 손으로 적지 않는다 — 우리가 아는 사이트 이름에서 끌어온다.
#   손으로 고르면 우리가 고치고 싶은 쪽으로 고르게 된다.
DEPTS = [n for n in site_names().values()
         if n.endswith("학과") or n.endswith("학부")]
from tools.live_probe import (BASE, HEADER, TOKEN_ENV,  # noqa: E402
                              flatten, payload)

# 되묻기·형식 안내를 알아보는 표시. live_probe 와 같은 말을 쓴다.
ASK_MARKS = ("골라 주시면", "여러 갈래로 나뉘어 있어요", "안내가 여러 곳에 있어요",
             "학과마다 달라요", "어느 식당을 볼까요", "어느 쪽인지",
             "붙여서 물어봐 주세요")
LOST_MARKS = ("못 찾았어요", "찾지 못했어요", "잘 모르겠어요",
              "준비되지 않았어요", "확인하지 못했어요")
SKIP = {"처음으로", "시작하기", "다른 식당", "총학 공지", "학사일정 전체"}

# 문안 속 예시: "'경제학부 졸업요건'처럼 학과를 붙여서" 의 따옴표 안
_EXAMPLE = re.compile(r"'([^']{2,40})'\s*처럼")


def is_ask(body: str) -> bool:
    return any(m in body for m in ASK_MARKS)


def is_lost(body: str) -> bool:
    return any(m in body for m in LOST_MARKS)


def followups(body: str, choices: list[str], question: str) -> list[tuple[str, str]]:
    """화면이 준 후속 발화 후보 — (보낼 말, 종류).

    ★ **사람이 실제로 하는 대답**을 같이 재야 한다
      처음엔 문안의 예시('경제학부 졸업요건')를 통째로 보내서 쟀는데,
      그건 쉬운 쪽이다. "어느 학과인지 알려주시면" 이라고 물으면
      사람은 **'경제학부'** 라고만 답한다. 대표가 실제로 그렇게 쳤다.
      쉬운 쪽만 재면 '거의 다 이어진다' 는 틀린 그림이 나온다.
      (자가 틀리면 진단이 틀린다 — 이 도구를 만들다 또 걸렸다)
    """
    out: list[tuple[str, str]] = [(c, "선택지") for c in choices
                                  if c and c not in SKIP]
    qtoks = [t for t in re.split(r"\s+", question or "") if len(t) >= 2]
    for m in _EXAMPLE.finditer(body):
        full = m.group(1)
        out.append((full, "예시전체"))
        # 질문에 이미 있던 말을 뺀 나머지 = 사람이 실제로 답하는 부분
        bare = full
        for t in qtoks:
            bare = bare.replace(t, " ")
        bare = " ".join(bare.split())
        if bare and bare != full:
            out.append((bare, "예시에서_학생말"))
    seen, uniq = set(), []
    for c, kind in out:
        if c not in seen:
            seen.add(c)
            uniq.append((c, kind))
    return uniq


# ── 두 가지 입구 — 로컬 DB / 배포 서버 ──────────────────────────

def local_asker(db: pathlib.Path):
    from skill import server
    logging.disable(logging.CRITICAL)

    def ask(utterance: str) -> dict:
        return server.handle(db, None, payload(utterance))
    return ask


def server_asker(token: str):
    def ask(utterance: str) -> dict:
        req = urllib.request.Request(
            BASE + "/skill", method="POST",
            data=json.dumps(payload(utterance), ensure_ascii=False).encode(),
            headers={"Content-Type": "application/json", HEADER: token})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    return ask


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--server", action="store_true", help="배포본에 물어본다")
    args = ap.parse_args(argv)

    if args.server:
        token = (os.environ.get(TOKEN_ENV) or "").strip()
        if not token:
            print(f"✗ {TOKEN_ENV} 환경변수가 없습니다.")
            return 2
        ask = server_asker(token)
        where = BASE
    else:
        ask = local_asker(pathlib.Path(args.db))
        where = args.db

    print("=" * 74)
    print(f"되묻기 → 학생의 대답 (2턴)  —  {where}")
    print("=" * 74)

    rows, rows3 = [], []
    for _t, q, _e, _m in QUESTIONS:
        try:
            body1, ch1, _s = flatten(ask(q))
        except Exception as e:                       # noqa: BLE001
            rows.append((q, "오류", "", "", str(e)[:40]))
            continue
        if not is_ask(body1):
            continue                                  # 되묻기가 아니면 여기 일이 아니다
        cands = followups(body1, ch1, q)
        if not cands:
            rows.append((q, "선택지없음", "", "", body1[:40]))
            continue
        # ★ 사람이 실제로 하는 대답을 우선해서 잰다
        cands.sort(key=lambda c: 0 if c[1] == "예시에서_학생말" else 1)
        pick, kind = cands[0]
        try:
            body2, _ch2, _s2 = flatten(ask(pick))
        except Exception as e:                       # noqa: BLE001
            rows.append((q, "오류", pick, kind, str(e)[:40]))
            continue
        if is_ask(body2):
            verdict = "또_되물음"
        elif is_lost(body2):
            verdict = "끊김"
        else:
            # ★ 이어짐은 **두 가지가 다 있어야** 한다 (세 번째로 고친 판정)
            #     ① 고른 것      '경제학부'
            #     ② 1턴의 주제   '졸업요건'
            #   ①만 보면 '경제학부' 를 골랐을 때 교육목표·학과앨범이 와도
            #   이어짐이 된다 — 실제로 그렇게 셌다.
            #   되묻기는 **주제를 그 학과로 좁히는 것**이므로 주제가 빠지면
            #   좁혀진 게 아니라 화제가 바뀐 것이다.
            picked = [t for t in re.split(r"\s+", pick) if len(t) >= 2]
            got_pick = bool(picked) and all(t in body2 for t in picked)
            if kind == "선택지":
                # 버튼을 누른 계약은 '그 제목의 것을 준다' 까지다.
                # 선택지 자체가 그 주제 안에서 뽑힌 것이므로 주제어를 또 안 본다.
                verdict = "이어짐" if got_pick else "끊김"
            else:
                # 형식 안내에 학과명으로 답한 경우는 **좁히기**다 —
                # 학과와 주제가 둘 다 있어야 좁혀진 것이다.
                topic = [t for t in re.split(r"\s+", q) if len(t) >= 2
                         and t not in pick]
                got_topic = (not topic) or any(t in body2 for t in topic)
                verdict = "이어짐" if (got_pick and got_topic) else "끊김"
        rows.append((q, verdict, pick, kind, " ".join(body2.split())[:44]))

        # ★ 3턴 — 대표가 실사용에서 찾은 자리 (2026-08-18)
        #   "질병휴학에서 **표에 뜬 과 말고 내 과**를 쳤는데
        #    질병휴학 내용이 아닌 졸업요건 내용이 뜸"
        #   우리는 '버튼은 살고 형식 안내만 죽는다' 고 결론냈다.
        #   그건 **2턴까지만 잰 것**이다. 버튼을 누른 뒤 학과를 치는
        #   3턴에서 주제가 증발한다.
        #
        #   ★ **안 보여준 학과**를 친다. 보여준 것만 치면 실제 사고를 못 잰다.
        #
        #   ★ 처음엔 '2턴이 또 되묻기일 때만' 3턴을 갔다 — 틀렸다.
        #     대표는 **답을 받고 나서** 그 답의 표에 자기 과가 없어서 쳤다.
        #     조건을 걸면 대표가 실제로 한 일을 못 잰다. 조건을 뺀다.
        offered = " ".join(_ch2 or [])
        dept = next((d for d in DEPTS if d not in offered and d not in body2), None)
        if dept is None:
            continue
        try:
            body3, _ch3, _s3 = flatten(ask(dept))
        except Exception as e:                       # noqa: BLE001
            rows3.append((q, "오류", dept, str(e)[:40]))
            continue
        # ★ 판정을 **낱말이 아니라 구조**로 한다 (같은 날 두 번째로 고침)
        #   처음엔 '1턴 질문의 낱말이 3턴 답에 있나' 로 쟀다.
        #   그랬더니 '수강신청 학점 상한' 이 이어짐으로 나왔다 —
        #   교과과정 표의 '학점-강의-실습' 에 '학점' 이 있었을 뿐이다.
        #   13건이 **전부 같은 답**인데 하나만 이어짐으로 셌다.
        #   오늘 정한 규칙에 그 규칙을 만든 자리에서 또 걸렸다:
        #   「그 낱말이 들었나」가 아니라 「학생이 무엇으로 읽나」다.
        #
        #   맥락 없이 그 학과만 쳤을 때의 답과 **똑같으면**,
        #   앞의 두 턴이 아무것도 나르지 않은 것이다. 낱말을 안 본다.
        cold, _cc, _cs = flatten(ask(dept))
        carried = " ".join(body3.split()) != " ".join(cold.split())
        got_topic = carried
        got_dept = dept in body3
        if is_ask(body3):
            v3 = "또_되물음"
        elif is_lost(body3):
            v3 = "끊김"
        elif got_dept and got_topic:
            v3 = "이어짐"
        elif got_dept:
            v3 = "주제증발"          # ← 대표가 본 것: 학과는 맞는데 딴 내용
        else:
            v3 = "끊김"
        rows3.append((q, v3, dept, " ".join(body3.split())[:44]))

    if not rows:
        print("\n되묻기가 난 문항이 없다.")
        return 0

    def _print3():
        if not rows3:
            print("\n3턴까지 간 문항이 없다.")
            return
        t3: dict[str, int] = {}
        for _q, v, _d, _b in rows3:
            t3[v] = t3.get(v, 0) + 1
        print("\n" + "=" * 108)
        print("3턴 — 버튼을 누른 뒤 **안 보여준 학과**를 쳤을 때")
        print("=" * 108)
        print(f"\n3턴까지 간 문항 {len(rows3)}건")
        for k, n in sorted(t3.items(), key=lambda x: -x[1]):
            mark = "  ★ 대표가 본 것" if k == "주제증발" else ""
            print(f"   {k:10} {n:>3}건{mark}")
        print(f"\n{'질문':20} {'판정':10} {'3턴에 친 학과':16} 3턴 답")
        print("─" * 108)
        for q, v, dept, body in rows3:
            print(f"{q[:19]:20} {v:10} {dept[:15]:16} {body}")

    tally: dict[str, int] = {}
    for _q, v, _p, _k, _b in rows:
        tally[v] = tally.get(v, 0) + 1

    print(f"\n되묻기가 난 문항 {len(rows)}건\n")
    print(f"{'질문':20} {'판정':10} {'2턴에 보낸 말':18} {'종류':14} 2턴 답")
    print("─" * 108)
    for q, v, pick, kind, body in rows:
        print(f"{q[:19]:20} {v:10} {pick[:17]:18} {kind:14} {body}")
    _print3()

    print("\n" + "─" * 40)
    for k in ("이어짐", "또_되물음", "끊김", "선택지없음", "오류"):
        if tally.get(k):
            print(f"  {k:10} {tally[k]:>3}건")
    broken = sum(tally.get(k, 0) for k in ("또_되물음", "끊김", "선택지없음"))
    print(f"\n★ 되묻고 나서 이어지지 않는 것 {broken}/{len(rows)}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
