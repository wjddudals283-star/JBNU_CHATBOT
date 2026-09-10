"""답안 → 필드. 보내기 전 검증이 실제로 거르는지."""
import pytest

from gform.payload import OTHER_SENTINEL, PayloadError, build_payload, header_for, split_multi

FULL = {
    "이름": "홍길동",
    "소속": "공과대학",
    "관심 기능": ["수강신청", "학식"],
    "만족도": "4",
    "채널별 이용 빈도 » 인스타": "매일",
    "채널별 이용 빈도 » 카카오톡": "가끔",
    "참여일": "2026-09-10",
    "선호 시각": "09:30",
    "학년": "2",
}


def test_full_payload(form):
    p = build_payload(form, FULL)
    d = {}
    for k, v in p:
        d.setdefault(k, []).append(v)
    assert d["entry.1001"] == ["홍길동"]
    assert d["entry.1003"] == ["수강신청", "학식"]       # 같은 이름 반복
    assert d["entry.1003_sentinel"] == [""]
    assert d["entry.1005"] == ["매일"] and d["entry.1006"] == ["가끔"]
    assert d["entry.1007_year"] == ["2026"] and d["entry.1007_month"] == ["9"] and d["entry.1007_day"] == ["10"]
    assert d["entry.1008_hour"] == ["9"] and d["entry.1008_minute"] == ["30"]
    assert d["fvv"] == ["1"] and d["pageHistory"] == ["0,1"]
    assert d["fbzx"] == ["-7342119912345678901"]
    assert d["partialResponse"] == ['[null,null,"-7342119912345678901"]']
    assert "emailAddress" not in d


def test_keys_by_entry_id_forms(form):
    ans = dict(FULL)
    ans.pop("이름")
    for key in (1001, "1001", "entry.1001", "이름 [entry.1001]"):
        p = dict(build_payload(form, {**ans, key: "x"}))
        assert p["entry.1001"] == "x"


def test_required_missing(form):
    ans = dict(FULL); ans.pop("소속")
    with pytest.raises(PayloadError, match="소속.*필수"):
        build_payload(form, ans)


def test_optional_missing_is_fine(form):
    ans = dict(FULL); ans.pop("관심 기능"); ans.pop("참여일")
    names = {k for k, _ in build_payload(form, ans)}
    assert "entry.1003" not in names and "entry.1007_year" not in names


def test_other_option(form):
    p = build_payload(form, {**FULL, "소속": "약학대학"})
    d = dict(p)
    assert d["entry.1002"] == OTHER_SENTINEL
    assert d["entry.1002.other_option_response"] == "약학대학"


def test_unknown_option_without_other_rejected(form):
    with pytest.raises(PayloadError, match="선택지에 없는 값"):
        build_payload(form, {**FULL, "학년": "3"})
    with pytest.raises(PayloadError, match="선택지에 없는 값"):
        build_payload(form, {**FULL, "관심 기능": ["수강신청", "기숙사"]})


def test_multiple_values_on_single_choice_rejected(form):
    with pytest.raises(PayloadError, match="하나만"):
        build_payload(form, {**FULL, "소속": ["공과대학", "인문대학"]})


def test_unknown_question_rejected(form):
    with pytest.raises(PayloadError, match="폼에 없는 문항"):
        build_payload(form, {**FULL, "없는 문항": "x"})


@pytest.mark.parametrize("bad", ["2026/09/10", "09-10 10:00", "2026-13-01", "어제"])
def test_bad_date(form, bad):
    with pytest.raises(PayloadError, match="참여일"):
        build_payload(form, {**FULL, "참여일": bad})


@pytest.mark.parametrize("bad", ["9시", "25:00", "09:60"])
def test_bad_time(form, bad):
    with pytest.raises(PayloadError, match="선호 시각"):
        build_payload(form, {**FULL, "선호 시각": bad})


def test_email_required_when_form_collects(form):
    form.collects_email = True
    with pytest.raises(PayloadError, match="이메일"):
        build_payload(form, FULL)
    d = dict(build_payload(form, FULL, email="a@b.c"))
    assert d["emailAddress"] == "a@b.c"


def test_email_rejected_when_form_does_not_collect(form):
    with pytest.raises(PayloadError, match="수집하지 않는데"):
        build_payload(form, FULL, email="a@b.c")


def test_header(form):
    h = header_for(form)
    assert h[0] == "이름" and "채널별 이용 빈도 » 인스타" in h
    form.collects_email = True
    assert header_for(form)[0] == "emailAddress"


def test_header_disambiguates_duplicates(form):
    form.by_entry(1010).title = "이름"        # 제목 충돌
    h = header_for(form)
    assert "이름 [entry.1001]" in h and "이름 [entry.1010]" in h


def test_split_multi():
    assert split_multi("a | b|| c ", "|") == ["a", "b", "c"]
    assert split_multi("a;b", ";") == ["a", "b"]
