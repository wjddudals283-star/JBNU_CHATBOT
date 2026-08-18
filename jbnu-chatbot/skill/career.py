"""취업·비교과 목록 — 활동 먼저, 끝난 공고는 뺀다.

★ 대표 정의 (2026-08-14, 그대로 옮김)
  "여기서 말한 취업은 채용도 말하는거지만 대부분 취업에 도움되는 활동들"

★ 왜 갈라야 하나 (2026-08-18 실측)
  최근 30일 123건 중 학생이 지원할 수 있는 활동은 46건이고
  나머지 77건은 채용(직원 뽑기)이다. 섞어 내면 절반 이상이 노이즈다.
  버리지는 않는다 — 대표가 '채용도 일부' 라고 했다. **순서를 준다.**

★ 끝난 공고를 빼는 근거는 **원문이 적어 준 것**이다
  notice_item 에는 마감일 칸이 없다. 그런데 제목에 적힌 게 21건 있다 —
      건설공제조합 … 채용 연장공고(~8/18)
      [~8.9.(일) 18:00 마감]2026학년도 2학기 산학협력현장실습 실습생 모집
  우리가 지어낸 게 아니라 학교가 제목에 쓴 값이다. 그것만 쓴다.
  제목에 마감이 없으면 **안 뺀다** — 모르는 것을 지났다고 하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import re

# 대표 정의 그대로. 학생이 **지원할 수 있는** 활동.
ACTIVITY = ("특강", "캠프", "멘토링", "자격증", "인턴", "공모전", "어학",
            "취업동아리", "현장실습", "아카데미", "워크숍", "워크샵",
            "설명회", "박람회")

# ★ 모집이 아니라 **결과**를 알리는 글. 지원할 수 없다.
#   언어 표면이다 — 학교 관측이 아니라 '무엇을 알리는 글인가' 다.
FINISHED = ("합격자", "합격 발표", "합격발표", "면접 일정", "면접일정",
            "결과 발표", "결과발표", "최종 합격", "선발 결과", "당첨자",
            "수상작", "심사 결과")

# 제목에 적힌 마감. '~8/18' · '~8.9(일)까지' · '[~8.9.(일) 18:00 마감]'
_DEADLINE = re.compile(r"~\s*(\d{1,2})\s*[./]\s*(\d{1,2})"
                       r"|(\d{1,2})\s*[./]\s*(\d{1,2})\s*(?:\([월화수목금토일]\))?\s*까지")


def title_deadline(title: str, published_at: str) -> dt.date | None:
    """제목에 적힌 마감일. 없으면 None.

    ★ 연도는 제목에 거의 안 적힌다. **게시일의 연도**를 쓰되,
      마감이 게시일보다 뒤로 가야 하므로 넘어가면 다음 해로 본다
      (12월에 올린 '1/5 마감' 은 다음 해다).
      이건 추측이 아니라 규약이다 — 마감이 게시일보다 앞설 수는 없다.
    """
    m = _DEADLINE.search(title or "")
    if not m:
        return None
    mo, da = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    try:
        base = dt.date.fromisoformat((published_at or "")[:10])
    except ValueError:
        return None
    try:
        d = dt.date(base.year, int(mo), int(da))
    except ValueError:
        return None            # 2/30 같은 것은 날짜가 아니다
    # ★ 되감김은 **큰 간격**일 때만이다 (2026-08-18 테스트가 잡음)
    #   처음엔 '마감 < 게시일이면 무조건 다음 해' 로 뒀다. 그런데
    #   '(~8.9)' 인데 게시일이 8/10 인 글이 있다 — 하루 어긋난 것이다
    #   (수집 시각·재게시). 그걸 다음 해로 밀면 **지난 공고가 1년치 살아남는다.**
    #   12월 게시 · 1/5 마감(11개월 뒤로 감김)만 해넘김으로 본다.
    if d < base and (base - d).days > 180:
        try:
            d = dt.date(base.year + 1, int(mo), int(da))
        except ValueError:
            return None
    return d


def is_activity(title: str) -> bool:
    return any(w in (title or "") for w in ACTIVITY)


def is_finished(title: str) -> bool:
    return any(w in (title or "") for w in FINISHED)


def sort_and_filter(rows: list[dict], *, today: dt.date) -> tuple[list[dict], dict]:
    """(보여줄 목록, 뺀 이유별 건수).

    ★ 뺀 건수를 돌려준다. 조용히 줄이면 '왜 적지' 를 나중에 못 묻는다.
    """
    dropped = {"마감 지남": 0, "끝난 공고": 0, "같은 제목": 0}
    keep, seen = [], set()
    for r in rows:
        t = r.get("title") or ""
        # ★ 같은 글이 게시판 두 곳에 올라온다 (학과 + 본부).
        #   화면에서는 한 줄이어야 한다 — 두 번 보이면 목록이 짧아 보인다.
        key = " ".join(t.split())
        if key in seen:
            dropped["같은 제목"] += 1
            continue
        seen.add(key)
        if is_finished(t):
            dropped["끝난 공고"] += 1
            continue
        dl = title_deadline(t, r.get("published_at") or "")
        if dl is not None and dl < today:
            dropped["마감 지남"] += 1
            continue
        keep.append(r)
    # ★ 활동 먼저, 채용 뒤로. 같은 묶음 안에서는 최신순.
    keep.sort(key=lambda r: (0 if is_activity(r.get("title") or "") else 1,
                             -(_ord(r.get("published_at")))))
    return keep, dropped


def _ord(published_at: str | None) -> int:
    try:
        return dt.date.fromisoformat((published_at or "")[:10]).toordinal()
    except ValueError:
        return 0
