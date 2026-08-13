"""HWP 5.0 본문 추출 — 학칙·규정을 읽기 위해서다.

★ 왜 필요한가
  전북대 규정집 609개(학생 관련 58개)가 전부 HWP 다.
  안내 페이지는 시스템 이름이 바뀌면 낡는다 — 오늘 OASIS → JUMP 로 겪었다.
  학칙은 '총장의 허가를 얻어' 같은 규정 자체라 안 흔들린다.
  그리고 must 를 **답값**으로 쓸 수 있는 유일한 원문이다.

★ 구조 (한글 문서 파일 형식 5.0 공개 규격)
  OLE 복합문서다. 첫 바이트가 D0 CF 11 E0.
      FileHeader          32바이트 서명 + 버전 + 속성
      BodyText/Section0..N  본문. 대개 zlib 로 눌려 있다 (raw deflate)
      레코드 = 헤더 uint32 + 데이터
          tag  = h & 0x3FF        (67 = PARA_TEXT)
          size = (h >> 20) & 0xFFF   0xFFF 면 다음 uint32 가 진짜 크기

★ 요약하지 않는다 — 여기도 같다
  조문을 그대로 옮긴다. 문단 경계만 살리고 손대지 않는다.
  제어문자(표·그림 자리)는 **버리지 않고 경계로 바꾼다** — 버리면
  '제5조 제3항' 같은 참조가 앞뒤로 붙어 다른 문장이 된다.
"""

from __future__ import annotations

import io
import re
import struct
import zlib

HWP_SIGNATURE = b"HWP Document File"
TAG_PARA_TEXT = 67          # HWPTAG_BEGIN(0x10) + 51
# 본문 안의 제어문자. 표·그림·각주가 여기 들어간다.
# 5 이하는 확장 제어문자라 뒤에 12바이트가 더 붙는다 (규격).
_INLINE_CTRL = set(range(1, 32)) - {9, 10, 13}


class NotHwp(ValueError):
    """HWP 5.0 이 아니다. 조용히 빈 문자열을 주지 않는다 — 왜 못 읽었는지 남긴다."""


def _sections(ole) -> list[bytes]:
    out = []
    for entry in sorted(ole.listdir()):
        if len(entry) == 2 and entry[0] == "BodyText" and \
                entry[1].startswith("Section"):
            out.append((entry[1], ole.openstream(entry).read()))
    out.sort(key=lambda x: int(re.sub(r"\D", "", x[0]) or 0))
    return [b for _, b in out]


def _compressed(ole) -> bool:
    """FileHeader 의 속성 비트 0 — 눌려 있나."""
    try:
        head = ole.openstream("FileHeader").read()
    except Exception:  # noqa: BLE001
        return True
    if len(head) < 40:
        return True
    (flags,) = struct.unpack("<I", head[36:40])
    return bool(flags & 0x01)


def _records(buf: bytes):
    """레코드 스트림을 (tag, level, payload) 로 편다."""
    p, n = 0, len(buf)
    while p + 4 <= n:
        (h,) = struct.unpack("<I", buf[p:p + 4])
        p += 4
        tag = h & 0x3FF
        level = (h >> 10) & 0x3FF
        size = (h >> 20) & 0xFFF
        if size == 0xFFF:
            if p + 4 > n:
                break
            (size,) = struct.unpack("<I", buf[p:p + 4])
            p += 4
        if p + size > n:
            break
        yield tag, level, buf[p:p + size]
        p += size


def _para_text(payload: bytes) -> str:
    """UTF-16LE 문단. 제어문자는 경계로 바꾼다.

    ★ 버리면 안 된다
      표 자리를 그냥 지우면 앞 문장과 뒷 문장이 붙어 버린다.
      '제5조' 와 '제3항' 이 이어져 없는 조문이 만들어진다.
    """
    out: list[str] = []
    i, n = 0, len(payload) - 1
    while i < n:
        (code,) = struct.unpack("<H", payload[i:i + 2])
        if code in _INLINE_CTRL:
            out.append(" ")
            # 확장 제어문자(1~3, 11~12, 14~18, 21~23)는 뒤에 14바이트가 더 있다
            i += 16 if code in (1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23) else 2
            continue
        out.append(chr(code))
        i += 2
    return "".join(out)


def _extract_hwpx(data: bytes) -> list[str]:
    """HWPX — 한글 2010+ 의 XML 형식. zip 안에 Contents/section*.xml 이 있다.

    ★ 드물지만 하필 필요한 곳에 있었다
      규정 607개 중 표본 40개는 전부 HWP(OLE) 였는데
      '성적 평가 지침' 하나가 HWPX 였다. 재수강 규정이 있을 자리다.
      드물다고 안 만들면, 드문 것이 정확히 필요한 것일 때 막힌다.
    """
    import xml.etree.ElementTree as ET
    import zipfile
    out: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = sorted(n for n in z.namelist()
                       if re.match(r"Contents/section\d+\.xml$", n))
        if not names:
            raise NotHwp("HWPX 인데 Contents/section*.xml 이 없다")
        for name in names:
            root = ET.fromstring(z.read(name))
            for para in root.iter():
                tag = para.tag.rsplit("}", 1)[-1]
                if tag != "p":
                    continue
                # 한 문단 안의 텍스트 조각을 순서대로 잇는다
                bits = [t.text or "" for t in para.iter()
                        if t.tag.rsplit("}", 1)[-1] == "t"]
                line = re.sub(r"[ \t]+", " ", "".join(bits)).strip()
                if line:
                    out.append(line)
    return out


def extract(data: bytes) -> list[str]:
    """HWP/HWPX 바이트 → 문단 목록. 원문 그대로, 순서 그대로."""
    import olefile
    if data[:2] == b"PK":
        return _extract_hwpx(data)
    if not data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise NotHwp("OLE 복합문서도 zip 도 아니다 (HWP/HWPX 가 아닐 수 있다)")
    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        head = ole.openstream("FileHeader").read(len(HWP_SIGNATURE))
        if head != HWP_SIGNATURE:
            raise NotHwp(f"서명이 다르다: {head!r}")
        packed = _compressed(ole)
        paras: list[str] = []
        for raw in _sections(ole):
            buf = zlib.decompress(raw, -15) if packed else raw
            for tag, _lv, payload in _records(buf):
                if tag != TAG_PARA_TEXT:
                    continue
                t = _para_text(payload)
                t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", t)
                t = re.sub(r"[ \t]+", " ", t).strip()
                if t:
                    paras.append(t)
        return paras
    finally:
        ole.close()


_ARTICLE = re.compile(r"^제\s*\d+\s*조(\s*의\s*\d+)?")


def articles(paras: list[str]) -> list[tuple[str, list[str]]]:
    """조문 단위로 묶는다 — (제목줄, 본문줄들).

    ★ 조문이 인용 단위다
      '제5조(휴학)' 은 그 자체로 뜻이 서고, 학생이 검증할 수 있는 최소 단위다.
      쪼개면 항·호가 조 없이 떠다니고, 합치면 규정 전체가 한 덩어리가 된다.
    """
    out: list[tuple[str, list[str]]] = []
    cur: tuple[str, list[str]] | None = None
    for p in paras:
        if _ARTICLE.match(p):
            if cur:
                out.append(cur)
            cur = (p, [])
        elif cur:
            cur[1].append(p)
    if cur:
        out.append(cur)
    return out
