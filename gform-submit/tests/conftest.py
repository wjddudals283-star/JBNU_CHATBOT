import pathlib
import pytest

FIX = pathlib.Path(__file__).parent / "fixtures"


@pytest.fixture
def viewform_html() -> str:
    return (FIX / "viewform.html").read_text(encoding="utf-8")


@pytest.fixture
def email_form_html() -> str:
    return (FIX / "viewform_email.html").read_text(encoding="utf-8")


@pytest.fixture
def confirmation_html() -> str:
    return (FIX / "confirmation.html").read_text(encoding="utf-8")


@pytest.fixture
def form(viewform_html):
    from gform.schema import parse_form_html
    return parse_form_html(viewform_html, form_id="1FAIpQLSeTESTFORMID")
