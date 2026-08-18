"""별칭이 **낱말 안쪽**에 걸리는 자리를 전수로 찾는다.

    python tools/alias_traps.py --db data/jbnu.db

★ 같은 종류가 두 번 나왔다
    학자금 대출        사이트 별칭에 걸렸다
    컴퓨터인공지능학부   '공지' 가 인**공지**능 안에서 걸렸다 (확신 답변 → 모른다)

  한글은 낱말 사이에 공백이 없어서 부분문자열이 그대로 덫이 된다.
  두 번 나왔으면 남은 별칭 전부를 같은 눈으로 볼 값어치가 있다.

★ 상상으로 만든 목록에 대고 재지 않는다
  '어떤 말이 위험할까' 를 떠올리면 안 떠올린 자리는 영영 안 잡힌다.
  그래서 **코퍼스에 실제로 있는 문서 제목**을 시험지로 쓴다.
  학생이 물을 만한 말은 대부분 학교가 쓰는 말이다.

★ 여기 뜬 게 전부 고장은 아니다
  '학사공지' 는 한쪽만 한글이라 통과하고, 그게 맞다.
  이 도구는 **양쪽이 다 한글인 자리**만 모은다 — 사람이 볼 자리를 좁혀 준다.
"""

from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import routing  # noqa: E402
from skill.section_search import site_aliases  # noqa: E402
from store import repo     # noqa: E402

HANGUL = re.compile(r"[가-힣]")


def inside_word(needle: str, hay: str) -> list[str]:
    """양쪽이 다 한글인 자리에 걸린 문맥을 모은다."""
    out = []
    i = hay.find(needle)
    while i != -1:
        before = hay[i - 1] if i > 0 else ""
        after = hay[i + len(needle)] if i + len(needle) < len(hay) else ""
        if HANGUL.match(before or " ") and HANGUL.match(after or " "):
            out.append(hay[max(0, i - 6):i + len(needle) + 6])
        i = hay.find(needle, i + 1)
    return out


def aliases_in_use() -> list[tuple[str, str]]:
    """(별칭, 핸들러). 코드에 박힌 게 아니라 config 에서 읽는다."""
    doc = routing.load()
    out = []
    for handler, names in (doc.get("handlers") or {}).items():
        for n in list(names or []) + [handler]:
            if len(routing._norm(n)) >= 2:
                out.append((n, handler))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    ap.add_argument("--limit", type=int, default=4000,
                    help="시험지로 쓸 문서 제목 수")
    args = ap.parse_args(argv)

    conn = repo.connect(pathlib.Path(args.db), readonly=True)
    try:
        titles = [r[0] for r in conn.execute(
            "SELECT DISTINCT title FROM page_registry "
            "WHERE title IS NOT NULL AND title <> '' LIMIT ?",
            (args.limit,))]
        paths = [r[0] for r in conn.execute(
            "SELECT DISTINCT path FROM page_section "
            "WHERE parent_key IS NULL AND path IS NOT NULL LIMIT ?",
            (args.limit,))]
    finally:
        conn.close()
    corpus = [t for t in titles + paths if t]

    print("=" * 74)
    print(f"별칭 전수 — 낱말 안쪽에 걸리는 자리 (시험지 {len(corpus):,}개 제목)")
    print("=" * 74)

    hits: dict[tuple[str, str], collections.Counter] = {}
    for alias, handler in aliases_in_use():
        ctr: collections.Counter = collections.Counter()
        for t in corpus:
            for ctx in inside_word(alias, t):
                ctr[ctx] += 1
        if ctr:
            hits[(alias, handler)] = ctr

    if not hits:
        print("\n★ 낱말 안쪽에 걸리는 별칭 없음")
        return 0

    print(f"\n★ 낱말 안쪽에 걸리는 별칭 {len(hits)}종")
    print("  (지금은 걸러진다. 경계 규칙이 없었다면 전부 오라우팅이었다)")
    for (alias, handler), ctr in sorted(hits.items(),
                                        key=lambda x: -sum(x[1].values())):
        total = sum(ctr.values())
        print(f"\n  [{alias}] → {handler}   {total}회")
        for ctx, n in ctr.most_common(4):
            print(f"       {n:4}회  …{ctx}…")

    # ═══════════════════════════════════════════════════════════
    # 두 번째 종류 — **사이트 별칭이 질문의 낱말을 먹는다**
    # ═══════════════════════════════════════════════════════════
    # ★ 세 번 나왔다. 개별로 고치면 네 번째는 개강 뒤에 나온다.
    #     학자금 대출          사이트 별칭에 먹혔다
    #     컴퓨터인공지능학부     별칭이 낱말 안쪽에 걸렸다 (위 검사)
    #     취업                career.jbnu.ac.kr 별칭에 먹혔다 —
    #                        그 사이트 공지는 0건인데 거기로 좁혀서
    #                        제목에 '취업' 이 든 공지 139건을 0건으로 만들었다
    #
    # ★ 재는 방법: 별칭이 **자기 사이트 밖에서 얼마나 쓰이나**
    #   '취업' 은 취업진로지원과의 이름이면서 891건의 공지 제목에 든 말이다.
    #   그런 별칭은 좁히는 순간 질문을 잘못된 곳으로 보낸다.
    # ═══════════════════════════════════════════════════════════
    # 세 번째 종류 — **학사일정 주제 별칭**이 질문의 낱말을 먹는다
    # ═══════════════════════════════════════════════════════════
    # ★ 이 표면을 뒤늦게 넣었다 (2026-08-18)
    #   검색 블록으로 온 시간 질문을 학사일정으로 넘기게 고쳤더니,
    #   그 순간 이 별칭들이 **답을 가르는 자리**가 됐다.
    #   테스트가 바로 잡았다 — '동아리 등록 기간' 이 등록금 납부로 갔다.
    #   별칭 '등록' 이 2글자라 '동아리 등록' 을 먹은 것이다.
    #   같은 병이 네 번째다. 표면이 늘 때마다 여기 한 칸씩 붙인다.
    #
    # ★ 재는 방법: **자기보다 긴 같은 주제 별칭이 안 붙은 비율**
    #   '등록'(2글자)이 든 공지 중 '등록금'·'등록기간'이 없는 것은
    #   '동아리 등록' 처럼 다른 주제일 가능성이 높다.
    #
    # ★ 첫 잣대가 틀렸다 — title_keywords 로 쟀다 (같은 날 고침)
    #   등록금 주제의 title_keywords 가 ['등록'] 이라 **자기 자신을 뺐고**,
    #   실제 덫인 '등록' 이 0.0% 로 나왔다. 방학은 keywords 가 ['휴가'] 라
    #   덫이 아닌데 100% 로 나왔다. 자가 틀리면 진단이 틀린다.
    #
    # ★ 이건 **판정이 아니라 볼 순서다**
    #   동의어('졸업식'→학위수여식)와 부분문자열('등록'⊂'동아리 등록')을
    #   이 자로는 못 가른다. 위에 오는 것부터 사람이 보면 된다.
    print("\n" + "═" * 74)
    print("학사일정 주제 별칭이 질문의 낱말을 먹는가")
    print("═" * 74)
    try:
        import yaml
        topics = yaml.safe_load(
            (ROOT / "config" / "calendar_topics.yaml").read_text(
                encoding="utf-8"))["topics"]
        c3 = repo.connect(pathlib.Path(args.db), readonly=True)
        rows3 = []
        for key, t in topics.items():
            kws = t.get("title_keywords") or []
            for alias in (t.get("utterance_aliases") or []):
                if len(alias) < 2:
                    continue
                tot = c3.execute(
                    "SELECT COUNT(*) FROM notice_item WHERE title LIKE ?",
                    (f"%{alias}%",)).fetchone()[0]
                if not tot:
                    continue
                longer = [a for a in (t.get("utterance_aliases") or [])
                          if len(a) > len(alias) and alias in a]
                if not longer:
                    continue     # 자기가 그 주제의 가장 긴 말이면 검사 대상이 아니다
                cond = " AND ".join(["title NOT LIKE ?"] * len(longer))
                off = c3.execute(
                    "SELECT COUNT(*) FROM notice_item WHERE title LIKE ? "
                    f"AND {cond}",
                    (f"%{alias}%", *[f"%{a}%" for a in longer])).fetchone()[0]
                rows3.append((alias, key, tot, off))
        c3.close()
        rows3.sort(key=lambda r: -(r[3] / r[2] if r[2] else 0))
        print(f"\n{'별칭':14} {'주제':12} {'그 말이 든 공지':>12} "
              f"{'긴말 안붙음':>10}  비율")
        print("─" * 74)
        for alias, key, tot, off in rows3[:12]:
            pct = off * 100 / tot if tot else 0
            mark = "  ← 먼저 볼 것" if pct >= 50 else ""
            print(f"{alias:14} {key:12} {tot:>12} {off:>10}  {pct:5.1f}%{mark}")
        print("\n  ★ 짧은 별칭일수록 위험하다 — '등록'(2글자)이 '동아리 등록'을 먹었다.")
        print("    긴 별칭이 이미 있으면(등록기간·등록금) 짧은 것은 빼는 게 낫다.")
    except Exception as e:  # noqa: BLE001
        print(f"  (건너뜀 — {type(e).__name__}: {e})")

    print("\n" + "═" * 74)
    print("사이트 별칭이 질문의 낱말을 먹는가")
    print("═" * 74)
    conn2 = repo.connect(pathlib.Path(args.db), readonly=True)
    try:
        rows = []
        for alias, host in sorted(site_aliases().items()):
            if len(alias) < 2:
                continue
            try:
                outside = conn2.execute(
                    "SELECT COUNT(*) FROM notice_item "
                    "WHERE title LIKE ? AND host <> ?",
                    (f"%{alias}%", host)).fetchone()[0]
                own = repo.notice_total(conn2, host=host)
            except Exception:  # noqa: BLE001
                continue
            if outside:
                rows.append((alias, host, outside, own))
        rows.sort(key=lambda r: -r[2])
        print(f"\n{'별칭':12} {'좁혀갈 사이트':26} "
              f"{'밖에서 쓰인 공지':>10} {'그 사이트 공지':>10}")
        print("─" * 74)
        for alias, host, outside, own in rows[:15]:
            mark = "  ★ 그 사이트엔 공지가 없다" if own == 0 else ""
            print(f"{alias:12} {host[:25]:26} {outside:>10,} {own:>10,}{mark}")
        blind = [r for r in rows if r[3] == 0]
        print()
        if blind:
            print(f"★ 공지 0건짜리 사이트로 좁히는 별칭 {len(blind)}종 — "
                  f"공지 검색에서는 좁히기가 취소된다")
            for alias, host, outside, _own in blind[:8]:
                print(f"    {alias:12} → {host:26} (밖에서 {outside:,}건)")
        else:
            print("★ 공지 0건짜리 사이트로 좁히는 별칭 없음")
    finally:
        conn2.close()

    # 지금 규칙이 실제로 막고 있는지 확인한다 — 주장하지 않고 눌러 본다
    #
    # ★ **어느 별칭이 맞췄는지**를 봐야 한다
    #   '학부공지사항' 은 notice.search 로 가는 게 맞다. 다만 그건 '공지' 가 아니라
    #   더 긴 '공지사항' 이 맞춘 것이고, 그 별칭은 뒤가 문자열 끝이라 경계가 있다.
    #   핸들러만 보고 '샌다' 고 세면 **맞는 동작을 고장으로 신고**하게 된다.
    #   (실제로 이 도구를 만들다 그렇게 셌다)
    print("\n" + "─" * 74)
    print("경계 규칙이 실제로 막는지 확인 — 어느 별칭이 맞췄는지로 본다")
    bad = 0
    for (alias, _handler), ctr in hits.items():
        for ctx in list(ctr)[:3]:
            _got, why = routing.by_utterance(ctx)
            if why == f"alias:{alias}":
                print(f"  ❌ {ctx!r} — '{alias}' 가 낱말 안쪽에서 맞췄다 ({why})")
                bad += 1
    print("  ✅ 낱말 안쪽으로는 아무것도 안 맞는다" if not bad
          else f"  ★ 아직 새는 자리 {bad}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
