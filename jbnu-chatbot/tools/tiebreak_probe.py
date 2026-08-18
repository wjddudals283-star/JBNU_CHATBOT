"""1등이 동점일 때 무엇을 앞에 둘 것인가 — **붙이기 전에** 잰다.

    python tools/tiebreak_probe.py --db data/jbnu.db

★ 왜 이게 필요한가 (2026-08-18)
  ⚠️ 5건 화면을 확인했더니 셋의 원인이 하나였다. 점수가 아니라 **동점**이다.
      기계공학과 교수   4.04  명예교수 / 평생지도교수제 / 교수 / 교수진
      증명서 발급     144.18  행동강령 / FAQ("써트피아"로 증명서 발급)
      취업 상담         동점  212호 / 213호 / 214호 개별상담실
  답한 38건 중 19건이 동점이다. 깨는 기준이 없어 SQL 이 준 순서가 답이 된다.

★ 이 도구는 **아무것도 안 바꾼다**
  19건을 한꺼번에 움직이는 자리라, 붙이고 나서 재면 이미 늦다.
  같은 검색 결과에 규칙만 얹어 **어디로 가는지**를 먼저 보여준다.

★ 세 기준은 전부 관측이다 — 임계값이 없다
  1. 군더더기 없는 제목   질문 낱말을 담되 덧붙은 글자가 적은 제목
                        ('교수' > '명예교수' > '평생지도교수제')
  2. 여러 잎에 걸린 문서   같은 결과 안에서 잎이 여러 개 걸린 페이지
                        (행동강령 1잎 vs FAQ 여러 잎)
  3. 상위 페이지         URL 조각이 적은 쪽
                        (개별 상담실 방보다 상담 안내가 위다)
  순서대로 보고, 앞 기준이 갈리면 뒤는 안 본다.

★ 흉내내지 않는다 — **코드의 규칙을 그대로 부른다** (2026-08-18 고침)
  처음엔 규칙을 이 파일에 다시 적고 `score` 동점으로 흉내냈다.
  실제 코드는 `page_score` 로 가르는데 그걸 몰랐다 — 4건이 움직인다고
  예고했지만 실제로는 3건이었다. 재는 자가 실제 경로와 다르면
  측정이 거짓말을 한다. 그래서 section_search._tiebreak 을 직접 부른다.

★ 판정은 사람이 한다
  '좋아졌다' 를 자동으로 못 센다. 정답을 우리가 모르기 때문이다.
  그래서 **바뀐 것만** 전후로 나란히 찍는다. 대표가 읽고 정한다.
"""

from __future__ import annotations

import argparse
import logging
import pathlib
import re
import sys
from urllib.parse import urlsplit

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import section_search as S  # noqa: E402
from store import repo                 # noqa: E402
from tools.answerability_report import QUESTIONS  # noqa: E402

EPS = 1e-6


def tokens(q: str) -> list[str]:
    return [t for t in re.split(r"[^0-9A-Za-z가-힣+]+", q or "") if len(t) >= 2]


def title_fit(title: str, toks: list[str]) -> tuple[int, int]:
    """(맞은 낱말 수 · 덧붙은 글자 수). 앞은 클수록, 뒤는 작을수록 좋다.

    ★ '군더더기' 를 글자 수로 센다 — 임계값이 아니라 뺄셈이다.
      제목 '교수' 에 '교수' 가 맞으면 군더더기 0.
      '명예교수' 는 2, '평생지도교수제' 는 5.
    """
    t = title or ""
    hit = [w for w in toks if w in t]
    return (len(hit), len(t) - sum(len(w) for w in hit))


def depth_of(url: str) -> int:
    """URL 조각 수 — 상위 페이지일수록 작다."""
    return len([p for p in urlsplit(url or "").path.split("/") if p])


def rank_key(h, toks: list[str], leaves: dict[str, int]):
    """★ 규칙은 여기 없다 — 코드가 쓰는 것을 그대로 부른다."""
    url = getattr(h, "page_url", "") or ""
    return (*S._tiebreak(h, leaves.get(url, 0)), len(url))


def _label(h) -> str:
    return f"{getattr(h, 'site_name', '')} · {getattr(h, 'page_title', '')}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/jbnu.db")
    args = ap.parse_args(argv)
    logging.disable(logging.CRITICAL)
    conn = repo.connect(pathlib.Path(args.db), readonly=True)

    print("=" * 96)
    print("1등 동점을 깨면 답이 어디로 가나 — 아무것도 안 바꾸고 재기만 한다")
    print("=" * 96)

    tied = solo = changed = same = 0
    moved = []
    for _t, q, expect, _m in QUESTIONS:
        if expect != "answer":
            continue
        r = S.search(conn, q, repo=repo)
        hits = list(r.hits or [])
        if not hits:
            continue
        # ★ 동점은 **page_score** 로 본다 — 문서를 고르는 저울이 그것이다.
        #   score(섹션 저울) 로 봤다가 4건이 움직인다고 예고했고 실제는 3건이었다.
        top = hits[0].page_score
        group = [h for h in hits if abs(h.page_score - top) < EPS]
        if len(group) < 2:
            solo += 1
            continue
        tied += 1
        # ★ 잎 수는 **이 검색 결과 안에서** 센다. 코퍼스 전체가 아니다 —
        #   '이 질문에 이 문서가 여러 군데 걸렸나' 가 우리가 보려는 것이다.
        leaves: dict[str, int] = {}
        for h in hits:
            u = getattr(h, "page_url", "") or ""
            leaves[u] = leaves.get(u, 0) + 1
        toks = tokens(q)
        new = sorted(group, key=lambda h: rank_key(h, toks, leaves))
        if getattr(new[0], "page_url", "") == getattr(group[0], "page_url", ""):
            same += 1
            continue
        changed += 1
        moved.append((q, len(group), group[0], new[0], toks, leaves))

    print(f"\n답한 {tied + solo}건 중 1등 동점 {tied}건 · 단독 {solo}건")
    print(f"규칙을 얹으면 **{changed}건이 다른 문서로 간다** "
          f"(동점이지만 1등이 그대로인 것 {same}건)\n")

    for q, n, old, new, toks, leaves in moved:
        print("─" * 96)
        print(f"■ {q}   (동점 {n}개)")
        for tag, h in (("지금", old), ("바뀜", new)):
            fit_n, extra = title_fit(getattr(h, "page_title", ""), toks)
            u = getattr(h, "page_url", "") or ""
            print(f"   {tag}  {_label(h)[:44]:46} "
                  f"낱말{fit_n} 군더더기{extra:>2} 잎{leaves.get(u,0):>2} 깊이{depth_of(u)}")
        qp = getattr(new, "quote_path", "") or getattr(new, "path", "")
        print(f"         └ {(qp or '')[:80]}")

    print("\n" + "=" * 96)
    print("★ '좋아졌다' 는 자동으로 못 센다 — 정답을 우리가 모르기 때문이다.")
    print("  바뀐 것만 전후로 찍었으니 읽고 정하면 된다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
