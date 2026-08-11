"""원문 보관 — 재파싱을 위해서다.

    원문이 바뀌었다  → 재수집 (네트워크)
    해석이 바뀌었다  → 재파싱 (디스크)

파서를 고칠 때마다 6,985페이지를 다시 긁는 것은 네트워크를 캐시 대신 쓰는 일이다.
"""

from __future__ import annotations

from crawler import snapshots

URL = "https://csai.jbnu.ac.kr/csai/29114/subview.do"
HTML = "<html><body><h1>졸업기준</h1><p>학점은 130학점 이상</p></body></html>"


def test_저장하고_그대로_복원한다(tmp_path):
    snapshots.save(URL, HTML, tmp_path)
    assert snapshots.load(URL, tmp_path) == HTML


def test_같은_주소는_같은_파일에_덮어쓴다(tmp_path):
    """이력이 아니라 **최신 원문**을 갖는 것이 목적이다.

    이력이 필요하면 source_snapshot 쪽 규약(시점 포함 식별자)을 쓴다.
    """
    snapshots.save(URL, HTML, tmp_path)
    snapshots.save(URL, HTML + "<!-- 갱신 -->", tmp_path)
    assert snapshots.usage(tmp_path)["files"] == 1
    assert "갱신" in snapshots.load(URL, tmp_path)


def test_없으면_None_이지_예외가_아니다(tmp_path):
    """원문이 없는 페이지는 재파싱 대상에서 빠질 뿐, 작업을 멈추지 않는다."""
    assert snapshots.load("https://x.jbnu.ac.kr/없음", tmp_path) is None


def test_호스트별로_나눠_담는다(tmp_path):
    snapshots.save(URL, HTML, tmp_path)
    snapshots.save("https://me.jbnu.ac.kr/me/1/subview.do", HTML, tmp_path)
    hosts = {p.parent.name for p in (tmp_path / "pages").rglob("*.html.gz")}
    assert hosts == {"csai.jbnu.ac.kr", "me.jbnu.ac.kr"}


def test_사용량을_보고한다(tmp_path):
    """디스크는 유한하고, 조용히 차면 배포가 멈춘다."""
    snapshots.save(URL, HTML, tmp_path)
    u = snapshots.usage(tmp_path)
    assert u["files"] == 1 and u["bytes"] > 0
