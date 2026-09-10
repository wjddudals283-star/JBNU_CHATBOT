"""HTTP 경계. MockTransport 로 구글 응답을 흉내 낸다 — 실제 폼에는 붙지 않는다."""
import httpx
import pytest

from gform.client import FormClient, LoginRequired
from gform.payload import build_payload
from gform.schema import SchemaError

VIEW = "https://docs.google.com/forms/d/e/1FAIpQLSeTESTFORMID/viewform"
POST = "https://docs.google.com/forms/d/e/1FAIpQLSeTESTFORMID/formResponse"


def make_client(handler):
    return FormClient(transport=httpx.MockTransport(handler))


def test_fetch_form(viewform_html):
    def h(req: httpx.Request):
        assert str(req.url) == VIEW
        return httpx.Response(200, text=viewform_html)
    with make_client(h) as c:
        f = c.fetch_form("https://docs.google.com/forms/d/e/1FAIpQLSeTESTFORMID/viewform?usp=x")
    assert f.form_id == "1FAIpQLSeTESTFORMID" and f.public and len(f.inputs) == 9


def test_fetch_resolves_forms_gle(viewform_html):
    def h(req: httpx.Request):
        if req.url.host == "forms.gle":
            return httpx.Response(302, headers={"location": VIEW})
        return httpx.Response(200, text=viewform_html)
    with make_client(h) as c:
        assert c.fetch_form("https://forms.gle/abc123").title == "총학 챗봇 수요 조사"


def test_fetch_login_required():
    def h(req):
        return httpx.Response(302, headers={"location": "https://accounts.google.com/ServiceLogin?x"})
    with make_client(h) as c, pytest.raises(LoginRequired):
        c.fetch_form(VIEW)


def test_fetch_http_error():
    with make_client(lambda r: httpx.Response(404)) as c, pytest.raises(SchemaError):
        c.fetch_form(VIEW)


def test_submit_success(form, confirmation_html):
    seen = {}
    def h(req: httpx.Request):
        seen["url"] = str(req.url)
        seen["body"] = req.content.decode()
        seen["referer"] = req.headers.get("referer")
        return httpx.Response(200, text=confirmation_html)
    with make_client(h) as c:
        r = c.submit(form, build_payload(form, {"이름": "a", "소속": "공과대학", "만족도": "5",
                                                "채널별 이용 빈도 » 인스타": "매일",
                                                "채널별 이용 빈도 » 카카오톡": "매일"}))
    assert r.ok and r.status == 200
    assert seen["url"] == POST and seen["referer"] == VIEW
    assert "entry.1001=a" in seen["body"] and "pageHistory=0%2C1" in seen["body"]


def test_submit_rejected_when_form_rerendered(form, viewform_html):
    with make_client(lambda r: httpx.Response(200, text=viewform_html)) as c:
        r = c.submit(form, [("fvv", "1")])
    assert not r.ok and "거절" in r.reason


def test_submit_login_redirect_and_http_error(form):
    with make_client(lambda r: httpx.Response(302, headers={"location": "https://accounts.google.com/x"})) as c:
        assert c.submit(form, []).reason == "로그인 필요"
    with make_client(lambda r: httpx.Response(500)) as c:
        assert c.submit(form, []).status == 500


def test_submit_network_error(form):
    def h(r):
        raise httpx.ConnectError("boom")
    with make_client(h) as c:
        r = c.submit(form, [])
    assert not r.ok and "네트워크" in r.reason


def test_throttle(form, confirmation_html, monkeypatch):
    import gform.client as mod
    clock = {"t": 100.0}
    slept = []
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(mod.time, "sleep", lambda s: slept.append(s))
    with FormClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=confirmation_html)),
                    min_interval=2.0) as c:
        c.submit(form, [])           # 첫 건: 대기 없음
        c.submit(form, [])           # 바로 다음: 2초 대기
    assert slept == [2.0]
