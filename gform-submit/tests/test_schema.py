"""viewform HTML → Form. 구조 관측이 맞는지, 비입력·미지원 문항이 제대로 빠지는지."""
import pytest

from gform.schema import QuestionType, SchemaError, normalize_form_url, parse_form_html


def test_title_and_pages(form):
    assert form.title == "행사 참가 신청"
    assert form.page_count == 2                 # 페이지 나눔 하나
    assert form.fbzx == "-7342119912345678901"
    assert form.page_history == "0,1"
    assert form.collects_email is False


def test_input_questions_in_order(form):
    labels = [q.label for q in form.inputs]
    assert labels == ["이름", "소속", "관심 세션", "만족도",
                      "채널별 이용 빈도 » 이메일", "채널별 이용 빈도 » 문자",
                      "참여일", "선호 시각", "학년"]


def test_non_input_and_unsupported_are_excluded(form):
    kinds = {q.qtype for q in form.questions}
    assert QuestionType.TITLE not in kinds
    assert QuestionType.PAGE_BREAK not in kinds
    assert QuestionType.FILE_UPLOAD not in kinds
    assert form.skipped == ["증빙"]


def test_choice_options_and_other(form):
    q = form.by_entry(1002)
    assert q.qtype is QuestionType.CHOICE
    assert q.required
    assert q.options == ["공과대학", "인문대학"]     # '기타' 는 선택지 목록에 안 들어간다
    assert q.allows_other


def test_checkbox_is_multi(form):
    q = form.by_entry(1003)
    assert q.multi and not q.required and not q.allows_other


def test_grid_rows_and_page(form):
    a, b = form.by_entry(1005), form.by_entry(1006)
    assert a.row_label == "이메일" and b.row_label == "문자"
    assert a.page == 1                            # 페이지 나눔 뒤
    assert form.by_entry(1001).page == 0


def test_date_time_flags(form):
    d = form.by_entry(1007)
    assert d.qtype is QuestionType.DATE and d.date_has_year and not d.date_has_time
    t = form.by_entry(1008)
    assert t.qtype is QuestionType.TIME and not t.time_is_duration


def test_email_collection_detected(email_form_html):
    f = parse_form_html(email_form_html)
    assert f.collects_email


def test_urls(form):
    assert form.viewform_url.endswith("/forms/d/e/1FAIpQLSeTESTFORMID/viewform")
    assert form.response_url.endswith("/forms/d/e/1FAIpQLSeTESTFORMID/formResponse")
    form.public = False
    assert "/forms/d/1FAIpQLSeTESTFORMID/formResponse" in form.response_url


@pytest.mark.parametrize("url,fid,public", [
    ("https://docs.google.com/forms/d/e/1FAIpQLSeABC_-x/viewform?usp=sf_link", "1FAIpQLSeABC_-x", True),
    ("docs.google.com/forms/d/e/1FAIpQLSeABC/formResponse", "1FAIpQLSeABC", True),
    ("https://docs.google.com/forms/d/1abcDEF/edit", "1abcDEF", False),
])
def test_normalize_url(url, fid, public):
    got_id, view = normalize_form_url(url)
    assert got_id == fid
    assert view.endswith("/viewform")
    assert ("/d/e/" in view) is public


def test_normalize_rejects_non_form():
    with pytest.raises(SchemaError):
        normalize_form_url("https://example.com/forms/d/e/abc/viewform")
    with pytest.raises(SchemaError):
        normalize_form_url("https://forms.gle/abc")


def test_missing_data_is_error():
    with pytest.raises(SchemaError):
        parse_form_html("<html>로그인 페이지</html>")
