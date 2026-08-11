"""되묻기를 만들기 전에, 되물을 거리가 실제로 있는지부터 센다.

    python tools/clarify_probe.py --db data/jbnu.db

★ 왜 이 도구가 먼저인가
  '형제 2~5개면 되묻는다' 는 그럴듯하지만 숫자를 안 보고 정한 값이다.
  표본 두세 개로 종류를 세는 건 관측이 아니라 추측이다 — 전수로 센다.

★ 애매함의 두 갈래 (대표 제안)
    우리가 못 찾아서 애매    → 되물어도 소용없다. 페이지로
    질문이 덜 정해져서 애매  → 되물으면 풀린다
  판별을 관측으로 한다: 형제 섹션이 여럿인데 질문이 아무것도 특정 못 하면 후자.

★ 같이 재는 것 — '안전하지만 쓸모없음'
  지금 46문항은 ✅/△/❌ 세 칸인데 △ 가 두 가지를 뭉쳐 세고 있다.
      못 찾음        : 자료가 없거나 검색이 실패
      찾았는데 엉뚱함 : 답은 나갔고 불확실성도 밝혔는데 학생에게 쓸모가 없다
                      (예: '휴학 어떻게 해' → '임신·출산·육아 휴학 > 휴학시기')
  뭉쳐 세면 고칠 수 있는지 없는지 알 수가 없다 — empty 갈래를 쪼갤 때와 같다.
  후자가 바로 되묻기가 노리는 칸이다.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import section_search  # noqa: E402
from store import repo  # noqa: E402
from tools.answerability_report import QUESTIONS  # noqa: E402

# 선택지로 쓸 수 있는 형제인가 — 너무 짧거나 긴 것은 버튼에 안 들어간다
MIN_LABEL = 2
MAX_LABEL = 20


def leaf_label(path: str) -> str:
    """'휴학 종류 > 임신·출산·육아 휴학' → '임신·출산·육아 휴학'"""
    return (path or "").split(">")[-1].strip()


def siblings(conn, section_key: str) -> list[dict]:
    """같은 부모를 둔 형제들. **이게 되물을 선택지다 — 사람이 안 만든다.**

    학교가 항목을 바꾸면 따라간다. 유지보수가 없다.
    """
    row = conn.execute(
        "SELECT page_url, parent_key, path FROM page_section WHERE section_key = ?",
        (section_key,)).fetchone()
    if row is None or not row["parent_key"]:
        return []
    rows = conn.execute(
        """SELECT section_key, path, depth, is_leaf FROM page_section
            WHERE page_url = ? AND parent_key = ? ORDER BY ordinal""",
        (row["page_url"], row["parent_key"])).fetchall()
    out = []
    seen = set()
    for r in rows:
        lab = leaf_label(r["path"])
        if not (MIN_LABEL <= len(lab) <= MAX_LABEL) or lab in seen:
            continue
        seen.add(lab)
        out.append({"key": r["section_key"], "label": lab})
    return out


def already_narrowed(question: str, sibs: list[dict]) -> str | None:
    """질문에 이미 한정어가 있나. 있으면 되물으면 안 된다.

    '군휴학 어떻게 해' 는 이미 골랐다. 되물으면 학생을 두 번 일하게 한다.
    """
    q = re.sub(r"\s+", "", question)
    for s in sibs:
        lab = re.sub(r"\s+", "", s["label"])
        # 라벨 전체가 들었거나, 라벨의 앞 2글자가 질문에 있으면 특정된 것으로 본다
        if lab and (lab in q or (len(lab) >= 2 and lab[:2] in q and len(lab[:2]) >= 2
                                 and lab[:2] not in ("휴학", "신청", "안내"))):
            return s["label"]
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    args = ap.parse_args(argv)

    conn = repo.connect(args.db, readonly=True)
    dist = collections.Counter()
    rows = []
    try:
        for topic, q, expect, must in QUESTIONS:
            r = section_search.search(conn, q, repo=repo)
            out = getattr(r.outcome, "value", str(r.outcome))
            if r.page_level:
                out += "/page"      # ★ 애매해서 페이지로 내린 것 — 되묻기가 노리는 칸
            key = r.top.section_key if r.top is not None else None
            sibs = siblings(conn, key) if key else []
            narrowed = already_narrowed(q, sibs) if sibs else None
            n = len(sibs)
            bucket = ("형제없음" if n == 0 else "1개" if n == 1
                      else "2~5개" if n <= 5 else "6~9개" if n <= 9 else "10개+")
            dist[(out, bucket)] += 1
            rows.append({"topic": topic, "q": q, "expect": expect, "outcome": out,
                         "n_sib": n, "bucket": bucket, "narrowed": narrowed,
                         "labels": [s["label"] for s in sibs[:6]]})
    finally:
        conn.close()

    print("═" * 72)
    print("결과 × 형제 수  — 되묻기가 발동할 수 있는 자리가 어디인가")
    print("═" * 72)
    outs = sorted({o for o, _ in dist})
    buckets = ["형제없음", "1개", "2~5개", "6~9개", "10개+"]
    print(f"  {'':12}" + "".join(f"{b:>9}" for b in buckets))
    for o in outs:
        line = f"  {o:12}"
        for b in buckets:
            line += f"{dist[(o, b)] or '·':>9}"
        print(line)

    print()
    print("═" * 72)
    print("되물을 수 있는 문항  (선택지는 형제 노드 그대로 — 사람이 안 만든다)")
    print("═" * 72)
    cand = [r for r in rows if 2 <= r["n_sib"] <= 5 and not r["narrowed"]]
    for r in cand:
        print(f"  [{r['outcome']:9}] {r['q']}")
        print(f"              → {' / '.join(r['labels'])}")
    if not cand:
        print("  없음")

    print()
    print("이미 한정어가 있어 되물으면 안 되는 문항")
    for r in rows:
        if r["narrowed"]:
            print(f"  {r['q']:22} 이미 고름: {r['narrowed']}")

    print()
    print(f"전체 {len(rows)}문항 · 되묻기 후보 {len(cand)} · "
          f"형제 6개 이상 {sum(1 for r in rows if r['n_sib'] >= 6)} (페이지로)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
