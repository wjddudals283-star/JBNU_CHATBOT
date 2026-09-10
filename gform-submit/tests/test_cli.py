"""명령줄 end-to-end. 네트워크는 MockTransport."""
import csv
import httpx
import pytest

from gform import cli
from gform.client import FormClient

VIEW = "https://docs.google.com/forms/d/e/1FAIpQLSeTESTFORMID/viewform"


@pytest.fixture
def patched_client(monkeypatch, viewform_html, confirmation_html):
    posts = []
    def h(req: httpx.Request):
        if req.method == "POST":
            posts.append(req.content.decode())
            return httpx.Response(200, text=confirmation_html)
        return httpx.Response(200, text=viewform_html)
    orig = FormClient.__init__
    def init(self, **kw):
        kw["transport"] = httpx.MockTransport(h)
        kw["min_interval"] = 0.0
        orig(self, **kw)
    monkeypatch.setattr(FormClient, "__init__", init)
    return posts


def write_csv(path, header, rows):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f); w.writerow(header); w.writerows(rows)


HEADER = ["이름", "소속", "관심 기능", "만족도", "채널별 이용 빈도 » 인스타",
          "채널별 이용 빈도 » 카카오톡", "참여일", "선호 시각", "학년"]
ROW = ["홍길동", "공과대학", "수강신청|학식", "4", "매일", "가끔", "2026-09-10", "09:30", "2"]


def test_inspect(patched_client, capsys):
    assert cli.main(["inspect", VIEW]) == 0
    out = capsys.readouterr().out
    assert "entry.1002" in out and "공과대학 / 인문대학" in out and "+기타" in out
    assert "증빙" in out                         # 건너뜀 표시


def test_template(patched_client, tmp_path):
    out = tmp_path / "t.csv"
    assert cli.main(["template", VIEW, "-o", str(out)]) == 0
    with out.open(encoding="utf-8-sig") as f:
        assert next(csv.reader(f)) == HEADER


def test_submit_dry_run_sends_nothing(patched_client, tmp_path, capsys):
    p = tmp_path / "a.csv"; write_csv(p, HEADER, [ROW, ROW])
    assert cli.main(["submit", VIEW, str(p), "--dry-run"]) == 0
    assert patched_client == []
    assert "2건 검증 통과" in capsys.readouterr().out


def test_submit_sends_each_row(patched_client, tmp_path):
    p = tmp_path / "a.csv"; write_csv(p, HEADER, [ROW, ["#메모 줄", *ROW[1:]], ROW])
    rep = tmp_path / "r.csv"
    assert cli.main(["submit", VIEW, str(p), "--yes", "--report", str(rep)]) == 0
    assert len(patched_client) == 2              # # 줄은 건너뜀
    assert "entry.1003=%EC%88%98%EA%B0%95%EC%8B%A0%EC%B2%AD" in patched_client[0]   # 수강신청
    with rep.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["ok"] for r in rows] == ["1", "1"] and rows[1]["csv_row"] == "4"


def test_submit_validates_all_before_sending(patched_client, tmp_path, capsys):
    bad = list(ROW); bad[1] = ""                  # 필수 '소속' 비움
    p = tmp_path / "a.csv"; write_csv(p, HEADER, [ROW, bad])
    assert cli.main(["submit", VIEW, str(p), "--yes"]) == 2
    assert patched_client == []                   # 한 건도 안 나감
    err = capsys.readouterr().err
    assert "3행" in err and "필수" in err


def test_submit_limit(patched_client, tmp_path):
    p = tmp_path / "a.csv"; write_csv(p, HEADER, [ROW, ROW, ROW])
    assert cli.main(["submit", VIEW, str(p), "--yes", "--limit", "1"]) == 0
    assert len(patched_client) == 1


def test_submit_custom_sep_and_entry_headers(patched_client, tmp_path):
    header = ["entry.1001", "entry.1002", "entry.1003", "entry.1004", "entry.1005", "entry.1006"]
    p = tmp_path / "a.csv"; write_csv(p, header, [["a", "인문대학", "수강신청;장학금", "1", "매일", "매일"]])
    assert cli.main(["submit", VIEW, str(p), "--yes", "--sep", ";"]) == 0
    body = patched_client[0]
    assert body.count("entry.1003=") == 2


def test_submit_declined_confirmation(patched_client, tmp_path, monkeypatch):
    p = tmp_path / "a.csv"; write_csv(p, HEADER, [ROW])
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert cli.main(["submit", VIEW, str(p)]) == 1
    assert patched_client == []
