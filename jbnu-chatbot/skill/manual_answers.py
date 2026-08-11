"""총학이 직접 확인해 넣은 답 (T4).

홈페이지에 없는 것 — 학점포기·수강신청 정정·기숙사 통금 — 은 크롤로 영영 못 채운다.
총학이 학사과·생활관에 전화해 확인한 것만 여기서 답한다.

★ 확인한 것만 나간다
  enabled 가 false 거나, verified_by / verified_at 가 비었으면 내보내지 않는다.
  안전 연락처에서 세운 규칙과 같다 — 미확인이 하나라도 있으면 그 항목은 안 쓴다.

★ 만료되면 스스로 꺼진다
  학사 제도는 바뀐다. 지난 답을 계속 내보내는 것이 모르는 것보다 위험하다.
  valid_to 가 지나면 답하지 않고, /admin/manual 이 그 사실을 보고한다.

★ 답에 출처를 붙인다
  홈페이지 링크가 없으므로 **어디에 물어 확인했는지**를 대신 밝힌다.
  학생이 검증할 수 있어야 한다 — 검증할 수 없는 답은 믿어달라는 요구다.
"""

from __future__ import annotations

import datetime as dt
import functools
import pathlib
from dataclasses import dataclass

CONFIG_PATH = (pathlib.Path(__file__).resolve().parents[1]
               / "config" / "manual_answers.yaml")


@dataclass
class ManualAnswer:
    key: str
    ask: list[str]
    answer: str
    source: str
    verified_by: str
    verified_at: str
    valid_to: str
    enabled: bool = False

    @property
    def ready(self) -> bool:
        """내보낼 수 있는 상태인가. 하나라도 비면 안 내보낸다."""
        return bool(self.enabled and self.answer.strip()
                    and self.verified_by and self.verified_at and self.valid_to
                    and not self.answer.strip().startswith("(확인 전"))

    def expired(self, today: str) -> bool:
        return bool(self.valid_to and self.valid_to < today)

    def status(self, today: str) -> str:
        if not self.enabled:
            return "꺼짐"
        if not self.ready:
            return "미확인 (확인자·확인일·만료일·본문 중 빠진 것이 있음)"
        return "만료" if self.expired(today) else "사용 중"


def _load(path: pathlib.Path) -> list[ManualAnswer]:
    import yaml
    if not path.exists():
        return []
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out = []
    for row in doc.get("answers") or []:
        out.append(ManualAnswer(
            key=str(row.get("key", "")),
            ask=[str(a) for a in (row.get("ask") or [])],
            answer=str(row.get("answer") or ""),
            source=str(row.get("source") or ""),
            verified_by=str(row.get("verified_by") or ""),
            verified_at=str(row.get("verified_at") or ""),
            valid_to=str(row.get("valid_to") or ""),
            enabled=bool(row.get("enabled")),
        ))
    return out


@functools.lru_cache(maxsize=1)
def load() -> list[ManualAnswer]:
    return _load(CONFIG_PATH)


def find(utterance: str, *, today: str | None = None,
         entries: list[ManualAnswer] | None = None) -> ManualAnswer | None:
    """질문에 해당하는 확인된 답. 없으면 None (그러면 검색으로 넘어간다).

    가장 **긴** 표현을 고른다. '통금' 과 '기숙사 통금' 이 둘 다 걸리면
    구체적인 쪽이 맞다.
    """
    today = today or dt.date.today().isoformat()
    best, best_len = None, 0
    for e in (entries if entries is not None else load()):
        if not e.ready or e.expired(today):
            continue
        for a in e.ask:
            if a and a in utterance and len(a) > best_len:
                best, best_len = e, len(a)
    return best


def report(today: str | None = None,
           entries: list[ManualAnswer] | None = None) -> dict:
    """/admin/manual 용. 무엇이 살아 있고 무엇이 만료됐는지 그대로 보고한다."""
    today = today or dt.date.today().isoformat()
    rows = entries if entries is not None else load()
    out = [{"key": e.key, "status": e.status(today), "valid_to": e.valid_to,
            "verified_by": e.verified_by, "verified_at": e.verified_at,
            "source": e.source, "ask": e.ask} for e in rows]
    live = [r for r in out if r["status"] == "사용 중"]
    return {
        "total": len(out),
        "live": len(live),
        "expired": sum(1 for r in out if r["status"] == "만료"),
        "unverified": sum(1 for r in out if r["status"].startswith("미확인")),
        "entries": out,
        # 만료가 임박한 것을 미리 알린다. 만료된 뒤에 아는 것은 늦다.
        "expiring_soon": [r["key"] for r in live
                          if r["valid_to"] <= _plus_days(today, 30)],
    }


def _plus_days(day: str, n: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=n)).isoformat()
