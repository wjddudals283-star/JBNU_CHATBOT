"""kakao.py UI 제약 + 템플릿 3분기 — T14 / T8 + 교차검증 재정의 + 안전 배포 차단."""

from __future__ import annotations

import datetime as dt
import pathlib

import pytest

from crawler import crossvalidate
from crawler import ingest as ingest_mod
from crawler import fetch as fetch_mod
from crawler.parsers import coop_week_menu as coop
from crawler.parsers import jbnu_cafeteria_day as jbnu
from skill import branch, kakao, safety, templates
from store import repo

FIX = pathlib.Path(__file__).parent / "fixtures"
WEEK_JSON = (FIX / "coop_week_20260810.json").read_text(encoding="utf-8")
DAY_HTML = (FIX / "jbnu_dataAjax_day.html").read_text(encoding="utf-8", errors="replace")

HUSAENG = "jbnu:facility/후생관-푸드코트"
SRC = "https://coopjbnu.kr/menu/week_menu.php"
NOW = dt.datetime.fromisoformat("2026-08-10T12:00:00+09:00")


# ═══════════════════════════════════════════════════════════════
# T14 — listCard 5개 제한
# ═══════════════════════════════════════════════════════════════

def test_T14_메뉴_7개는_5개_전체보기_버튼으로_렌더된다():
    items = [{"title": f"메뉴{i}", "description": "3,000원"} for i in range(7)]
    card, dropped = kakao.list_card(
        "후생관 8/10 점심", items,
        overflow_button=kakao.web_button("전체 7개 보기", SRC))
    assert len(card["listCard"]["items"]) == 5
    assert dropped == 2
    assert card["listCard"]["buttons"][0]["label"] == "전체 7개 보기"


def test_T14b_잘리는데_전체보기가_없으면_예외():
    """조용히 잘려서 답이 반쪽이 되는 게 가장 피해야 할 실패다."""
    items = [{"title": f"메뉴{i}"} for i in range(7)]
    with pytest.raises(kakao.KakaoSpecError) as e:
        kakao.list_card("헤더", items)
    assert "반쪽" in str(e.value)


def test_T14c_5개_이하면_전체보기가_안_붙는다():
    items = [{"title": f"메뉴{i}"} for i in range(3)]
    card, dropped = kakao.list_card("헤더", items,
                                    buttons=[kakao.web_button("원문 보기", SRC)])
    assert dropped == 0
    assert len(card["listCard"]["buttons"]) == 1


# ═══════════════════════════════════════════════════════════════
# T8 — 응답 규격 검증
# ═══════════════════════════════════════════════════════════════

def test_T8_빌더가_만든_응답은_규격을_통과한다():
    card, _ = kakao.list_card("헤더", [{"title": "메뉴"}])
    payload = kakao.response([card], [kakao.quick_reply("내일 메뉴")])
    assert kakao.validate(payload) == []
    assert payload["version"] == "2.0"


def test_T8b_손으로_만든_잘못된_응답은_걸린다():
    bad = {
        "version": "1.0",
        "template": {
            "outputs": [{"listCard": {
                "header": {"title": "x"},
                "items": [{"title": f"m{i}"} for i in range(7)],
            }}],
            "quickReplies": [kakao.quick_reply(f"q{i}") for i in range(12)],
        },
    }
    errs = kakao.validate(bad)
    assert any("version" in e for e in errs)
    assert any("listCard.items" in e for e in errs)
    assert any("quickReplies" in e for e in errs)


def test_T8c_빈_outputs는_예외():
    with pytest.raises(kakao.KakaoSpecError):
        kakao.response([])


def test_버튼_라벨_초과는_자르지_않고_던진다():
    """자르면 뜻이 달라지는 건 던진다."""
    assert len("전체 7개 보기") <= kakao.MAX_BTN_LABEL_V     # 세로는 14자
    with pytest.raises(kakao.KakaoSpecError):
        kakao.web_button("가" * 15, SRC)                      # 세로 초과
    with pytest.raises(kakao.KakaoSpecError):
        kakao.web_button("가" * 9, SRC, horizontal=True)      # 가로는 8자


def test_simpleText_초과는_자르지_않고_던진다():
    with pytest.raises(kakao.KakaoSpecError):
        kakao.simple_text("가" * 1001)


def test_quickReplies는_넘치면_자른다():
    """추천질문은 잘려도 뜻이 안 달라진다."""
    card, _ = kakao.list_card("h", [{"title": "m"}])
    p = kakao.response([card], [kakao.quick_reply(f"q{i}") for i in range(15)])
    assert len(p["template"]["quickReplies"]) == 10


# ═══════════════════════════════════════════════════════════════
# 템플릿 3분기 — 실데이터
# ═══════════════════════════════════════════════════════════════

@pytest.fixture()
def loaded(conn, tmp_path):
    for fid, name in ((HUSAENG, "후생관"), ("jbnu:facility/진수원", "진수원"),
                      ("jbnu:facility/의대식당", "의대식당")):
        conn.execute(
            """INSERT OR IGNORE INTO facility (id, name, facility_type,
                                               source_url, source_type)
               VALUES (?,?,?,?,'coop')""", (fid, name, "식당", SRC))
    conn.commit()
    res = fetch_mod.make_result("coop_week_menu", SRC, SRC, 200,
                                WEEK_JSON.encode("utf-8"),
                                "2026-08-10T06:00:00+09:00", "json")
    ingest_mod.ingest(conn, res, parser=coop.parse, snapshot_dir=tmp_path,
                      extraction_method="json_api")
    return conn


def _answer(conn, meal_type: str, date: str = "2026-08-10"):
    return branch.resolve_meal(conn, facility_id=HUSAENG, date=date,
                               meal_type=meal_type, now=NOW)


def test_A분기_렌더_실데이터(loaded):
    a = _answer(loaded, "lunch")
    assert a.branch is branch.Branch.A
    p = templates.render_meal(a, facility_name="후생관", date="2026-08-10",
                              meal_type="lunch", source_url=SRC)
    assert kakao.validate(p) == []
    card = p["template"]["outputs"][0]["listCard"]
    assert len(card["items"]) <= 5
    assert "후생관" in card["header"]["title"]


def test_B분기_렌더_실데이터(loaded):
    """후생관 조식은 원천이 '운영없음'을 명시했다."""
    a = _answer(loaded, "breakfast")
    assert a.branch is branch.Branch.B
    p = templates.render_meal(a, facility_name="후생관", date="2026-08-10",
                              meal_type="breakfast", source_url=SRC)
    assert kakao.validate(p) == []
    text = p["template"]["outputs"][0]["simpleText"]["text"]
    assert "운영하지 않아요" in text
    assert "메뉴가 없" not in text, "'없다'와 '운영 안 함'은 다른 말이다"


def test_C2분기는_추측한_값을_내놓지_않는다(loaded):
    a = branch.resolve_meal(loaded, facility_id=HUSAENG, date="2026-08-22",
                            meal_type="lunch", now=NOW)
    assert a.branch is branch.Branch.C2
    p = templates.render_meal(a, facility_name="후생관", date="2026-08-22",
                              meal_type="lunch", source_url=SRC,
                              contact="생협 063-270-2151")
    text = p["template"]["outputs"][0]["simpleText"]["text"]
    assert "확인하지 못했어요" in text and SRC in text


def test_가격이_안_붙은_항목도_카드가_비어보이지_않는다(loaded):
    """68% 가 정상이라 —를 그대로 노출하면 고장난 것처럼 보인다."""
    a = _answer(loaded, "lunch")
    repo.attach_prices(loaded, a.rows, facility_id=HUSAENG, on_date="2026-08-10")
    p = templates.render_meal(a, facility_name="후생관", date="2026-08-10",
                              meal_type="lunch", source_url=SRC)
    for item in p["template"]["outputs"][0]["listCard"]["items"]:
        assert item.get("description"), "설명이 비면 카드가 망가져 보인다"
        assert item["description"] != "—"


# ═══════════════════════════════════════════════════════════════
# 교차검증 재정의 — 관측의 부재는 충돌이 아니다
# ═══════════════════════════════════════════════════════════════

def test_침묵과_진술은_충돌이_아니다():
    primary = coop.parse(WEEK_JSON).meals
    secondary = jbnu.parse(DAY_HTML).meals
    rep = crossvalidate.compare(primary, secondary)
    assert rep.content_conflict_rate == 0.0, "실측 기준선"
    assert rep.coverage_gap_rate > 0, "커버리지 차이는 따로 집계된다"
    assert all(c.kind == "coverage_gap" for c in rep.coverage_gaps)


def test_한쪽만_관측하면_관측한_쪽을_채택한다():
    primary = coop.parse(WEEK_JSON).meals
    secondary = jbnu.parse(DAY_HTML).meals
    merged, rep = crossvalidate.merge(primary, secondary)
    assert rep.adopted_by_observation > 0

    # 진수원 조식: 1차는 빈칸(unknown), 2차는 '운영없음' 명시
    key = ("jbnu:facility/진수원", "2026-08-10", "breakfast", "한식", "백반")
    got = next((m, o) for m, o in merged
               if (m.facility_id, m.date, m.meal_type, m.zone, m.corner) == key)
    meal, origin = got
    assert meal.service_status == "closed_temporary"
    assert origin == crossvalidate.SECONDARY, "출처도 2차를 가리켜야 한다"


def test_둘_다_관측이고_다르면_1차를_채택하고_경보(loaded=None):
    primary = coop.parse(WEEK_JSON).meals
    secondary = jbnu.parse(DAY_HTML).meals
    pk = {(m.facility_id, m.date, m.meal_type, m.zone, m.corner) for m in primary
          if m.service_status == "operating"}
    target = next(m for m in secondary
                  if (m.facility_id, m.date, m.meal_type, m.zone, m.corner) in pk
                  and m.service_status == "operating")
    target.items = [repo.ParsedItem("가짜메뉴", display_order=0)]

    merged, rep = crossvalidate.merge(primary, secondary)
    assert len(rep.conflicts) == 1 and rep.content_conflict_rate > 0
    key = (target.facility_id, target.date, target.meal_type, target.zone, target.corner)
    meal, origin = next((m, o) for m, o in merged
                        if (m.facility_id, m.date, m.meal_type, m.zone, m.corner) == key)
    assert origin == crossvalidate.PRIMARY
    assert "가짜메뉴" not in [i.name for i in meal.items]


# ═══════════════════════════════════════════════════════════════
# 안전 분기 배포 차단
# ═══════════════════════════════════════════════════════════════

def test_현재_설정은_배포_가능하다():
    """총학이 공식 사이트로 확인 완료(2026-08-10). 이제 상담 창구가 나간다."""
    cfg = safety.load()
    assert cfg.deployable
    assert cfg.unverified_contacts() == []
    text = cfg.response_text(cfg.match("죽고싶어요"))
    assert "109" in text and "112" in text


GOOD = ("{label: 자살예방상담전화, phone: '109', verified: true, "
        "verified_at: '2026-08-10', verified_by: '총학생회', "
        "verified_method: official_site}")


def _yaml(*contact_lines: str) -> str:
    body = "".join(f"      - {c}\n" for c in contact_lines)
    return ("priority: [emergency]\nfooter: '긴급시 112'\n"
            "unverified_fallback: 총학에 문의\n"
            "categories:\n  emergency:\n    keywords: [죽고싶]\n    lead: 안내\n"
            f"    contacts:\n{body}")


def test_전부_확인되면_번호가_나간다(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(_yaml(GOOD), encoding="utf-8")
    cfg = safety.load(p)
    assert cfg.deployable
    text = cfg.response_text(cfg.match("죽고싶어요"))
    assert "109" in text and "112" in text


def test_하나라도_미확인이면_전부_막는다(tmp_path):
    """전부 아니면 전무 — 일부만 맞는 목록을 주면 어느 줄이 맞는지 떠넘기는 것이다."""
    p = tmp_path / "mixed.yaml"
    p.write_text(_yaml(GOOD, "{label: 미확인기관, phone: '000-0000'}"),
                 encoding="utf-8")
    cfg = safety.load(p)
    assert not cfg.deployable
    text = cfg.response_text(cfg.match("죽고싶어요"))
    assert "총학" in text
    assert "109" not in text, "확인된 번호까지 같이 막힌다"


def test_확인이력_없는_verified는_예외(tmp_path):
    """★ 조용히 미확인으로 강등하지 않는다.

    절반만 채운 항목은 누군가 확인을 시작했다가 멈춘 흔적이고,
    그 상태로 배포되는 게 가장 위험하다. T4 의 author/approved_by 와 같은 규칙.
    """
    for line in (
        "{label: X, phone: '109', verified: true}",
        "{label: X, phone: '109', verified: true, verified_at: '2026-08-10'}",
        "{label: X, phone: '109', verified: true, verified_by: '총학생회'}",
        "{label: X, phone: '109', verified: true, verified_at: '2026-08-10', "
        "verified_by: '총학생회'}",                                  # method 누락
        "{label: X, phone: '109', verified: true, verified_at: '', "
        "verified_by: '', verified_method: ''}",
    ):
        p = tmp_path / "bad.yaml"
        p.write_text(_yaml(line), encoding="utf-8")
        with pytest.raises(safety.SafetyConfigError):
            safety.load(p)


def test_알_수_없는_확인등급은_예외(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(_yaml(
        "{label: X, phone: '109', verified: true, verified_at: '2026-08-10', "
        "verified_by: '총학생회', verified_method: 대충확인}"), encoding="utf-8")
    with pytest.raises(safety.SafetyConfigError) as e:
        safety.load(p)
    assert "verified_method" in str(e.value)


def test_확인_워크시트가_등급까지_보여준다():
    """나중에 누가 봐도 어느 수준의 확인인지 알 수 있어야 한다."""
    rows = safety.load().verification_worksheet()
    assert rows and all(r["verified"] for r in rows)
    assert {r["verified_method"] for r in rows} <= {"official_site", "phone"}

    # ★ 전북대 두 곳은 **전화로 확인**했다 (2026-08-31, 총학생회장이 직접 걺).
    #   이 봇에서 유일하게 사람 목숨에 닿는 문장이다 —
    #   다른 답은 틀리면 학생이 헛걸음하지만 이건 위급한 학생이
    #   **안 받는 전화**를 건다. 틀릴 때의 값이 다르면 등급도 달라야 한다.
    #   등급이 되돌아가면 여기서 잡는다.
    for name in ("전북대 행복드림센터", "전북대 인권센터"):
        r = next(x for x in rows if x["label"] == name)
        assert r["verified_method"] == "phone", name
    # 같은 기관이 여러 범주에 걸쳐도 한 줄로 모은다
    center = next(r for r in rows if r["label"] == "전북대 인권센터")
    assert set(center["categories"]) == {"violence", "harassment"}


def test_안전_응답에_내부_메모가_새지_않는다():
    """note 는 답변에 그대로 나간다. 찾아가는 데 필요한 것만 쓴다.

    위기 상황의 사용자에게 배경 설명은 소음이고, 소음이 쌓이면 정작 필요한
    번호가 묻힌다.
    """
    cfg = safety.load()
    text = cfg.response_text(cfg.match("죽고싶어요"))
    assert "진수당 1층" in text, "찾아가는 정보는 남는다"
    for noise in ("통합", "1393", "2024년"):
        assert noise not in text, f"내부 메모가 새어 나갔다: {noise!r}"


def test_확인_불가한_기관은_목록에_없다():
    """국가인권위원회(1331)는 공식 사이트 확인 불가로 제외했다.

    확인 안 된 번호를 남겨두면 안전 분기 전체가 막혀 급한 사람에게 목록을 못 준다.
    """
    labels = {r["label"] for r in safety.load().verification_worksheet()}
    assert not any("국가인권위" in x for x in labels)


def test_미확인_항목이_어느_것인지_알려준다(tmp_path):
    """차단만 하지 않고 해제 경로를 준다."""
    p = tmp_path / "u.yaml"
    p.write_text(_yaml(GOOD, "{label: 미확인기관, phone: '000'}"), encoding="utf-8")
    un = safety.load(p).unverified_contacts()
    assert [x[1] for x in un] == ["미확인기관 000"]
