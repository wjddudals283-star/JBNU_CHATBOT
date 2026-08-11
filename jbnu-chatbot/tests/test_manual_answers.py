"""총학이 직접 확인해 넣은 답 (T4).

홈페이지에 없는 것은 크롤로 영영 못 채운다. 사람이 확인해 넣는다.
다만 **확인한 것만** 나가야 한다 — 안전 연락처에서 세운 규칙과 같다.
"""

from __future__ import annotations

import pathlib

from skill import manual_answers as ma
from skill import templates

TODAY = "2026-08-11"


def _entry(**kw) -> ma.ManualAnswer:
    base = dict(key="학점포기", ask=["학점포기", "학점 포기"],
                answer="전북대는 학점포기 제도를 운영하지 않습니다.",
                source="학사과 유선 확인", verified_by="사무국장",
                verified_at="2026-08-11", valid_to="2027-02-28", enabled=True)
    base.update(kw)
    return ma.ManualAnswer(**base)


def test_확인된_답만_나간다():
    ok = _entry()
    assert ma.find("학점포기 되나요", today=TODAY, entries=[ok]) is ok

    # 하나라도 비면 안 나간다
    for missing in ("verified_by", "verified_at", "valid_to"):
        e = _entry(**{missing: ""})
        assert ma.find("학점포기", today=TODAY, entries=[e]) is None, missing


def test_꺼진_항목은_나가지_않는다():
    assert ma.find("학점포기", today=TODAY, entries=[_entry(enabled=False)]) is None


def test_확인_전_문구는_답이_아니다():
    """틀을 채우기 전 상태가 그대로 나가면 학생이 '(확인 전…)' 을 받는다."""
    e = _entry(answer="(확인 전 — 학사과에 문의한 뒤 채우세요)")
    assert ma.find("학점포기", today=TODAY, entries=[e]) is None


def test_만료되면_스스로_꺼진다():
    """학사 제도는 바뀐다. 지난 답을 계속 내보내는 것이 모르는 것보다 위험하다."""
    e = _entry(valid_to="2026-08-10")
    assert ma.find("학점포기", today=TODAY, entries=[e]) is None
    assert e.status(TODAY) == "만료"


def test_더_구체적인_표현이_이긴다():
    """'통금' 과 '기숙사 통금' 이 둘 다 걸리면 구체적인 쪽이 맞다."""
    broad = _entry(key="통금", ask=["통금"], answer="일반 안내")
    exact = _entry(key="기숙사통금", ask=["기숙사 통금"], answer="생활관 출입 안내")
    got = ma.find("기숙사 통금 몇 시야", today=TODAY, entries=[broad, exact])
    assert got is exact


def test_답변에_확인_경로와_확인일이_붙는다():
    """홈페이지 링크가 없으므로 어디에 물어 확인했는지를 대신 밝힌다.

    검증할 수 없는 답은 믿어달라는 요구다.
    """
    out = templates.render_manual(_entry())
    text = out["template"]["outputs"][0]["simpleText"]["text"]
    assert "학사과 유선 확인" in text
    assert "2026-08-11" in text
    assert "총학생회" in text


def test_상태를_그대로_보고한다():
    rows = [_entry(), _entry(key="만료건", valid_to="2026-01-01"),
            _entry(key="미확인건", verified_by="")]
    r = ma.report(today=TODAY, entries=rows)
    assert r["live"] == 1 and r["expired"] == 1 and r["unverified"] == 1


def test_만료_임박을_미리_알린다():
    """만료된 뒤에 아는 것은 늦다."""
    soon = _entry(key="곧만료", valid_to="2026-08-20")
    r = ma.report(today=TODAY, entries=[soon])
    assert "곧만료" in r["expiring_soon"]


def test_배포된_설정에는_실명이_없다():
    """verified_by 는 직책으로 쓴다. 공개 저장소에 개인정보를 남기지 않는다."""
    text = ma.CONFIG_PATH.read_text(encoding="utf-8")
    assert "직책" in text          # 규칙이 파일에 적혀 있어야 한다
    for e in ma._load(ma.CONFIG_PATH):
        # 아직 아무것도 확인되지 않았어야 한다 (확인 전 배포 금지)
        assert not e.ready or e.verified_by, e.key


# ── 부재는 관측되지 않는다 ───────────────────────────────────────────────

def test_제도가_없다는_답은_사람만_넣을_수_있다():
    """크롤로는 영원히 알 수 없다.

    없는 제도는 페이지가 없고, 4,231페이지 어디에도
    "학점포기는 없습니다" 라는 문장은 없다. 웹은 있는 것만 말한다.
    """
    e = ma.find("학점포기 되나요", today=TODAY)
    assert e is not None and e.kind == "absent"
    out = templates.render_manual(e)
    text = out["template"]["outputs"][0]["simpleText"]["text"]
    assert "없어요" in text


def test_없다고만_하지_않고_대안을_준다():
    """'없어요' 만 하면 학생은 다른 데서 계속 찾는다."""
    e = ma.find("학점포기", today=TODAY)
    text = templates.render_manual(e)["template"]["outputs"][0]["simpleText"]["text"]
    assert "재수강" in text


def test_부재_답변도_만료와_확인자를_요구한다():
    """제도는 생길 수 있다. 없다는 답도 언젠가 틀린다."""
    e = _entry(kind="absent", valid_to="2026-08-10")
    assert ma.find("학점포기", today=TODAY, entries=[e]) is None
