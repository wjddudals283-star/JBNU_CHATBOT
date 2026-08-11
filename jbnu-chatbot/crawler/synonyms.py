"""제목 동의어를 **관측으로** 찾는다.

학과마다 같은 것을 다르게 부른다.

    졸업요건 / 졸업기준 / 학번별졸업요건 / 학번별졸업기준 / 이수학점 및 졸업요건

손으로 목록을 적으면 학과 100곳에서 또 새 표현이 나온다.
공통점은 **여러 학과 사이트에서 같은 자리(메뉴 위치)를 차지한다**는 것이고,
그게 관측이다.

★ 신호 셋을 모두 만족해야 묶는다

  1. 어휘 겹침    두 제목이 같은 말조각을 공유한다 ('졸업')
  2. 상보 분포    한 사이트에 둘 다 있지는 않다 — 같은 자리를 다르게 부르므로
  3. **본문 겹침**  두 제목의 페이지들이 같은 말로 쓰여 있다

  상보 분포만 쓰면 안 된다. 사이트가 204곳인데 23곳과 8곳이 무작위로 흩어져도
  겹침 기대치는 0.9다 — 겹침 0 은 우연일 수 있다.

  처음엔 '메뉴 이웃' 을 셋째 신호로 썼는데 약했다. 학과마다 메뉴 구성이 달라서
  '졸업요건' 의 이웃만 74종이었고 자카드가 0.06 까지 떨어졌다.
  본문은 다르다 — 실측:

      졸업요건 × 졸업기준        0.68   ← 같은 것
      연구실소개 × 실험실소개      0.30
      연구소 규정 × 연구소 연혁    0.05   ← 다른 것
      졸업요건 × 공지사항         0.00

  '연구소' 를 공유해도 규정과 연혁은 본문이 다르다. 어휘만으로는 못 가르는 것을
  본문이 가른다.

★ 근거를 함께 남긴다
  몇 곳에서 관측했고 이웃이 얼마나 겹쳤는지 적는다.
  근거 없는 묶음은 손으로 적은 목록과 다를 바 없다.
"""

from __future__ import annotations

import collections
import itertools
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIN_SITES = 3          # 이보다 적은 곳에만 나오면 표본이 아니다
MAX_HOST_OVERLAP = 0.1  # 같은 자리면 한 사이트에 둘 다 있지 않다
MIN_CONTENT = 0.25     # 본문이 이만큼은 겹쳐야 '같은 것' 이다
PROFILE_TOP = 40       # 본문 지문으로 쓸 상위 낱말 수
MIN_COMMON_CHARS = 2   # 어휘 겹침 최소 길이


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def _longest_common(a: str, b: str) -> str:
    """가장 긴 공통 부분 문자열. 짧은 제목이라 단순 비교로 충분하다."""
    best = ""
    for i in range(len(a)):
        for j in range(i + len(best) + 1, len(a) + 1):
            if a[i:j] in b and (j - i) > len(best):
                best = a[i:j]
    return best


_WORD = re.compile(r"[가-힣]{2,}")


def content_profile(conn, title: str, top: int = PROFILE_TOP) -> set[str]:
    """그 제목을 단 페이지들의 본문 지문 — 자주 나오는 낱말 상위 N."""
    rows = conn.execute(
        """SELECT s.text FROM page_section s
             JOIN page_registry r ON r.page_url = s.page_url
            WHERE r.title = ? AND s.is_leaf = 1 LIMIT 3000""",
        (title,)).fetchall()
    cnt: collections.Counter = collections.Counter()
    for (t,) in rows:
        for w in _WORD.findall(t or ""):
            cnt[w] += 1
    return {w for w, _ in cnt.most_common(top)}


def load_menu_order() -> dict[str, list[str]]:
    """호스트별 메뉴 순서. 발견 당시 index 페이지의 링크 순서가 곧 메뉴 순서다."""
    import yaml
    out: dict[str, list[str]] = {}
    for name in ("pages_dept.yaml", "pages_sites.yaml"):
        p = ROOT / "config" / name
        if not p.exists():
            continue
        for row in yaml.safe_load(p.read_text(encoding="utf-8")).get("pages", []):
            out.setdefault(row["host"], []).append(row["url"])
    return out


def observe(conn, *, min_sites: int = MIN_SITES,
            verbose: bool = False) -> list[dict]:
    titles = {r["page_url"]: (r["title"] or "").strip()
              for r in conn.execute(
                  "SELECT page_url, title FROM page_registry")}
    order = load_menu_order()

    hosts_of: dict[str, set[str]] = collections.defaultdict(set)
    for host, urls in order.items():
        for u in urls:
            t = titles.get(u, "")
            if t:
                hosts_of[t].add(host)

    cands = [t for t, hs in hosts_of.items() if len(hs) >= min_sites]
    prof = {t: content_profile(conn, t) for t in cands}
    pairs = []
    for a, b in itertools.combinations(sorted(cands), 2):
        na, nb = _norm(a), _norm(b)
        common = _longest_common(na, nb)
        if len(common) < MIN_COMMON_CHARS:
            continue
        A, B = hosts_of[a], hosts_of[b]
        overlap = len(A & B) / max(min(len(A), len(B)), 1)
        if overlap > MAX_HOST_OVERLAP:
            continue                      # 같은 사이트에 둘 다 있다 = 다른 것
        pa, pb = prof[a], prof[b]
        if not pa or not pb:
            continue
        # 크기 차이에 벌점을 주지 않는다 — 한쪽이 사이트가 적으면 지문도 작다
        content = len(pa & pb) / max(min(len(pa), len(pb)), 1)
        if content < MIN_CONTENT:
            continue                      # 같은 말로 쓰여 있지 않다 = 다른 것
        pairs.append({"a": a, "b": b, "common": common,
                      "sites_a": len(A), "sites_b": len(B),
                      "host_overlap": round(overlap, 3),
                      "content": round(content, 3)})

    # ★ 이어 붙이지 않는다.
    #   'A≡B 이고 B≡C 이면 A≡C' 는 추론이고, 우리는 추론으로 값을 만들지 않는다.
    #   실제로 그렇게 묶었더니 '학사안내 ≡ 장학제도' 가 나왔다 —
    #   '학사안내'—'장학안내'—'장학' 사슬을 타고 양 끝이 붙은 것이다.
    #   그룹 안의 **모든 쌍**이 직접 관측돼야 한다.
    linked: dict[str, set[str]] = collections.defaultdict(set)
    for p in pairs:
        linked[p["a"]].add(p["b"])
        linked[p["b"]].add(p["a"])

    seen: set[str] = set()
    groups_list: list[list[str]] = []
    # 널리 쓰이는 이름부터 씨앗으로 삼는다
    for seed in sorted(linked, key=lambda t: -len(hosts_of[t])):
        if seed in seen:
            continue
        members = [seed]
        for cand in sorted(linked[seed], key=lambda t: -len(hosts_of[t])):
            if cand in seen:
                continue
            if all(cand in linked[m] for m in members):
                members.append(cand)
        if len(members) > 1:
            seen.update(members)
            groups_list.append(members)

    out = []
    for members in groups_list:
        members = sorted(set(members), key=lambda t: -len(hosts_of[t]))
        ev = [p for p in pairs if p["a"] in members and p["b"] in members]
        out.append({
            "label": members[0],                 # 가장 널리 쓰이는 이름
            "members": members,
            "sites": sorted({h for t in members for h in hosts_of[t]}),
            "site_count": len({h for t in members for h in hosts_of[t]}),
            "content_min": min(p["content"] for p in ev),
            "host_overlap_max": max(p["host_overlap"] for p in ev),
            "pairs": len(ev),
        })
    out.sort(key=lambda g: -g["site_count"])
    if verbose:
        print(f"후보 제목 {len(cands)} · 쌍 {len(pairs)} · 그룹 {len(out)}")
    return out
