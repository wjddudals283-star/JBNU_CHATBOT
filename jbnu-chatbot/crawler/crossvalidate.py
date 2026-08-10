"""교차 검증 (게이트 5) — 1차·2차 원천 대조.

두 원천이 같은 주의 같은 식단을 독립적으로 제공한다.

| | 원천 | 비고 |
|---|---|---|
| 1차 | 생협 JSON API | date 지정 가능 → 백필 가능. **기준** |
| 2차 | 학교 XHR      | 이번 주만. 가격·운영시간 보유 |

규칙
  · 불일치 시 **1차 채택 + conflict 플래그** + 로그
  · 불일치율을 크롤 지표로 기록. 급등하면 한쪽 파서가 깨진 신호
  · 2차만 있고 1차가 없으면 2차 채택. confidence 를 낮추지는 않는다(둘 다 공식 원천)

★ 여기서 값을 합성하지 않는다. 둘을 섞어 "더 그럴듯한" 메뉴를 만드는 건 추론이다.
  1차를 그대로 쓰고, 다르다는 사실만 표시한다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from store import repo


@dataclass
class Conflict:
    facility_id: str
    date: str
    meal_type: str
    zone: str
    corner: str
    kind: str          # status | items | missing_in_primary | missing_in_secondary
    primary: str
    secondary: str

    @property
    def key(self) -> tuple:
        return (self.facility_id, self.date, self.meal_type, self.zone, self.corner)


@dataclass
class CrossReport:
    compared: int = 0
    # 둘 다 관측인데 말이 다른 경우. 이것만 진짜 충돌이다.
    conflicts: list[Conflict] = field(default_factory=list)
    # 한쪽만 관측한 경우. 충돌이 아니라 커버리지 차이다.
    coverage_gaps: list[Conflict] = field(default_factory=list)
    primary_only: int = 0
    secondary_only: int = 0
    adopted_secondary: int = 0
    adopted_by_observation: int = 0

    @property
    def content_conflict_rate(self) -> float:
        """★경보는 여기에만 건다. 실측 기준선 0.0% — 1건만 나와도 신호다."""
        return (len(self.conflicts) / self.compared) if self.compared else 0.0

    @property
    def coverage_gap_rate(self) -> float:
        """한쪽만 관측한 비율. 경보 아님. 추세만 본다.

        실측 기준선 11.1% — 생협은 진수원 조식에 diet:"" 를 주고,
        학교는 같은 칸에 '운영없음'을 명시한다. 파서 버그가 아니라 표현 차이다.
        """
        return (len(self.coverage_gaps) / self.compared) if self.compared else 0.0


def _key(m) -> tuple:
    return (m.facility_id, m.date, m.meal_type, m.zone, m.corner)


def _items(m) -> list[str]:
    return [repo.normalize_name(i.name) for i in m.items]


def _observed(m) -> bool:
    """이 칸에 대해 원천이 무언가를 **말했는가.**

    unknown 은 관측이 아니라 관측의 부재다 — 원천이 빈칸을 줬다는 뜻이다.
    """
    return m.service_status != "unknown"


def compare(primary: list, secondary: list) -> CrossReport:
    """두 파서 출력을 대조한다. DB 를 건드리지 않는 순수 함수다."""
    rep = CrossReport()
    pmap = {_key(m): m for m in primary}
    smap = {_key(m): m for m in secondary}

    for k, p in pmap.items():
        s = smap.get(k)
        if s is None:
            rep.primary_only += 1
            continue
        rep.compared += 1

        # ★ 관측의 부재는 충돌이 아니다. 침묵과 진술은 서로 모순하지 않는다.
        #   우선순위 규칙은 **둘 다 관측일 때만** 발동한다.
        p_obs, s_obs = _observed(p), _observed(s)
        if p_obs != s_obs:
            rep.coverage_gaps.append(Conflict(
                *k, kind="coverage_gap",
                primary=p.service_status, secondary=s.service_status))
            continue
        if not p_obs:
            continue      # 둘 다 미관측 → unknown 그대로. 사건이 아니다

        if p.service_status != s.service_status:
            rep.conflicts.append(Conflict(
                *k, kind="status",
                primary=p.service_status, secondary=s.service_status))
        elif _items(p) != _items(s):
            rep.conflicts.append(Conflict(
                *k, kind="items",
                primary=", ".join(i.name for i in p.items),
                secondary=", ".join(i.name for i in s.items)))

    for k, s in smap.items():
        if k not in pmap:
            rep.secondary_only += 1
    return rep


PRIMARY, SECONDARY = "primary", "secondary"


def merge(primary: list, secondary: list) -> tuple[list[tuple], CrossReport]:
    """대조 후 저장할 목록을 만든다. `(레코드, 출처)` 쌍을 돌려준다.

    출처를 같이 주는 이유 — 2차를 채택했으면 `source_id`/`source_url` 도
    2차를 가리켜야 한다. 답변에 원문 링크가 나가기 때문이다.

    | 상황 | 채택 |
    |---|---|
    | 둘 다 관측 · 일치 | 1차 |
    | 둘 다 관측 · 불일치 | **1차** + conflict 기록 + 경보 (값을 섞지 않는다) |
    | 한쪽만 관측 | **관측한 쪽** (차수 무관) |
    | 둘 다 미관측 | 1차 (unknown 그대로) |
    | 한쪽에만 칸이 있음 | 있는 쪽 |
    """
    rep = compare(primary, secondary)
    smap = {_key(m): m for m in secondary}
    pkeys = {_key(m) for m in primary}
    out: list[tuple] = []

    for m in primary:
        s = smap.get(_key(m))
        if s is not None and not _observed(m) and _observed(s):
            # 침묵 vs 진술 → 진술을 택한다. 이건 합성이 아니라 관측 우선이다.
            out.append((s, SECONDARY))
            rep.adopted_by_observation += 1
        else:
            out.append((m, PRIMARY))

    for m in secondary:
        if _key(m) not in pkeys:
            out.append((m, SECONDARY))
            rep.adopted_secondary += 1

    return out, rep


def mark_conflicts(conn: sqlite3.Connection, rep: CrossReport) -> int:
    """불일치한 레코드에 conflict 플래그를 남긴다.

    ★ status='conflict' 로 두면 조회에서 빠진다(status='verified' 필터).
      "다르다"는 이유로 답을 아예 막는 건 과하다 — 1차는 여전히 공식 원천이다.
      그래서 status 는 verified 로 두고 note 에 표시한다.
    """
    n = 0
    for c in rep.conflicts:
        mid = repo.meal_service_id(c.facility_id, c.date, c.meal_type, c.zone, c.corner)
        conn.execute(
            """UPDATE meal_service
                  SET note = COALESCE(note || ' | ', '') || ?
                WHERE id = ?""",
            (f"conflict({c.kind}): 2차={c.secondary[:80]}", mid),
        )
        n += conn.total_changes and 1 or 0
    return len(rep.conflicts)
