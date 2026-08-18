"""재는 자와 실제 경로를 **한 곳에 묶는다**.

★ 같은 병이 두 번 났다 (2026-08-18)
    1차  검수 CSV 가 공지 문항을 **안내 검색** 결과로 실었다.
    2차  리포트가 '수강신청 언제야' 를 **검색**으로 쟀다.
         학생은 학사일정으로 간다. 받지도 않는 답을 채점할 뻔했다.
  한 번이면 사고, 두 번이면 부류다.

★ 그래서 갈래를 **흉내내지 않는다 — 끌어온다**
  두 도구가 각자 if 문을 적으면 서버가 바뀔 때마다 조용히 어긋난다.
  여기서는 `server.route_of` 를 그대로 불러 갈래를 정한다.
  route_of 가 답을 만들지 않고 '누가 받는지' 만 정하도록 만들어 둔 게
  여기서 값을 한다 — DB 를 안 보므로 재는 쪽에서 부담 없이 부를 수 있다.

★ 새 경로가 생기면 **조용히 어긋나지 않고 시끄럽게 남는다**
  route_of 가 우리가 모르는 갈래를 돌려주면 kind="못 읽음" 으로 나오고,
  도구가 그 건수를 찍는다. 다음번엔 눈으로 발견하지 않아도 된다.
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from skill import calendar_search  # noqa: E402
from skill import manual_answers   # noqa: E402
from skill import section_search as ss  # noqa: E402
from skill import server           # noqa: E402
from store import repo             # noqa: E402


def payload(utterance: str) -> dict:
    return {"userRequest": {"utterance": utterance}, "action": {"params": {}}}


class _Hit:
    """검색 결과 자리에 끼워 넣는 최소한의 그릇. judge 가 읽는 것만 채운다."""

    def __init__(self, *, site_name, page_title, text, url="", path=""):
        self.site_name = site_name
        self.page_title = page_title
        self.title = page_title
        self.board_name = ""
        self.text = text
        self.quote_text = text
        self.path = path or page_title
        self.quote_path = path or page_title
        self.page_url = url
        self.url = url
        self.score = 1.0
        self.page_score = 1.0
        self.matched = []
        self.observed_at = ""
        self.page_modified = None


class _Result:
    def __init__(self, outcome, top):
        self.outcome = outcome
        self.top = top
        self.hits = [top] if top is not None else []
        self.query_tokens = []
        self.defer_reason = ""
        self.page_level = False


class Observed:
    """한 문항을 실제 경로로 한 번 태운 결과.

    kind 는 판정이 아니라 **어느 자가 읽을 수 있나**다.
        search / notices / manual / calendar   → judge 가 읽는다
        못 읽음                                → 자가 모르는 경로다. 시끄럽게 남긴다.
    """

    def __init__(self, *, question, route, why, kind, result):
        self.question = question
        self.route = route
        self.why = why
        self.kind = kind
        self.result = result

    @property
    def readable(self) -> bool:
        return self.result is not None


# route_of 가 돌려주는 갈래 → 무엇으로 재는가.
# ★ 목록을 여기 한 곳에만 둔다. 두 도구가 각자 적으면 또 갈린다.
_AS_SEARCH = {"info.search"}
_AS_NOTICES = {"notice.search"}
_AS_CALENDAR = {"deadline.upcoming"}
# ★ **학교 사실을 주장하지 않는** 갈래.
#   '안녕하세요' 의 expect=defer 는 "인사하지 마라" 가 아니라
#   "학교 사실을 답하지 마라" 다. 인사·웰컴은 사실 주장이 아니므로
#   답했다고 세면 안 된다 — 실제로 그렇게 세서 확신 오답 1건이 났다.
_NO_CLAIM = {"smalltalk", "welcome"}


def observe(conn, question: str, *, today: dt.date | None = None,
            db_path=None) -> Observed:
    """★ 갈래를 정하는 건 우리가 아니라 서버다."""
    route, why = server.route_of(payload(question), None)

    if route == "manual":
        e = manual_answers.find(question)
        top = _Hit(site_name="총학 확인", page_title=e.key, text=e.answer)
        return Observed(question=question, route=route, why=why,
                        kind="manual", result=_Result(ss.Outcome.FOUND, top))

    if route in _AS_SEARCH:
        return Observed(question=question, route=route, why=why,
                        kind="search", result=ss.search(conn, question, repo=repo))

    if route in _AS_NOTICES:
        return Observed(question=question, route=route, why=why, kind="notices",
                        result=ss.search_notices(conn, question, repo=repo))

    if route in _AS_CALENDAR:
        return Observed(question=question, route=route, why=why,
                        kind="calendar",
                        result=_calendar_result(conn, question, today=today))

    # ★ 나머지 갈래(안전·인사·학식…)는 **렌더된 답을 그대로 읽는다**
    #   검색 결과가 없으니 흉내낼 것도 없다. 학생이 받는 화면이 곧 답이다.
    #   db_path 가 없으면 못 읽는다고 남긴다 — 조용히 검색으로 안 떨어뜨린다.
    if db_path is not None:
        text = rendered_text(db_path, question)
        if text:
            top = _Hit(site_name="화면 그대로", page_title=route, text=text)
            # 사실을 주장하지 않는 갈래는 '답하지 않았다' 쪽으로 센다.
            oc = (ss.Outcome.NOT_FOUND if route in _NO_CLAIM
                  else ss.Outcome.FOUND)
            return Observed(question=question, route=route, why=why,
                            kind="화면", result=_Result(oc, top))

    return Observed(question=question, route=route, why=why,
                    kind="못 읽음", result=None)


def rendered_text(db_path, question: str) -> str:
    """서버가 실제로 내는 화면의 글을 한 덩어리로 모은다."""
    out = server.handle(pathlib.Path(db_path), None, payload(question))
    bits = []
    for o in out.get("template", {}).get("outputs", []):
        for k, v in o.items():
            if k == "simpleText":
                bits.append(v.get("text", ""))
            elif isinstance(v, dict):
                bits += [str(v.get("title") or ""), str(v.get("description") or "")]
    return " ".join(b for b in bits if b).strip()


# 학사일정 항목 검색은 서버와 **같은 창**으로 본다 (server._handle_calendar_item)
_BACK, _AHEAD = server.ITEM_SEARCH_BACK_DAYS, server.ITEM_SEARCH_AHEAD_DAYS


def _calendar_result(conn, question: str, *, today: dt.date | None) -> _Result:
    today = today or server.now_kst().date()
    rows = repo.query_calendar(
        conn,
        since=(today - dt.timedelta(days=_BACK)).isoformat(),
        until=(today + dt.timedelta(days=_AHEAD)).isoformat(), limit=500)
    r = calendar_search.search(rows, question)
    r.entries = calendar_search.rank(r.entries, today.isoformat())
    if r.outcome is not calendar_search.Outcome.FOUND or not r.entries:
        return _Result(ss.Outcome.NOT_FOUND, None)
    e = r.entries[0]
    span = e.get("start_date") or ""
    if e.get("end_date") and e["end_date"] != span:
        span = f"{span}~{e['end_date']}"
    top = _Hit(site_name="학사일정",
               page_title=(r.topic.label if r.topic else "학사일정"),
               text=f"{e.get('title', '')} — {span}",
               url=server.SCHEDULE_URL,
               path=e.get("title", ""))
    return _Result(ss.Outcome.FOUND, top)
