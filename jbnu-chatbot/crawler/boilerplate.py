"""보일러플레이트 자동 탐지 — 템플릿을 관측으로 가려낸다.

★ 하드코딩하지 않는다
  '만족도조사결과' 같은 목록을 코드에 박으면 CMS 가 바뀔 때 깨지고,
  한 번 잘못 걸면 수백 페이지에 그 오류가 퍼진다. 손으로 관리할 규모가 아니다.

★ 판별 근거는 관측이다
  본문은 페이지마다 다르고 템플릿은 모든 페이지에 같다.
  같은 조각이 여러 페이지에 반복 출현하면 그건 템플릿이다.

★ 판정 단위는 'DOM 조각'이지 '섹션'이 아니다
  섹션 단위로 매기면, 템플릿이 본문과 **같은 블록 안에** 있을 때
  블록 텍스트가 페이지마다 달라져 영영 안 걸린다. 실제로 그렇게 실패했다.
      블록 단위  → 0종 탐지
      섹션 단위  → 5종 탐지, 노이즈 126→51 (블록 본문에 섞인 51은 못 잡음)
      조각 단위  → 파싱 **전에** DOM 에서 잘라내므로 블록 텍스트도 깨끗해진다
  그래서 2단계다. 1차로 전 페이지 조각을 세고, 2차로 그 해시를 잘라낸 뒤 파싱한다.

★ 임계는 실측에서 온다
  /web/ 107페이지 반복 분포:  40% 만족도조사 · 9% 표 안 숫자 · 2% 이하 본문.
  40% 와 9% 사이가 4배 벌어져 있다. 그 골짜기에 임계를 둔다.
  이 값은 가정이 아니라 관측이고, 관측이 바뀌면 다시 재야 한다.

★ 지우는 쪽이 되돌리기 어렵다
  표본이 적으면 아무것도 지우지 않는다. 짧은 조각은 후보에서 뺀다.
  본문을 통째로 날릴 만큼 큰 조각은 임계를 넘어도 남긴다.
  그리고 무엇을 얼마나 지웠는지 반드시 보고한다 — 조용한 삭제는 커버리지 착시를 만든다.
"""

from __future__ import annotations

import collections
import hashlib
import re
from dataclasses import dataclass, field

DEFAULT_RATIO = 0.25
MIN_PAGES = 5               # 표본이 이보다 적으면 판정하지 않는다
BORDERLINE = 0.5            # 임계의 이 배율 위쪽은 '경계선'으로 보고한다
MIN_FRAG_CHARS = 6          # 이보다 짧은 조각은 후보에서 뺀다 (우연히 겹친다)
MAX_FRAG_SHARE = 0.8        # 본문의 이 비율을 넘는 조각은 지우지 않는다

# 조각으로 볼 태그 — 텍스트를 담는 구조 노드
FRAG_TAGS = ("div", "ul", "ol", "li", "table", "tr", "p", "dl", "dt", "dd",
             "section", "article", "span", "strong")


def frag_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest()[:16]


def fragments(node) -> dict[str, str]:
    """한 페이지 본문의 조각 해시 → 표본 텍스트.

    같은 해시가 한 페이지에 여러 번 나와도 1회로 센다 (페이지 출현 수가 기준).
    """
    out: dict[str, str] = {}
    for n in node.traverse(include_text=False):
        if n.tag not in FRAG_TAGS:
            continue
        t = re.sub(r"\s+", " ", n.text() or "").strip()
        if len(t) < MIN_FRAG_CHARS:
            continue
        out.setdefault(frag_hash(t), t[:70])
    return out


@dataclass
class BoilerplateReport:
    total_pages: int
    hashes: set[str] = field(default_factory=set)
    detail: list[dict] = field(default_factory=list)
    borderline: list[dict] = field(default_factory=list)
    threshold_pages: int = 0
    skipped_reason: str = ""

    def is_boilerplate(self, h: str) -> bool:
        return bool(h) and h in self.hashes

    def summary(self) -> dict:
        return {"pages": self.total_pages, "threshold_pages": self.threshold_pages,
                "template_fragments": len(self.hashes),
                "borderline": len(self.borderline),
                "skipped_reason": self.skipped_reason,
                "top": self.detail[:10]}


def detect(page_fragments: list[dict[str, str]], *, ratio: float = DEFAULT_RATIO,
           min_pages: int = MIN_PAGES) -> BoilerplateReport:
    """page_fragments: 페이지마다 fragments() 결과 하나씩."""
    total = len(page_fragments)
    rep = BoilerplateReport(total_pages=total)
    if total < min_pages:
        rep.skipped_reason = f"표본 {total}페이지 < 최소 {min_pages} — 판정 보류"
        return rep

    seen: collections.Counter = collections.Counter()
    sample: dict[str, str] = {}
    for frags in page_fragments:
        for h, t in frags.items():
            seen[h] += 1
            sample.setdefault(h, t)

    threshold = max(min_pages, int(total * ratio))
    rep.threshold_pages = threshold
    for h, n in seen.items():
        row = {"hash": h, "pages": n, "ratio": round(n / total, 2),
               "sample": sample.get(h, "")}
        if n >= threshold:
            rep.hashes.add(h)
            rep.detail.append(row)
        elif n >= threshold * BORDERLINE:
            # 임계 바로 아래 — 절벽이 좁아지면 여기서 먼저 보인다.
            # 조용히 넘어가면 임계가 언제 틀렸는지 알 수 없다.
            rep.borderline.append(row)
    rep.detail.sort(key=lambda d: -d["pages"])
    rep.borderline.sort(key=lambda d: -d["pages"])
    return rep


def prune(root, rep: BoilerplateReport) -> dict:
    """본문 DOM 에서 템플릿 조각을 잘라낸다. 파싱 **전에** 부른다.

    본문의 MAX_FRAG_SHARE 를 넘는 조각은 지우지 않는다 —
    본문을 통째로 날리는 쪽이 노이즈가 남는 쪽보다 나쁘다.
    """
    if not rep.hashes:
        return {"pruned": 0, "held": 0, "chars_removed": 0}

    total_chars = len(re.sub(r"\s+", "", root.text() or ""))
    victims, held = [], 0
    for n in root.traverse(include_text=False):
        if n.tag not in FRAG_TAGS:
            continue
        t = re.sub(r"\s+", " ", n.text() or "").strip()
        if len(t) < MIN_FRAG_CHARS or not rep.is_boilerplate(frag_hash(t)):
            continue
        if total_chars and len(re.sub(r"\s+", "", t)) / total_chars > MAX_FRAG_SHARE:
            held += 1
            continue
        victims.append((n, len(re.sub(r"\s+", "", t))))

    removed = 0
    chars = 0
    for n, size in victims:
        # 조상이 먼저 잘리면 자손 노드는 이미 사라졌다. decompose 는 멱등하지 않으므로
        # 실패를 삼킨다.
        try:
            n.decompose()
            removed += 1
            chars += size
        except Exception:  # noqa: BLE001
            pass
    return {"pruned": removed, "held": held, "chars_removed": chars}
