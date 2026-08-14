"""총학 공지·행사 (T4 — 구글시트).

★ 인스타 API 를 접고 시트로 왔다 (2026-08-14)
  Graph API 는 앱 심사 · 60일 토큰 만료 · 페이스북 페이지 연결이 따라온다.
  60일마다 사람이 기억해야 하는데, 학식 백필에서 그 구조가 학기 중에
  진다는 걸 이미 봤다.

★ T4 는 학생이 제일 믿는 자리다
  총학이 직접 넣은 것이라 크롤 결과보다 근거가 세다.
  그래서 **지어내기의 대가가 제일 크다** — 마감 지난 모집이 나가면
  크롤 오답보다 나쁘다.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from crawler.parsers import council_sheet
from skill import kakao, server, templates
from store import repo

TODAY = dt.date(2026, 8, 14)
NOW = dt.datetime.fromisoformat("2026-08-14T12:00:00+09:00")
LATER = dt.datetime.fromisoformat("2026-09-10T12:00:00+09:00")

CSV = """게시일,제목,내용,인스타링크,마감일,작성국
2026-08-13,2026 학문체 댄스제 모집,"모집 기간 : 8/13(수) ~ 8/25(월) 23:59
모집 대상 : 전북대 재학생 누구나
참가비 : 팀당 10,000원",https://instagram.com/p/AAA,2026-08-25,문화국
2026-08-14,사무실 이전 안내,"총학생회실이 학생회관 2층으로 옮겼습니다.",https://instagram.com/p/BBB,,사무국
2026-08-10,날짜가 이상한 글,본문,https://instagram.com/p/CCC,9월 초,홍보국
,,,,,
"""


def _db(tmp_path, csv_text: str = CSV, *, with_run: bool = True):
    p = tmp_path / "c.db"
    c = repo.connect(p)
    repo.init_db(c)
    c.execute("""INSERT INTO source_snapshot (id, source_key, url, fetched_at,
                 http_status, content_hash, content_path, media_type)
                 VALUES ('s','council_sheet','https://x',
                         '2026-08-14T09:00:00+09:00',200,'h','','html')""")
    parsed = council_sheet.parse(csv_text, today=TODAY)
    repo.upsert_council_posts(c, parsed.rows, source_id="s",
                              source_url="https://x",
                              observed_at="2026-08-14T09:00:00+09:00")
    if with_run:
        c.execute("""INSERT INTO crawl_run (id, source_key, started_at,
                     finished_at, outcome)
                     VALUES ('r','council_sheet','2026-08-14T09:00:00+09:00',
                             '2026-08-14T09:00:05+09:00','success')""")
    c.commit()
    c.close()
    return p


def _pay(u: str) -> dict:
    return {"userRequest": {"utterance": u}, "action": {"params": {}}}


def _text(r) -> str:
    parts = []
    for o in r["template"]["outputs"]:
        if "simpleText" in o:
            parts.append(o["simpleText"]["text"])
        elif "textCard" in o:
            parts.append(o["textCard"]["title"] + " " +
                         o["textCard"].get("description", ""))
        elif "listCard" in o:
            parts.append(" ".join(i["title"] for i in o["listCard"]["items"]))
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════
# ① 마감일이 지나면 후보에서 뺀다
# ═══════════════════════════════════════════════════════════════

def test_마감이_지나면_안_나간다(tmp_path):
    """★ 9월에 "8월 25일까지 신청하세요" 가 나가면 크롤 오답보다 나쁘다.

    총학이 직접 넣은 것이라 학생이 더 믿기 때문이다.
    """
    db = _db(tmp_path)
    assert "댄스제" in _text(server.handle(db, None, _pay("총학 공지"), now=NOW))
    assert "댄스제" not in _text(
        server.handle(db, None, _pay("총학 공지"), now=LATER))


def test_마감이_없는_글은_계속_나간다(tmp_path):
    """★ 없는 것과 지난 것은 다르다.

    마감 없는 안내(사무실 이전)를 '지났다' 로 보면 영영 안 나간다.
    """
    db = _db(tmp_path)
    assert "사무실 이전" in _text(
        server.handle(db, None, _pay("총학 공지"), now=LATER))


def test_마감이_지나도_지우지는_않는다(tmp_path):
    """★ 지운 것과 지난 것은 다른 사실이다.

    '왜 안 나갔지' 를 나중에 물을 수 있어야 한다.
    """
    db = _db(tmp_path)
    c = repo.connect(db, readonly=True)
    try:
        assert repo.council_expired_count(c, today="2026-09-10") == 1
        assert c.execute("SELECT COUNT(*) FROM council_post").fetchone()[0] == 2
    finally:
        c.close()


# ═══════════════════════════════════════════════════════════════
# ② 없으면 '없다' 가 아니라 '못 가져왔다'
# ═══════════════════════════════════════════════════════════════

def test_비었으면_못_가져왔다고_말한다(tmp_path):
    """★ 진짜 없는 건지 시트를 못 읽은 건지 우리는 구별 못 한다.

    구별 못 하는 걸 구별한 척하면 그게 지어내기다.
    학식 stale 문안과 같은 구조 — '없다' 로 단정하지 않는다.
    """
    p = tmp_path / "e.db"
    c = repo.connect(p)
    repo.init_db(c)
    c.commit()
    c.close()
    r = server.handle(p, None, _pay("총학 공지"), now=NOW)
    assert kakao.validate(r) == []
    t = _text(r)
    assert "못 가져왔어요" in t
    assert "없어요" not in t, "없다고 단정하면 안 된다"


def test_못_가져왔으면_인스타로_보낸다(tmp_path):
    """갈 길을 연다 — 총학 공지의 원본은 인스타다."""
    p = tmp_path / "e2.db"
    c = repo.connect(p)
    repo.init_db(c)
    c.commit()
    c.close()
    r = server.handle(p, None, _pay("총학 공지"), now=NOW)
    cards = [o["textCard"] for o in r["template"]["outputs"] if "textCard" in o]
    assert cards and cards[0]["buttons"][0]["webLinkUrl"].startswith(
        "https://www.instagram.com/")


def test_오래_안_읽었으면_그것도_말한다():
    a = _text(templates.render_council_empty(stale=True))
    b = _text(templates.render_council_empty(stale=False))
    assert "오래됐어요" in a and "오래됐어요" not in b


# ═══════════════════════════════════════════════════════════════
# ③ 캡션은 원문 그대로 — 요약하지 않는다
# ═══════════════════════════════════════════════════════════════

def test_캡션을_요약하지_않는다(tmp_path):
    """★ 8/14 에 자족성 판정기로 확인했다 —

    날짜·대상·방법·금액·마감시각이 캡션 안에 전부 있다.
    우리가 줄이면 그 값들이 사라진다.
    """
    db = _db(tmp_path)
    t = _text(server.handle(db, None, _pay("댄스제"), now=NOW))
    for must in ("8/13(수)", "8/25(월) 23:59", "전북대 재학생 누구나", "10,000원"):
        assert must in t, f"{must!r} 이 사라졌다"


def test_길면_자르되_자른_사실을_밝힌다():
    long_body = "가" * (templates.COUNCIL_BODY_BUDGET + 200)
    r = templates.render_council([{
        "title": "긴 글", "body": long_body, "link": "https://x",
        "deadline": None, "bureau": "문화국", "published_at": "2026-08-14"}])
    t = _text(r)
    assert "뒷부분이 있어요" in t
    assert kakao.validate(r) == []


def test_출처를_총학으로_밝힌다(tmp_path):
    """크롤 인용과 같은 무게로 읽히면 안 된다 — T4 가 더 무겁다."""
    db = _db(tmp_path)
    assert "총학생회가 직접 올린" in _text(
        server.handle(db, None, _pay("총학 공지"), now=NOW))


# ═══════════════════════════════════════════════════════════════
# T4 는 크롤보다 먼저다
# ═══════════════════════════════════════════════════════════════

def test_학생은_총학공지라고_안_친다(tmp_path):
    """★ '댄스제' 라고 친다.

    별칭이 걸릴 때만 T4 를 쓰면 '등급이 높다' 는 말이 실제로는 안 지켜진다.
    """
    db = _db(tmp_path)
    assert "댄스제" in _text(server.handle(db, None, _pay("댄스제"), now=NOW))


def test_제목만_본다_본문은_안_본다(tmp_path):
    """★ 캡션은 길고 흔한 낱말이 많다 ('신청' '기간' '학생').

    본문까지 보면 엉뚱한 질문이 총학 공지로 끌려간다.
    """
    db = _db(tmp_path)
    # '참가비' 는 본문에만 있다 — 이걸로는 안 끌려가야 한다
    t = _text(server.handle(db, None, _pay("참가비"), now=NOW))
    assert "총학생회가 직접 올린" not in t


def test_마감_지난_글은_승격도_안_된다(tmp_path):
    db = _db(tmp_path)
    t = _text(server.handle(db, None, _pay("댄스제"), now=LATER))
    assert "총학생회가 직접 올린" not in t


def test_총학_별칭이_학교_공지보다_먼저다():
    from skill import routing
    assert routing.by_utterance("총학 공지")[0] == "council.notice"
    assert routing.by_utterance("총학공지")[0] == "council.notice"
    # 학교 공지는 그대로
    assert routing.by_utterance("수강신청 공지")[0] == "notice.search"


# ═══════════════════════════════════════════════════════════════
# 파서 — 이상한 값을 지어내지 않는다
# ═══════════════════════════════════════════════════════════════

def test_마감일이_날짜가_아니면_그_줄을_격리한다():
    """★ '9월 초' 를 2026-09-01 로 바꾸면 총학이 안 쓴 마감일을 우리가 만든 것이다.

    마감일을 NULL 로 두고 내보내도 안 된다 — 지난 모집이 영영 안 꺼진다.
    지어내지도, 무시하지도 않는다.
    """
    parsed = council_sheet.parse(CSV, today=TODAY)
    assert parsed.counts == (2, 1)
    assert "9월 초" in parsed.quarantined[0][1]


def test_머리글이_바뀌면_파싱_실패다():
    """★ 전부 격리가 아니라 실패다 — 기존 데이터를 지우지 않기 위해서다."""
    with pytest.raises(council_sheet.ParseError):
        council_sheet.parse("이름,내용\na,b\n", today=TODAY)


def test_칸_순서를_바꿔도_이름으로_찾는다():
    csv_text = ("작성국,제목,게시일,마감일,내용,인스타링크\n"
                "문화국,행사,2026-08-14,2026-08-20,본문,https://x\n")
    parsed = council_sheet.parse(csv_text, today=TODAY)
    assert parsed.rows[0]["title"] == "행사"
    assert parsed.rows[0]["deadline"] == "2026-08-20"


def test_연도를_안_쓰면_올해로_본다():
    assert council_sheet.parse_date("9/1", today=TODAY) == "2026-09-01"
    assert council_sheet.parse_date("2025.3.4", today=TODAY) == "2025-03-04"
    assert council_sheet.parse_date("9월 초", today=TODAY) is None
    assert council_sheet.parse_date("", today=TODAY) is None


def test_시트에서_지운_글은_사라진다(tmp_path):
    """★ 총학이 잘못 올린 글을 지웠는데 우리가 계속 내보내면
    **지울 방법이 없는 챗봇**이 된다."""
    db = _db(tmp_path)
    c = repo.connect(db)
    try:
        left = council_sheet.parse(
            "게시일,제목,내용,인스타링크,마감일,작성국\n"
            "2026-08-14,사무실 이전 안내,본문,https://x,,사무국\n", today=TODAY)
        repo.upsert_council_posts(c, left.rows, source_id="s",
                                  source_url="https://x",
                                  observed_at="2026-08-14T10:00:00+09:00")
        c.commit()
        titles = [r[0] for r in c.execute("SELECT title FROM council_post")]
    finally:
        c.close()
    assert titles == ["사무실 이전 안내"]


def test_설정이_없으면_고장이_아니라_건너뜀이다(tmp_path, monkeypatch):
    """★ 아직 안 켠 기능을 '고장' 으로 세면 진짜 고장이 묻힌다."""
    from crawler import council_run
    monkeypatch.delenv(council_run.URL_ENV, raising=False)
    out = council_run.run(tmp_path / "x.db", now=NOW)
    assert out["ok"] is False and "skipped" in out
    assert "error" not in out


# ═══════════════════════════════════════════════════════════════
# 배포 순서 — 새 표가 없는 DB 를 첫 학생이 발견하면 안 된다
# ═══════════════════════════════════════════════════════════════

def test_새_표가_없어도_500이_안_난다(tmp_path):
    """★ 실제로 터질 뻔했다 (2026-08-14).

    council_post 를 추가하고 배포하면 서버 DB 에는 그 표가 없다.
    수집이 한 번 돌아야 생기는데, 그 전에 학생이 검색하면
    `no such table` 로 500 이 난다.
    스키마는 코드와 함께 배포되지만 **DB 는 디스크에 남아 있다.**
    """
    import sqlite3
    p = tmp_path / "old.db"
    c = repo.connect(p)
    repo.init_db(c)
    c.execute("DROP TABLE council_post")      # 새 표가 없던 시절의 DB
    c.commit()
    c.close()

    ro = repo.connect(p, readonly=True)
    try:
        assert repo.search_council_titles(ro, ["댄스제"], today="2026-08-14") == []
        assert repo.query_council_posts(ro, today="2026-08-14") == []
    finally:
        ro.close()

    # 서버 경로도 500 이 아니라 정상 응답이어야 한다
    r = server.handle(p, None, _pay("휴학"), now=NOW)
    assert kakao.validate(r) == []


def test_기동하면_있는_DB_의_표를_맞춘다(tmp_path, monkeypatch):
    """있는 DB 와 새 코드의 어긋남을 기동 때 메운다."""
    from fastapi.testclient import TestClient
    p = tmp_path / "mig.db"
    c = repo.connect(p)
    repo.init_db(c)
    c.execute("DROP TABLE council_post")
    c.commit()
    c.close()

    monkeypatch.setenv("SKILL_TOKEN", "x" * 24)
    with TestClient(server.create_app(p, with_scheduler=False)):
        pass
    c2 = repo.connect(p, readonly=True)
    try:
        names = [r[0] for r in c2.execute(
            "SELECT name FROM sqlite_master WHERE name='council_post'")]
    finally:
        c2.close()
    assert names == ["council_post"]


def test_없는_DB_를_만들지는_않는다(tmp_path):
    """★ 빈 DB 를 만들면 warm 이 True 가 되어 계기판이 무뎌진다.

    '떴다' 와 '답할 준비가 됐다' 는 다른 말이다.
    경로가 틀린 건 설정 문제고, 워밍업이 warm=False 로 말하게 둔다.
    """
    from fastapi.testclient import TestClient
    missing = tmp_path / "없는파일.db"
    with TestClient(server.create_app(missing, with_scheduler=False)) as cl:
        assert cl.get("/health").json()["warm"] is False
    # ★ 파일이 생기는지는 안 잰다 — repo.connect(readonly=True) 가 파일이 없으면
    #   쓰기 연결로 물러서기 때문이고, 그건 우리가 만든 동작이 아니다.
    #   여기서 지키려는 건 **표를 만들어 warm 을 True 로 만들지 않는 것**이다.
    c = repo.connect(missing, readonly=True)
    try:
        names = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE name='council_post'")]
    finally:
        c.close()
    assert names == [], "없는 DB 에 표를 만들면 계기판이 무뎌진다"


# ═══════════════════════════════════════════════════════════════
# ★ 총학을 물었으면 학교 것으로 채우지 않는다
#   배포본 실측: '총학생회 공지' → 국제협력과 비자 안내가 나왔다.
#   학식에서 '자료 없음' 을 '휴무' 로 말한 것과 같은 종류다.
# ═══════════════════════════════════════════════════════════════

def test_총학과_공지가_떨어져_있어도_잡는다():
    """★ 별칭은 **붙어 있는 말**만 잡는다.

    '총학 장학금 공지' 는 '총학 공지' 를 품고 있지 않아서 '장학금' 이 이겼고,
    학교 장학금 안내가 나갔다. 학생은 그걸 총학이 낸 것으로 읽는다.
    """
    def route(u):
        return server.route_of({"userRequest": {"utterance": u},
                                "action": {"params": {}}})[0]

    for u in ("총학생회 공지", "총학 장학금 공지", "총학생회 등록금 공지",
              "학생회 행사", "총학 소식"):
        assert route(u) == "council.notice", u
    # 학교 공지는 그대로 간다
    assert route("수강신청 공지") == "notice.search"
    assert route("모집 공고") == "notice.search"


def test_총학_질문에_학교_공지를_섞지_않는다(tmp_path):
    """시트에 없으면 **없다고 말한다.** 학교 공지로 대체하지 않는다."""
    db = _db(tmp_path)
    t = _text(server.handle(db, None, _pay("총학 장학금 공지"), now=NOW))
    assert "못 찾았어요" in t
    assert "총학 인스타" in t
    # 다른 총학 공지로 채우지도 않는다
    assert "댄스제" not in t and "사무실 이전" not in t


def test_구체적으로_물었으면_최근_글로_채우지_않는다(tmp_path):
    """★ '장학금 공지' 에 '댄스제 모집' 을 보여주면
    총학이 장학금 공지를 낸 것처럼 읽힌다."""
    db = _db(tmp_path)
    t = _text(server.handle(db, None, _pay("총학 등록금 공지"), now=NOW))
    assert "댄스제" not in t


def test_못_가져온_것과_그건_없는_것을_가른다(tmp_path):
    """둘은 다른 사실이다. 같은 문장으로 내면 학생이 어디를 볼지 못 정한다."""
    empty = tmp_path / "e.db"
    c = repo.connect(empty)
    repo.init_db(c)
    c.commit()
    c.close()
    # 시트가 비었다 → 우리 사정
    assert "못 가져왔어요" in _text(
        server.handle(empty, None, _pay("총학 장학금 공지"), now=NOW))
    # 시트는 읽었는데 그 글이 없다 → 총학이 아직 안 올렸다
    db = _db(tmp_path)
    assert "못 찾았어요" in _text(
        server.handle(db, None, _pay("총학 장학금 공지"), now=NOW))


def test_못_찾았을_때도_전체를_볼_길을_연다(tmp_path):
    db = _db(tmp_path)
    r = server.handle(db, None, _pay("총학 장학금 공지"), now=NOW)
    labels = [q["label"] for q in r["template"]["quickReplies"]]
    assert "총학 공지 전체" in labels


# ═══════════════════════════════════════════════════════════════
# 분류 — 사람이 적은 것만 믿는다
# ═══════════════════════════════════════════════════════════════

CSV_CAT = """게시일,분류,제목,내용,인스타링크,마감일,작성국
2026-08-13,취업·비교과,총학 주관 이력서 특강,본문,https://x/a,2026-08-27,복지국
2026-08-12,교내행사,학문체 댄스제,본문,https://x/b,2026-08-25,문화국
2026-08-11,,분류를 안 적은 글,취업 특강 관련 내용입니다,https://x/c,,사무국
2026-08-10,"교내행사, 취업·비교과",진로 박람회,본문,https://x/d,2026-08-30,홍보국
"""


def _db_cat(tmp_path):
    p = tmp_path / "cat.db"
    c = repo.connect(p)
    repo.init_db(c)
    c.execute("""INSERT INTO source_snapshot (id, source_key, url, fetched_at,
                 http_status, content_hash, content_path, media_type)
                 VALUES ('s','council_sheet','https://x',
                         '2026-08-14T09:00:00+09:00',200,'h','','html')""")
    parsed = council_sheet.parse(CSV_CAT, today=TODAY)
    repo.upsert_council_posts(c, parsed.rows, source_id="s",
                              source_url="https://x",
                              observed_at="2026-08-14T09:00:00+09:00")
    c.commit()
    return p, c


def test_분류는_사람이_적은_것만_믿는다(tmp_path):
    """★ 제목·본문으로 추측하지 않는다.

    추측이 틀린 전례가 둘 있다 —
      laws.jbnu.ac.kr   학칙인 줄 알았는데 법무대학원이었다
      교내공지          총학 게시물이 0건이었다
    '분류를 안 적은 글' 은 본문에 '취업 특강' 이 있어도 **안 나온다.**
    총학이 안 적은 것을 우리가 정하면 T4 의 신뢰가 무너진다.
    """
    _p, c = _db_cat(tmp_path)
    try:
        got = repo.council_by_category(c, "취업·비교과", today="2026-08-14")
        titles = [r["title"] for r in got]
    finally:
        c.close()
    assert "총학 주관 이력서 특강" in titles
    assert "분류를 안 적은 글" not in titles, "본문으로 추측하면 안 된다"
    assert "학문체 댄스제" not in titles


def test_분류는_쉼표로_여러_개(tmp_path):
    _p, c = _db_cat(tmp_path)
    try:
        career = [r["title"] for r in
                  repo.council_by_category(c, "취업·비교과", today="2026-08-14")]
        event = [r["title"] for r in
                 repo.council_by_category(c, "교내행사", today="2026-08-14")]
    finally:
        c.close()
    assert "진로 박람회" in career and "진로 박람회" in event


def test_분류_조회도_마감을_지킨다(tmp_path):
    _p, c = _db_cat(tmp_path)
    try:
        got = repo.council_by_category(c, "취업·비교과", today="2026-09-10")
    finally:
        c.close()
    assert got == []


def test_분류_칸이_없어도_파싱은_된다():
    """총학이 아직 칸을 안 넣었을 수도 있다 — 그때도 나머지는 읽는다."""
    parsed = council_sheet.parse(CSV, today=TODAY)
    assert parsed.rows and parsed.rows[0]["categories"] == ""


# ═══════════════════════════════════════════════════════════════
# 취업·비교과 — 못 세는 것을 못 센다고 말한다
# ═══════════════════════════════════════════════════════════════

def test_지금_신청_가능이라고_부르지_않는다():
    """★ 우리는 학교 공지의 마감을 모른다.

    notice_item 에는 게시일만 있고, 마감은 제목·본문에 글로 적혀 있는데
    우리는 본문을 안 읽는다. 게시일 30일을 '신청 가능' 이라고 부르면
    모르는 걸 아는 척하는 것이다.
    """
    r = templates.render_career(
        [{"title": "취업 특강 안내", "url": "https://x",
          "published_at": "2026-08-13", "board_name": "교내공지"}], [])
    t = _text(r)
    assert "신청 가능" not in t and "신청할 수 있" not in t
    assert "최근 30일 안에 올라온 취업 관련 공지예요." in t
    assert "마감은 각 공지에서 확인해 주세요." in t


def test_마감을_아는_건_총학_시트뿐이다():
    """아는 것과 모르는 것을 한 줄에 섞지 않는다."""
    r = templates.render_career(
        [{"title": "학교 공지", "url": "https://x",
          "published_at": "2026-08-13", "board_name": "교내공지"}],
        [{"title": "총학 특강", "link": "https://y", "deadline": "2026-08-27",
          "published_at": "2026-08-13"}])
    items = [o["listCard"]["items"] for o in r["template"]["outputs"]
             if "listCard" in o][0]
    by = {i["title"]: i["description"] for i in items}
    assert "마감" in by["총학 특강"]
    assert "마감" not in by["학교 공지"], "모르는 마감을 적으면 안 된다"


def test_총학_시트가_먼저_나온다():
    """T4 가 크롤보다 무겁다 — 목록에서도 그렇다."""
    r = templates.render_career(
        [{"title": "학교 공지", "url": "https://x",
          "published_at": "2026-08-13", "board_name": "교내공지"}],
        [{"title": "총학 특강", "link": "https://y", "deadline": None,
          "published_at": "2026-08-13"}])
    items = [o["listCard"]["items"] for o in r["template"]["outputs"]
             if "listCard" in o][0]
    assert items[0]["title"] == "총학 특강"


def test_하나도_없으면_인스타로_보낸다():
    r = templates.render_career([], [])
    assert kakao.validate(r) == []
    assert "못 찾았어요" in _text(r)


# ═══════════════════════════════════════════════════════════════
# 들어오는 문이 둘이면 둘 다 같은 답을 해야 한다
# ═══════════════════════════════════════════════════════════════

def test_job_원천도_crawler_run_으로_돈다(tmp_path, monkeypatch):
    """★ "파서 미구현 — 건너뜀" 이 나가고 있었다 (2026-08-14).

    council_sheet 는 parser 가 아니라 job 으로 돈다.
    스케줄러는 그 갈래를 알지만 CLI 는 몰랐다.
    **파서는 있는데 명령이 거짓말을 했고**, 그 말을 본 사람은
    '아직 안 만들었구나' 로 읽는다 — 실제로 그랬다.
    """
    from crawler import run as run_mod
    called = {}

    def fake_job(db_path, cfg, now):
        called["yes"] = (db_path, cfg.get("job"))
        return {"ok": True, "parsed": 2}

    from crawler import jobs as jobs_mod
    monkeypatch.setitem(jobs_mod.JOBS, "council", fake_job)
    run_mod.run_source(None, "council_sheet", {"job": "council"},
                       date=None, dry_run=False, force=False)
    assert called.get("yes"), "job 원천이 그냥 건너뛰어졌다"


def test_모르는_작업은_조용히_넘기지_않는다(tmp_path, capsys):
    from crawler import run as run_mod
    run_mod.run_source(None, "x", {"job": "없는작업"},
                       date=None, dry_run=False, force=False)
    assert "모르는 작업" in capsys.readouterr().out


# ═══════════════════════════════════════════════════════════════
# 갈래 셋 — 못 가져왔다 / 진행 중 없음 / 그건 못 찾았다
# ═══════════════════════════════════════════════════════════════

def test_가져왔는데_다_지났으면_못_가져왔다고_안_한다(tmp_path):
    """★ '못 가져왔다' 는 틀린 말이다 — 가져왔다.

    우리 사정(못 읽음)과 학교 사정(지금은 없음)을 섞으면
    학생이 어디를 봐야 할지 못 정한다.
    """
    db = _db(tmp_path)
    c = repo.connect(db)
    c.execute("""UPDATE crawl_run SET started_at=?, finished_at=?
                  WHERE source_key='council_sheet'""",
              ("2026-09-10T08:00:00+09:00", "2026-09-10T08:00:05+09:00"))
    c.commit()
    c.close()
    now = dt.datetime.fromisoformat("2026-09-10T12:00:00+09:00")
    # 마감 없는 '사무실 이전' 이 남아 있으므로 그건 지운다
    c = repo.connect(db)
    c.execute("DELETE FROM council_post WHERE deadline IS NULL")
    c.commit()
    c.close()
    t = _text(server.handle(db, None, _pay("총학 공지"), now=now))
    assert "지금 진행 중인" in t
    assert "못 가져왔어요" not in t


def test_교내행사는_분류로_뽑는다(tmp_path):
    """★ 학교 공지를 섞지 않는다 — 낱말 목록을 지어내면 그게 추측이다."""
    p, c = _db_cat(tmp_path)
    c.execute("""INSERT INTO crawl_run (id, source_key, started_at,
                 finished_at, outcome)
                 VALUES ('r','council_sheet','2026-08-14T09:00:00+09:00',
                         '2026-08-14T09:00:05+09:00','success')""")
    c.commit()
    c.close()
    t = _text(server.handle(p, None, _pay("교내 행사"), now=NOW))
    assert "학문체 댄스제" in t
    assert "총학 주관 이력서 특강" not in t, "다른 분류가 섞였다"
    assert "분류를 안 적은 글" not in t


def test_교내행사_별칭이_붙는다():
    def route(u):
        return server.route_of({"userRequest": {"utterance": u},
                                "action": {"params": {}}})[0]
    for u in ("교내 행사", "교내행사", "학교 행사", "무슨 행사"):
        assert route(u) == "council.event", u
