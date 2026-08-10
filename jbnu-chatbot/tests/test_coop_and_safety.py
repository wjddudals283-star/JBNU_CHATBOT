"""생협 파서 + 교차검증 (T18) + 안전 분기 (T13)."""

from __future__ import annotations

import datetime as dt
import json
import pathlib

import pytest

from crawler import crossvalidate
from crawler import fetch as fetch_mod
from crawler import ingest as ingest_mod
from crawler.parsers import coop_week_menu as coop
from crawler.parsers import jbnu_cafeteria_day as jbnu
from crawler.validate import AnchorMismatch, ParseError
from skill import safety
from store import repo

FIX = pathlib.Path(__file__).parent / "fixtures"
WEEK_JSON = (FIX / "coop_week_20260810.json").read_text(encoding="utf-8")
DAY_HTML = (FIX / "jbnu_dataAjax_day.html").read_text(encoding="utf-8", errors="replace")

HUSAENG = "jbnu:facility/후생관-푸드코트"
NOW = dt.datetime.fromisoformat("2026-08-10T12:00:00+09:00")


# ═══════════════════════════════════════════════════════════════
# 생협 파서
# ═══════════════════════════════════════════════════════════════

def test_주간_15건이_한_응답에서_나온다():
    r = coop.parse(WEEK_JSON)
    dates = {m.date for m in r.meals} | {m.date for m, _ in r.quarantined}
    assert dates == {f"2026-08-{d}" for d in ("10", "11", "12", "13", "14")}
    assert {m.facility_id for m in r.meals} <= set(coop.FACILITY_BY_REST.values())


def test_운영없음은_품목이_아니라_closed_temporary():
    r = coop.parse(WEEK_JSON)
    names = {i.name for m in r.meals for i in m.items}
    assert "운영없음" not in names
    closed = [m for m in r.meals if m.service_status == "closed_temporary"]
    assert closed and all(m.items == [] for m in closed)
    assert any(m.note == "운영없음" for m in closed)


def test_빈_diet는_unknown이지_미운영이_아니다():
    """의대식당 조식은 원천이 빈 문자열을 준다. 방학이라고 단정하지 않는다."""
    r = coop.parse(WEEK_JSON)
    unknown = [m for m in r.meals if m.service_status == "unknown"]
    assert unknown, "빈 diet 가 unknown 으로 잡혀야 한다"
    assert all(m.items == [] for m in unknown)


def test_list가_비면_레코드_0건이고_parse_error가_아니다():
    """토·일이거나 아직 안 올라온 주. '운영 안 함'이 아니다."""
    r = coop.parse('{"status":"success","list":[]}')
    assert r.meals == [] and r.empty_list is True


def test_status가_success가_아니면_ParseError():
    with pytest.raises(ParseError):
        coop.parse('{"status":"fail","list":[]}')


def test_list키가_없으면_ParseError():
    """스키마가 바뀐 것이다. 조용히 0건으로 넘기면 안 된다."""
    with pytest.raises(ParseError):
        coop.parse('{"status":"success"}')


def test_day필드와_날짜가_어긋나면_잡힌다():
    """같은 정보가 두 번 들어 있으면 대조를 건다."""
    data = json.loads(WEEK_JSON)
    data["list"][0]["day"] = 3        # 실제 2026-08-10 은 월요일(0)
    with pytest.raises(AnchorMismatch):
        coop.parse(data)


def test_모르는_식당표기는_ParseError():
    data = json.loads(WEEK_JSON)
    data["list"][0]["restNm"] = "신설식당"
    with pytest.raises(ParseError) as e:
        coop.parse(data)
    assert "별칭" in str(e.value)


def test_raw_text가_보존된다():
    """분할 규칙을 바꿔도 재크롤 없이 다시 쪼갤 수 있어야 한다."""
    r = coop.parse(WEEK_JSON)
    with_items = [m for m in r.meals if m.items]
    assert with_items and all(m.raw_text for m in with_items)


def test_구분자를_놓치면_검증에서_걸린다():
    """품목 1건인데 내부 공백이 여러 개 = 구분자 놓침 신호."""
    data = json.loads(WEEK_JSON)
    for item in data["list"]:
        for sub in item.get("subData") or []:
            if sub.get("diet", "").strip():
                sub["diet"] = "흰밥 미역국 제육볶음 포기김치"
                break
        break
    r = coop.parse(data)
    assert any("구분자를 놓쳤을" in why for _, why in r.quarantined)


# ═══════════════════════════════════════════════════════════════
# T18 — 교차 검증
# ═══════════════════════════════════════════════════════════════

def test_T18_둘_다_관측이고_다르면_1차_채택():
    primary = coop.parse(WEEK_JSON).meals
    secondary = jbnu.parse(DAY_HTML).meals

    # 둘 다 관측(operating)인 칸 하나를 일부러 다르게 만든다
    pk = {(m.facility_id, m.date, m.meal_type, m.zone, m.corner) for m in primary
          if m.service_status == "operating"}
    target = next(m for m in secondary
                  if (m.facility_id, m.date, m.meal_type, m.zone, m.corner) in pk
                  and m.service_status == "operating")
    target.items = [repo.ParsedItem("가짜메뉴", display_order=0)]

    merged, rep = crossvalidate.merge(primary, secondary)
    assert rep.compared > 0 and rep.conflicts

    key = (target.facility_id, target.date, target.meal_type, target.zone, target.corner)
    kept, origin = next((m, o) for m, o in merged
                        if (m.facility_id, m.date, m.meal_type, m.zone, m.corner) == key)
    assert origin == crossvalidate.PRIMARY
    assert "가짜메뉴" not in [i.name for i in kept.items]


def test_T18b_1차에_없는_칸은_2차를_채택한다():
    primary = coop.parse(WEEK_JSON).meals
    secondary = jbnu.parse(DAY_HTML).meals
    merged, rep = crossvalidate.merge(primary, secondary)
    assert len(merged) == len(primary) + rep.adopted_secondary
    assert rep.adopted_secondary == rep.secondary_only


def test_T18c_지표가_둘로_나뉜다():
    """경보는 content_conflict_rate 에만. coverage_gap 은 추세만 본다."""
    primary = coop.parse(WEEK_JSON).meals
    secondary = jbnu.parse(DAY_HTML).meals
    rep = crossvalidate.compare(primary, secondary)
    assert rep.compared > 0
    assert rep.content_conflict_rate == 0.0, "실측 기준선"
    assert 0.0 < rep.coverage_gap_rate < 1.0


def test_교차검증은_값을_합성하지_않는다():
    """둘을 섞어 '더 그럴듯한' 메뉴를 만들면 그게 추론이다.

    채택은 **레코드 통째로** 한다. 필드 단위로 좋은 것만 골라 붙이지 않는다.
    """
    primary = coop.parse(WEEK_JSON).meals
    secondary = jbnu.parse(DAY_HTML).meals
    originals = {id(m) for m in primary} | {id(m) for m in secondary}
    merged, _ = crossvalidate.merge(primary, secondary)
    assert all(id(m) in originals for m, _ in merged), \
        "합성된 새 객체가 있으면 안 된다"


# ═══════════════════════════════════════════════════════════════
# T13 — 안전 분기
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("utterance,category", [
    ("죽고싶어요", "emergency"),
    ("자살하고 싶어", "emergency"),
    ("성폭력 당했어요", "violence"),
    ("성 폭 력", "violence"),                      # 공백 우회
    ("남자친구가 스토킹해요", "violence"),
    ("선배가 계속 괴롭혀요", "harassment"),
    ("요즘 너무 우울해", "mental_health"),
])
def test_T13_민감_발화는_즉시_안전분기(utterance, category):
    m = safety.check(utterance)
    assert m is not None and m.category == category


@pytest.mark.parametrize("utterance", [
    "오늘 후생관 메뉴 뭐야",
    "열람실 몇 시까지 해",
    "휴학 어떻게 해",
    "공약 진행상황 알려줘",
])
def test_T13b_일반_발화는_안전분기가_아니다(utterance):
    assert safety.check(utterance) is None
    assert not safety.is_sensitive(utterance)


def test_T13c_안전응답은_대화를_이어가지_않는다():
    """추천질문을 달면 계속 얘기하자는 신호가 된다. 사람에게 넘기고 끝낸다."""
    r = safety.response("죽고싶어요")
    assert r["version"] == "2.0"
    assert "quickReplies" not in r["template"]
    text = r["template"]["outputs"][0]["simpleText"]["text"]
    # 총학 확인 완료(2026-08-10, official_site) → 상담 창구가 나간다
    assert "109" in text and "112" in text


def test_T13d_위험도가_높은_범주가_먼저_걸린다():
    """'우울한데 죽고싶어' 는 mental_health 가 아니라 emergency 다."""
    m = safety.check("우울한데 죽고싶어요")
    assert m.category == "emergency"


def test_T13e_안전분기는_인텐트분류보다_먼저다():
    """식단 키워드가 섞여 있어도 안전 분기가 이긴다."""
    m = safety.check("밥도 안 넘어가고 죽고싶어")
    assert m is not None and m.category == "emergency"


def test_연락처는_코드가_아니라_설정에서_온다(tmp_path):
    p = tmp_path / "safety.yaml"
    p.write_text(
        "priority: [emergency]\nfooter: ''\n"
        "categories:\n  emergency:\n    keywords: [테스트키워드]\n"
        "    lead: 안내\n    contacts:\n"
        "      - {label: 테스트, phone: '000', verified: true,\n"
        "         verified_at: '2026-08-10', verified_by: '총학생회',\n"
        "         verified_method: official_site}\n",
        encoding="utf-8")
    cfg = safety.load(p)
    m = cfg.match("테스트키워드요")
    assert m and "000" in cfg.response_text(m)
