"""대표 PC 자동 수집 스크립트 — 지켜야 할 것들.

★ 이 스크립트들은 대표 PC 에서 돈다. 우리가 못 보는 자리다.
  그래서 '이렇게 하기로 했다' 를 사람 기억이 아니라 여기에 둔다.

★ 왜 PC 인가 (2026-08-14)
  생협 API 가 해외 IP 를 막는다. 서버에서 직접 재보니
  curl 그대로 · 브라우저 UA · UA+Referer 전부 403 에 318바이트로
  **응답이 한 바이트도 안 변했다.** 헤더는 원인이 아니고,
  지역 차단인지 데이터센터 차단인지도 구별이 안 된다.
  검증 안 된 가정 위에 클라우드 계정을 쌓지 않기로 했다.
"""

from __future__ import annotations

import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
PUSH = SCRIPTS / "push_meal.ps1"
CHECK = SCRIPTS / "check_meal.ps1"


def _text(p: pathlib.Path) -> str:
    return p.read_bytes().decode("utf-8-sig")


@pytest.mark.parametrize("p", [PUSH, CHECK])
def test_스크립트가_있다(p):
    assert p.exists(), f"{p.name} 이 없다"


@pytest.mark.parametrize("p", [PUSH, CHECK])
def test_UTF8_BOM_이_있다(p):
    """★ PowerShell 5.1 은 BOM 이 없으면 스크립트를 ANSI 로 읽는다.

    실행해 보고 알았다 — 한글이 전부 깨져 나왔다.
    대표가 보는 화면이라 이게 깨지면 아무것도 못 읽는다.
    """
    assert p.read_bytes().startswith(b"\xef\xbb\xbf"), \
        f"{p.name}: BOM 이 없으면 한글이 깨진다"


@pytest.mark.parametrize("p", [PUSH, CHECK])
def test_토큰을_명령행으로_넘기지_않는다(p):
    """★ 명령행 인자로 주면 프로세스 목록과 셸 히스토리에 남는다.

    환경변수에서만 읽는다 — push.py 도 같은 규칙이다.
    """
    s = _text(p)
    assert "$env:SKILL_TOKEN" in s, "환경변수에서 읽어야 한다"
    for bad in ("--token", "-Token ", "SKILL_TOKEN=", "$Token"):
        assert bad not in s, f"{p.name}: 토큰을 인자로 넘기는 흔적 ({bad})"


def test_토큰_값을_화면이나_파일에_찍지_않는다():
    """토큰은 로그에도 남기지 않는다. 로그는 대표가 캡처해서 보낼 수 있다."""
    for p in (PUSH, CHECK):
        s = _text(p)
        # 값을 그대로 출력하는 꼴
        assert 'Write-Output $env:SKILL_TOKEN' not in s
        assert 'Write-Line $env:SKILL_TOKEN' not in s
        assert '"$env:SKILL_TOKEN"' not in s


def test_네이티브_실행에_2대1_리다이렉트를_쓰지_않는다():
    """★ 실행해 보고 잡은 것 — 이게 '조용히 실패' 를 만들었다.

    PowerShell 5.1 은 네이티브 exe 의 stderr 를 ErrorRecord 로 감싼다.
    $ErrorActionPreference='Stop' 이라 그게 스크립트를 죽였고,
    로그가 '시작' 에서 끊기고 결과 줄도 경고 파일도 안 남았다 —
    **우리가 막으려던 상태 그 자체가 됐다.**
    """
    s = _text(PUSH)
    # ★ 주석은 뺀다 — 왜 쓰면 안 되는지가 주석에 적혀 있어서
    #   그대로 재면 설명문이 자기 자신을 고장으로 신고한다.
    #   (오늘 별칭 도구에서 같은 실수를 했다)
    code = "\n".join(l for l in s.splitlines() if not l.lstrip().startswith("#"))
    assert "2>&1" not in code, "네이티브 호출에 2>&1 을 쓰면 조용히 죽는다"
    assert "Start-Process" in code and "-RedirectStandardError" in code


def test_실패하면_세_군데에_남긴다():
    """로그 · 마지막결과 · 바탕화면 경고. 그리고 종료 코드."""
    s = _text(PUSH)
    assert "push_meal.log" in s
    assert "LAST_RESULT.txt" in s
    assert "학식_수집_실패.txt" in s
    assert "exit $code" in s


def test_성공하면_경고_파일을_지운다():
    """★ 낡은 경고가 남아 있으면 '또 실패했나' 를 매번 사람이 판단하게 된다."""
    s = _text(PUSH)
    assert "Remove-Item $Alert" in s


def test_확인은_서버에_묻는다():
    """★ 작업이 아예 안 돌았으면 PC 에는 아무 기록도 안 남는다.

    없는 것을 알아채는 게 제일 어렵다. 그래서 '마지막 성공' 은 서버가 답한다.
    오늘 서버에서 겪은 게 정확히 그 상태였다 — 돌긴 도는데 기록이 없었다.
    """
    s = _text(CHECK)
    assert "/admin/status" in s
    assert "coop_week_menu" in s


def test_절차서가_핵심_옵션을_짚는다():
    """'놓친 작업 실행' 이 이 설계의 전부다 — 그게 사람의 기억을 대신한다."""
    doc = (ROOT / "docs" / "PC_SCHEDULER.md").read_text(encoding="utf-8")
    assert "놓친 경우 가능한 대로 빨리 작업 시작" in doc
    assert "시스템 변수" in doc, "사용자 변수에 넣으면 스케줄러가 못 본다"
    # 토큰을 문서에 적어두라고 하면 안 된다
    assert "카카오톡" in doc and "붙여넣지 마세요" in doc
