"""오늘 학식 — 끼니를 안 밝히면 세 끼니 전부, 근거는 갈라서 말한다.

★ 실사용에서 세 가지가 한꺼번에 드러났다 (2026-08-14)
  ① 학생은 '오늘' 을 물었는데 '아침' 만 나갔다. 시각으로 끼니를 하나 골랐다.
     그날 점심에는 세 식당 다 메뉴가 있었다 — **있는 답을 안 보여줬다.**
  ② '운영 안 해요' 가 근거 없이 나갔다. 원천을 전수로 다시 읽어 보니
     두 가지가 섞여 있었다.
        후생관 조식·석식   diet="운영없음"   → 원천이 적어 놓았다. 관측된 휴무다.
        진수원·의대 조식   칸이 비어 있다     → 안 올라온 것이다. 휴무가 아니다.
     한 문장으로 뭉개면 없는 사실을 만들어낸다.
  ③ 식당을 안 밝히면 되물어야 한다.

★ 시각으로 고르지 않는다
  '지금 몇 시인가' 는 학생이 무엇을 궁금해하는지에 대한 근거가 아니다.
  고르는 순간 그건 우리가 의도를 추측한 것이다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from skill import kakao, server
from store import repo

KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime.fromisoformat("2026-08-14T06:30:00+09:00")   # ★ 새벽 — 전에는 조식만 나갔다
DATE = "2026-08-14"
SRC = "https://coopjbnu.kr/menu/week_menu.php"
FID = "jbnu:facility/후생관-푸드코트"
FID2 = "jbnu:facility/진수원"


def _service(c, fid: str, meal: str, status: str, items: list[str], *,
             corner: str = "코너1") -> None:
    sid = f"{fid}|{DATE}|{meal}|{corner}"
    c.execute("""INSERT INTO meal_service
                   (id, facility_id, date, meal_type, service_status, zone, corner,
                    raw_text, source_id, source_url, observed_at, valid_from,
                    confidence, extraction_method, tier)
                 VALUES (?,?,?,?,?,'',?,?,'snap',?,?,?,1.0,'json_api','T1')""",
              (sid, fid, DATE, meal, status, corner, " / ".join(items),
               SRC, "2026-08-14T05:00:00+09:00", DATE))
    for i, name in enumerate(items):
        c.execute("""INSERT INTO menu_item
                       (id, meal_service_id, name, name_normalized, display_order,
                        source_id, source_url, observed_at, confidence,
                        extraction_method, tier)
                     VALUES (?,?,?,?,?,'snap',?,?,1.0,'json_api','T1')""",
                  (f"{sid}|{i}", sid, name, name, i,
                   SRC, "2026-08-14T05:00:00+09:00"))


@pytest.fixture()
def db(tmp_path):
    """2026-08-14 원천을 그대로 옮긴 것 — 두 갈래가 실제로 섞여 있던 날이다."""
    path = tmp_path / "m.db"
    c = repo.connect(path)
    repo.init_db(c)
    c.execute("""INSERT INTO source_snapshot
                   (id, source_key, url, fetched_at, http_status, content_hash,
                    content_path, media_type)
                 VALUES ('snap','coop_week_menu',?,?,200,'h','x.json','json')""",
              (SRC, "2026-08-14T05:00:00+09:00"))
    for fid, name in ((FID, "후생관"), (FID2, "진수원")):
        c.execute("""INSERT OR IGNORE INTO facility
                       (id, name, facility_type, source_url, source_type)
                     VALUES (?,?,?,?,'coop')""", (fid, name, "식당", SRC))
    # 후생관 — 조식·석식은 원천이 '운영없음' 이라고 적었다, 점심에는 메뉴가 있다
    _service(c, FID, "breakfast", "closed_temporary", [])
    _service(c, FID, "lunch", "operating", ["제육볶음", "된장국", "계란찜"])
    _service(c, FID, "dinner", "closed_temporary", [])
    # 진수원 — 조식은 칸이 비어 있었다 (행 자체가 없다)
    _service(c, FID2, "lunch", "operating", ["돈까스", "우동"])
    c.commit()
    c.close()
    return path


def _pay(utterance: str, **params) -> dict:
    return {"userRequest": {"utterance": utterance, "user": {"id": "u"}},
            "action": {"params": params, "detailParams": {}}}


def _text(r) -> str:
    return r["template"]["outputs"][0]["simpleText"]["text"]


# ═══════════════════════════════════════════════════════════════
# ① 끼니를 안 밝히면 세 끼니 전부
# ═══════════════════════════════════════════════════════════════

def test_끼니를_안_밝히면_세_끼니_전부(db):
    """★ 새벽에 물어도 점심이 나가야 한다.

    이게 실사용에서 잡힌 고장이다. 06:30 에 '오늘 후생관 학식' 을 물으면
    조식만 나갔고, 조식은 그날 운영없음이었다. 학생은 '학식이 없다' 고 읽었다.
    정작 점심에는 메뉴가 세 개 올라와 있었다.
    """
    r = server.handle(db, "food.menu.today", _pay("오늘 후생관 학식"), now=NOW)
    assert kakao.validate(r) == []
    t = _text(r)
    assert "[아침]" in t and "[점심]" in t and "[저녁]" in t
    assert "제육볶음" in t, "있는 답을 안 보여주면 안 된다"


def test_끼니를_밝히면_그것만(db):
    """밝혔으면 추측이 아니다 — 물은 것만 답한다."""
    r = server.handle(db, "food.menu.today", _pay("후생관 점심"), now=NOW)
    assert "제육볶음" in str(r)
    assert "[아침]" not in str(r)


# ═══════════════════════════════════════════════════════════════
# ② '운영 안 해요' 의 근거를 갈라 말한다
# ═══════════════════════════════════════════════════════════════

def test_원천이_운영없음이라_적은_것과_안_올라온_것을_구분한다(db):
    """★ 두 가지를 한 문장으로 뭉개면 없는 사실을 만들어낸다.

    후생관 조식은 원천에 '운영없음' 이라고 **적혀 있다** — 그렇게 말해도 된다.
    진수원 조식은 칸이 비어 있다 — 이걸 '운영 안 해요' 라고 하면 그건 우리가
    지어낸 것이다. 실제로 그렇게 나가고 있었다.
    """
    a = _text(server.handle(db, "food.menu.today", _pay("후생관 학식"), now=NOW))
    b = _text(server.handle(db, "food.menu.today", _pay("진수원 학식"), now=NOW))

    breakfast_a = next(l for l in a.split("\n") if l.startswith("[아침]"))
    breakfast_b = next(l for l in b.split("\n") if l.startswith("[아침]"))

    assert "운영없음" in breakfast_a, "원천이 적어 놓은 것은 그대로 인용한다"
    assert "운영없음" not in breakfast_b, "안 올라온 것을 휴무라고 하면 안 된다"
    assert breakfast_a != breakfast_b


def test_운영없음은_원천_표현을_인용한다(db):
    """우리가 판정한 것처럼 말하지 않는다 — 식단표가 그렇게 적었다고 말한다."""
    t = _text(server.handle(db, "food.menu.today", _pay("후생관 학식"), now=NOW))
    assert "식단표에" in t


# ═══════════════════════════════════════════════════════════════
# ③ 식당을 안 밝히면 되묻는다
# ═══════════════════════════════════════════════════════════════

def test_식당을_안_밝히면_되묻는다(db):
    """★ 커스텀 메뉴 6번 칸 — 개강 첫날 제일 많이 눌릴 자리다."""
    r = server.handle(db, "food.menu.today", _pay("학식"), now=NOW)
    assert kakao.validate(r) == []
    labels = [q["label"] for q in r["template"]["quickReplies"]]
    assert "후생관" in labels and "진수원" in labels


def test_되묻기_버튼을_누르면_세_끼니가_나온다(db):
    """★ 되묻기는 상태를 안 만든다 — 버튼이 보내는 말만으로 끝나야 한다.

    버튼이 '후생관 학식' 을 보내고, 그 한 마디로 답이 나온다.
    2턴에 다시 되묻으면 그건 고장이다.
    """
    ask = server.handle(db, "food.menu.today", _pay("학식"), now=NOW)
    sent = ask["template"]["quickReplies"][0]["messageText"]
    r = server.handle(db, None, _pay(sent), now=NOW)
    t = _text(r)
    assert "[점심]" in t, f"되묻기가 반복됐다: {t[:60]}"


def test_잘린_끼니는_갈_길을_열어준다(db, tmp_path):
    """★ 자른 사실을 표시하고 끝내면 그건 정보를 숨긴 것이다.

    후생관 점심은 코너가 11개라 실제로 32품목이 온다. 한 줄에 6개만 들어가는데
    나머지 26개를 볼 길이 없으면 학생은 그게 전부인 줄 안다.
    버튼이 보내는 말이 그대로 상세로 가야 한다 — 되묻기와 같은 규칙이다.
    """
    c = repo.connect(db)
    _service(c, FID, "lunch", "operating",
             [f"품목{i}" for i in range(9)], corner="코너2")
    c.commit()
    c.close()

    r = server.handle(db, "food.menu.today", _pay("후생관 학식"), now=NOW)
    assert "외 " in _text(r), "잘랐으면 잘랐다고 말한다"
    qr = {q["label"]: q["messageText"] for q in r["template"]["quickReplies"]}
    assert "점심 자세히" in qr

    # 그 버튼이 보내는 말 한 마디로 상세가 나와야 한다 (상태를 안 만든다)
    r2 = server.handle(db, None, _pay(qr["점심 자세히"]), now=NOW)
    assert kakao.validate(r2) == []
    assert "listCard" in r2["template"]["outputs"][0]


def test_되묻기에서_되돌아갈_수_있다(db):
    """답변에 붙는 버튼은 전부 눌러서 답이 나와야 한다 (button_probe 와 같은 규칙)."""
    r = server.handle(db, "food.menu.today", _pay("후생관 학식"), now=NOW)
    labels = [q["label"] for q in r["template"]["quickReplies"]]
    assert "다른 식당" in labels and "처음으로" in labels


# ═══════════════════════════════════════════════════════════════
# 회귀 — 안전 분기 순서
# ═══════════════════════════════════════════════════════════════

def test_안전분기가_여전히_먼저다(db):
    r = server.handle(db, "food.menu.today", _pay("학식 먹다가 죽고싶어"), now=NOW)
    assert "109" in _text(r)


# ═══════════════════════════════════════════════════════════════
# 우리가 붙인 버튼이 거짓말을 하면 안 된다
# ═══════════════════════════════════════════════════════════════

def test_발화의_내일을_읽는다():
    """★ '내일 메뉴' 버튼이 오늘 메뉴를 보여주고 있었다 (2026-08-14).

    오픈빌더가 sys.date 를 채워 주는 경로만 보고 있었는데,
    버튼은 폴백(블록 없음)으로 들어와서 params 가 비어 있다.
    끼니는 발화에서 보완하면서 날짜는 안 했다.
    """
    now = dt.datetime.fromisoformat("2026-08-14T20:00:00+09:00")
    assert server._relative_date("후생관 내일 점심", now) == "2026-08-15"
    assert server._relative_date("후생관 모레 점심", now) == "2026-08-16"
    assert server._relative_date("후생관 점심", now) is None
    # params 가 있으면 그게 이긴다 — 오픈빌더 추출이 우선이다
    assert server._resolve_date({"date": "2026-09-01"}, {}, now, "내일") == "2026-09-01"
    assert server._resolve_date({}, {}, now, "내일 뭐 나와") == "2026-08-15"


def test_내일을_보고_있으면_내일_버튼을_안_붙인다(db):
    """★ 이미 내일을 보여주면서 '내일 메뉴' 를 또 붙이면 자기 자신으로 돌아온다."""
    NEXT = dt.datetime.fromisoformat("2026-08-13T20:00:00+09:00")   # 오늘=8/13
    r = server.handle(db, "food.menu.today", _pay("후생관 내일 점심"), now=NEXT)
    msgs = [q["messageText"] for q in r["template"]["quickReplies"]]
    assert "후생관 내일 점심" not in msgs, "누르면 같은 날짜가 또 나온다"
    assert "후생관 오늘 점심" in msgs, "돌아갈 길은 열어 둔다"


def test_오늘을_보고_있으면_내일_버튼이_붙는다(db):
    r = server.handle(db, "food.menu.today", _pay("후생관 점심"), now=NOW)
    msgs = [q["messageText"] for q in r["template"]["quickReplies"]]
    assert "후생관 내일 점심" in msgs
