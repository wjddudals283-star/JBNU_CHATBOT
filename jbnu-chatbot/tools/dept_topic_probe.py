"""'주제 + 학과' 로 물으면 그 학과의 그 문서를 찾는가.

    python tools/dept_topic_probe.py --db data/jbnu.db

★ 왜 이걸 재나 (2026-08-18)
  되묻기 3턴에서 13/13 주제가 증발한다. 고치는 길이 둘이었다.
      (a) 상태를 유지한다      — 앞 턴의 주제를 기억한다
      (b) 안내 문구로 알려준다  — "'질병휴학 경제학부' 처럼 붙여서 물어봐 주세요"
  (b) 가 통하려면 **붙여 쓴 형태가 실제로 답을 내야** 한다. 그걸 먼저 잰다.

★ 코퍼스를 정답으로 깐다
  '그 학과에 그 주제 문서가 있을 때만' 센다. 없는 것을 못 찾았다고 세면
  검색 탓이 아닌 것을 검색 탓으로 돌리게 된다.

★ 낱말로 세지 않는다
  처음엔 '답에 주제어가 있나' 로 쟀다. 그랬더니
  "'질병휴학' 관련 안내는 **못 찾았어요**" 가 ✅ 로 잡혔다 —
  못 찾았다는 문장에 주제어가 들어 있었을 뿐이다.
  이제 **문서 URL 이 정답과 같은가**로 센다.
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

# 학생이 학과를 붙여 묻는 주제. 46문항에서 되묻기가 나던 것들이다.
TOPICS = ["휴학", "복학", "졸업요건", "조기졸업", "전과",
          "복수전공", "교환학생", "증명서"]


def dept_names() -> list[str]:
    return sorted((n for n in S.site_names().values()
                   if n.endswith(("학과", "학부", "대학", "대학원"))),
                  key=len, reverse=True)


def scope_only(question: str, hit, depts: list[str]) -> bool:
    """맞은 낱말이 **질문에 든 학과 이름의 조각뿐**이다.

    = 범위는 맞췄고 주제는 하나도 못 맞췄다.
      '휴학 항공우주공학과' 가 matched=['항공우주공학'] 로 학습성과를 냈다.
      학과 이름은 **어디를 볼지**지 **무엇을 볼지**가 아니다.

    ★ 처음엔 '맞은 낱말이 아무 사이트 이름의 부분문자열이면' 으로 쟀다.
      '수강신청 공지' 가 걸렸다 — 두 낱말 다 맞았는데도.
      질문에 실제로 든 학과 이름으로 좁혀야 한다.
    """
    d = next((x for x in depts if x in question), None)
    if not d:
        return False
    m = list(getattr(hit, "matched", None) or [])
    return bool(m) and all(t in d for t in m)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    args = ap.parse_args(argv)
    logging.disable(logging.CRITICAL)
    conn = repo.connect(pathlib.Path(args.db), readonly=True)

    depts = dept_names()
    hosts = {n: h for h, n in S.site_names().items()}
    targets = [n for n in hosts if n.endswith(("학과", "학부"))]

    print("=" * 84)
    print("'주제 + 학과' 로 물으면 그 학과의 그 문서를 찾는가")
    print("=" * 84)

    have = hit = notfound = wrongdoc = 0
    caught = wrongly = 0
    bad = []
    for t in TOPICS:
        for d in targets:
            row = conn.execute(
                "SELECT page_url FROM page_registry WHERE host=? "
                "AND title LIKE ? AND leaf_count > 0 LIMIT 1",
                (hosts[d], f"%{t}%")).fetchone()
            if not row:
                continue                     # 그 학과에 그 문서가 없다 — 셀 자리가 아니다
            have += 1
            q = f"{t} {d}"
            r = S.search(conn, q, repo=repo)
            top = getattr(r, "top", None)
            if r.outcome is not S.Outcome.FOUND or top is None:
                notfound += 1
                continue
            if top.page_url == row[0]:
                hit += 1
                if scope_only(q, top, depts):
                    wrongly += 1
                continue
            wrongdoc += 1
            if scope_only(q, top, depts):
                caught += 1
            if len(bad) < 10:
                bad.append((q, f"{top.site_name}·{top.page_title}",
                            "범위만" if scope_only(q, top, depts) else ""))

    print(f"\n그 학과에 그 주제 문서가 **있는** 쌍 {have}건")
    print(f"   ✅ 그 문서를 찾는다        {hit:>3}건 "
          f"({hit * 100 // max(have, 1)}%)")
    print(f"   ⚠️ 확신 있게 딴 문서를 낸다  {wrongdoc:>3}건   ← 제일 나쁜 자리")
    print(f"   △ 못 찾거나 되묻는다       {notfound:>3}건")

    print(f"\n★ '범위만 맞음' 규칙이 딴 문서 {wrongdoc}건 중 **{caught}건**을 잡는다")
    print(f"  맞는 답인데 잘못 잡는 것 **{wrongly}건**")
    print("\n딴 문서를 낸 예")
    for q, got, mark in bad:
        print(f"   {q:24} → {got[:44]}  {mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
