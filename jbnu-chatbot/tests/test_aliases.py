"""발화 기반 슬롯 보완 + 식당 미지정 응답.

★ 이건 '자체 NLU'가 아니다. 인텐트 분류는 오픈빌더가 하고,
  우리는 **이미 매칭된 블록 안에서** 슬롯만 채운다.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from crawler import fetch as fetch_mod
from crawler import ingest as ingest_mod
from crawler.parsers import coop_week_menu as coop
from skill import aliases, kakao, server
from store import repo

FIX = pathlib.Path(__file__).parent / "fixtures"
WEEK_JSON = (FIX / "coop_week_20260810.json").read_text(encoding="utf-8")
SRC = "https://coopjbnu.kr/menu/week_menu.php"
KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime.fromisoformat("2026-08-10T12:00:00+09:00")

HUSAENG = "jbnu:facility/후생관-푸드코트"
JINSU = "jbnu:facility/진수원"
UIDAE = "jbnu:facility/의대식당"


# ═══════════════════════════════════════════════════════════════
# 별칭 매칭
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("utterance,expected", [
    ("오늘 후생관 메뉴 뭐야", HUSAENG),
    ("후생 밥", HUSAENG),
    ("푸드코트 점심", HUSAENG),
    ("공대식당 뭐 나와", HUSAENG),
    ("진수원 저녁", JINSU),
    ("진수당 점심", JINSU),           # 문서 오기지만 학생이 그렇게 부를 수 있다
    ("의대 점심", UIDAE),
    ("의대식당 메뉴", UIDAE),
    ("기숙사 아침", "jbnu:facility/생활관-식당"),
])
def test_발화에서_식당을_찾는다(utterance, expected):
    assert aliases.find_facility(utterance) == expected


def test_긴_별칭이_먼저_걸린다():
    """'의대식당'이 '의대'보다 먼저여야 한다.

    짧은 걸 먼저 보면 '의대식당 메뉴'에서 '의대'만 잡고 끝난다.
    지금은 같은 시설이라 결과가 같지만, 다른 시설이면 오답이 된다.
    """
    pairs = aliases.facility_pairs()
    lens = [len(a) for a, _ in pairs]
    assert lens == sorted(lens, reverse=True)
    i_long = next(i for i, (a, _) in enumerate(pairs) if a == "의대식당")
    i_short = next(i for i, (a, _) in enumerate(pairs) if a == "의대")
    assert i_long < i_short


def test_식당이_없으면_None():
    """억지로 고르지 않는다."""
    assert aliases.find_facility("오늘 학식 뭐야") is None
    assert aliases.find_facility("") is None


def test_공백을_무시한다():
    assert aliases.find_facility("후 생 관 메뉴") == HUSAENG


@pytest.mark.parametrize("utterance,expected", [
    ("후생관 아침", "breakfast"), ("조식 뭐야", "breakfast"),
    ("점심 메뉴", "lunch"), ("중식", "lunch"),
    ("저녁 뭐 나와", "dinner"), ("석식", "dinner"),
])
def test_발화에서_끼니를_찾는다(utterance, expected):
    assert aliases.find_meal_type(utterance) == expected


# ═══════════════════════════════════════════════════════════════
# params 우선, 없으면 utterance
# ═══════════════════════════════════════════════════════════════

def test_params가_있으면_그걸_쓴다():
    fid, src = aliases.resolve_facility({"outlet": "진수원"}, "후생관 메뉴")
    assert fid == JINSU and src == "params:alias"


def test_params가_정규ID여도_받는다():
    fid, src = aliases.resolve_facility({"outlet": HUSAENG}, "")
    assert fid == HUSAENG and src == "params:id"


def test_params가_비면_utterance로_보완한다():
    """★ 오픈빌더는 태깅된 발화만 params 를 준다. 자유 발화는 계속 빈다."""
    fid, src = aliases.resolve_facility({}, "오늘 후생관 뭐 나와")
    assert fid == HUSAENG and src == "utterance"


def test_둘_다_없으면_None():
    fid, src = aliases.resolve_facility({}, "오늘 학식")
    assert fid is None and src == "none"


# ═══════════════════════════════════════════════════════════════
# 서버 통합
# ═══════════════════════════════════════════════════════════════

@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "a.db"
    c = repo.connect(path)
    repo.init_db(c)
    for fid in aliases.all_facility_ids():
        c.execute("""INSERT OR IGNORE INTO facility
                       (id, name, facility_type, source_url, source_type)
                     VALUES (?,?,?,?,'coop')""",
                  (fid, aliases.canonical_name(fid), "식당", SRC))
    c.commit()
    res = fetch_mod.make_result("coop_week_menu", SRC, SRC, 200,
                                WEEK_JSON.encode("utf-8"),
                                "2026-08-10T06:00:00+09:00", "json")
    ingest_mod.ingest(c, res, parser=coop.parse, snapshot_dir=tmp_path,
                      extraction_method="json_api")
    c.close()
    return path


def _payload(utterance: str, **params):
    return {"userRequest": {"utterance": utterance, "user": {"id": "u"}},
            "action": {"params": params, "detailParams": {}}}


def test_params가_비어도_발화로_답한다(db):
    """★ 이게 지금 폴백이 나오는 원인이었다. 봇테스트에 '파라미터: -' 로 뜬다."""
    r = server.handle(db, "food.menu.today",
                      _payload("오늘 후생관 점심 뭐야"), now=NOW)
    assert kakao.validate(r) == []
    card = r["template"]["outputs"][0]["listCard"]
    assert "후생관" in card["header"]["title"]
    assert "준비되지 않았어요" not in str(r)


def test_식당도_끼니도_안_말하면_되묻는다(db):
    """'오늘 학식' — 폴백이 아니라 **어느 식당인지 되묻는다**.

    자료는 있고 어느 식당인지만 모르는 상태다. '모른다'와 '안 물었다'는 다르다.

    ★ 전에는 전체 목록을 보여줬는데, 그러려면 끼니를 하나 골라야 했다.
      시각으로 골랐고 — 새벽에 물으면 조식만 나갔다. 그날 점심에는
      세 식당 다 메뉴가 있었는데도 그랬다. 고르는 것 자체가 추측이었다.
    """
    r = server.handle(db, "food.menu.today", _payload("오늘 학식 뭐야"), now=NOW)
    assert kakao.validate(r) == []
    assert "listCard" not in r["template"]["outputs"][0]
    labels = [q["label"] for q in r["template"]["quickReplies"]]
    assert "후생관" in labels and "진수원" in labels
    assert "준비되지 않았어요" not in str(r)


def test_끼니만_말하면_전체_목록(db):
    """'점심' 이라고 밝혔으면 그 끼니로 전체 식당을 보여준다 — 추측이 아니다."""
    r = server.handle(db, "food.menu.today", _payload("점심 학식"), now=NOW)
    assert kakao.validate(r) == []
    titles = [i["title"] for i in r["template"]["outputs"][0]["listCard"]["items"]]
    assert "후생관" in titles and "진수원" in titles


def test_식당만_말하면_세_끼니_전부(db):
    """★ 학생이 '오늘' 을 물었으면 조·중·석이 다 나가야 한다."""
    r = server.handle(db, "food.menu.today", _payload("후생관 학식"), now=NOW)
    assert kakao.validate(r) == []
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    assert "[아침]" in text and "[점심]" in text and "[저녁]" in text


def test_발화에서_끼니도_보완된다(db):
    r = server.handle(db, "food.menu.today", _payload("후생관 아침"), now=NOW)
    text = str(r)
    assert "아침" in text


def test_안전분기는_여전히_먼저다(db):
    """슬롯 보완을 넣어도 순서가 바뀌면 안 된다."""
    r = server.handle(db, "food.menu.today",
                      _payload("후생관 가다가 죽고싶어"), now=NOW)
    assert "109" in r["template"]["outputs"][0]["simpleText"]["text"]
    assert "listCard" not in r["template"]["outputs"][0]


# ═══════════════════════════════════════════════════════════════
# 오픈빌더 내보내기
# ═══════════════════════════════════════════════════════════════

def test_CSV_내보내기가_대표어_먼저(capsys):
    from tools import export_aliases
    export_aliases.main(["--csv", "--group", "outlet"])
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert lines
    for line in lines:
        cells = line.split(",")
        assert cells[0] not in cells[1:], "대표어가 동의어에 중복되면 안 된다"
    assert any(l.startswith("후생관,") for l in lines)
