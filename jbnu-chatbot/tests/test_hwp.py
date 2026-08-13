"""HWP 본문 추출 — 학칙·규정을 읽는다.

★ 왜 값진가
  안내 페이지는 시스템 이름이 바뀌면 낡는다 — OASIS → JUMP 로 오늘 겪었다.
  학칙은 규정 자체라 안 흔들린다.
      제53조(휴학절차) ① … 휴학원을 학장에게 제출하여 총장의 허가를 얻어야 한다
  시스템 이름이 한 글자도 없다.

★ 학칙을 학생에게 **우선하지 않는다**
  학칙은 문어체라 안내가 아니라 규정이다.
      안내 있음  → "휴학시기 : 수업일수 3/4선 이내"      ← 이게 먼저다
      없을 때만  → "총장의 허가를 얻어야 한다"
  우선순위를 뒤집으면 지금보다 나빠진다. 학칙의 값어치는 **답값**에 있다.
"""

from __future__ import annotations

import struct

import pytest

from crawler.parsers import hwp


def rec(tag: int, payload: bytes, level: int = 0) -> bytes:
    h = (tag & 0x3FF) | ((level & 0x3FF) << 10) | ((len(payload) & 0xFFF) << 20)
    return struct.pack("<I", h) + payload


def para(text: str) -> bytes:
    return rec(hwp.TAG_PARA_TEXT, text.encode("utf-16-le"))


def test_레코드를_편다():
    buf = para("제1조(목적)") + para("이 학칙은…")
    got = [p for t, _, p in hwp._records(buf) if t == hwp.TAG_PARA_TEXT]
    assert len(got) == 2
    assert hwp._para_text(got[0]) == "제1조(목적)"


def test_큰_레코드는_크기가_따로_붙는다():
    """size 가 0xFFF 면 다음 uint32 가 진짜 크기다. 안 다루면 본문이 잘린다."""
    body = ("가" * 3000).encode("utf-16-le")
    h = (hwp.TAG_PARA_TEXT & 0x3FF) | (0xFFF << 20)
    buf = struct.pack("<I", h) + struct.pack("<I", len(body)) + body
    out = list(hwp._records(buf))
    assert len(out) == 1 and len(out[0][2]) == len(body)


def test_제어문자를_버리지_않고_경계로_바꾼다():
    """★ 버리면 앞뒤 문장이 붙어 없는 조문이 만들어진다.

    표 자리를 그냥 지우면 '제5조' 와 '제3항' 이 이어져 '제5조제3항' 이 된다.
    """
    payload = ("제5조".encode("utf-16-le")
               + struct.pack("<H", 4)           # 표 등 인라인 제어문자
               + "제3항".encode("utf-16-le"))
    out = hwp._para_text(payload)
    assert "제5조제3항" not in out
    assert "제5조" in out and "제3항" in out


def test_조문_단위로_묶는다():
    """조문이 인용 단위다 — 쪼개면 항이 조 없이 떠다니고,
    합치면 규정 전체가 한 덩어리가 된다."""
    paras = ["전북대학교 학칙", "제1조(목적) 이 학칙은…", "② 둘째 항",
             "제2조(정의) 용어는…", "③ 셋째 항"]
    arts = hwp.articles(paras)
    assert [t.split("(")[0] for t, _ in arts] == ["제1조", "제2조"]
    assert arts[0][1] == ["② 둘째 항"]
    # 조문 앞의 표지는 버린다 — 어느 조에도 속하지 않는다
    assert all("전북대학교 학칙" not in t for t, _ in arts)


def test_제N조의M_도_조문이다():
    arts = hwp.articles(["제35조의2(특별학기) ① …", "② …"])
    assert len(arts) == 1 and arts[0][0].startswith("제35조의2")


def test_HWP가_아니면_조용히_빈값을_주지_않는다():
    """왜 못 읽었는지 남긴다. 빈 문자열을 돌려주면 '내용 없는 규정' 이 된다."""
    with pytest.raises(hwp.NotHwp):
        hwp.extract(b"%PDF-1.4 not an ole file")


def test_실제_학칙이_있으면_조문이_나온다(tmp_path):
    """받아둔 파일이 있을 때만 돈다. 네트워크를 타지 않는다."""
    import pathlib
    cand = list(pathlib.Path(".").glob("**/*.hwp"))
    if not cand:
        pytest.skip("hwp 표본 없음")
    arts = hwp.articles(hwp.extract(cand[0].read_bytes()))
    assert arts, "조문을 하나도 못 뽑았다"
