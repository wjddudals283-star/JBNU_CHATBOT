"""전북대 규정집이 코퍼스 밖인 이유와, 뚫는 길 — 범위 측정.

    python tools/probe_rules.py

★ 왜 값진가
  절차의 근거가 전부 있다. 개인정보가 없다. 공식 문서다.
  그리고 **안 낡는다** — 오늘 OASIS → JUMP 로 안내 페이지가 통째로 낡았는데,
  학칙은 '총장의 허가를 얻어' 같은 규정 자체라 시스템 이름이 바뀌어도 안 흔들린다.
  오늘 사고의 근본 해법이기도 하다.

★ 왜 못 긁고 있었나
  /web/intro/situation/rule.do 의 원시 HTML 은 껍데기뿐이다.
      - [규정](javascript:;)  - [지침](javascript:;)  - [규정개정예고](javascript:;)
  우리 파서는 이미 정확히 진단해 두었다:
      parse_status=empty · note='HTML 에 본문이 없다 (스크립트로 그리는 페이지일 수 있음)'

★ 뚫는 길 (학사일정 dataAjax.do 와 같은 구조 — 새 기술이 아니다)
  1. rule.do 에서 #Form 의 _csrf 를 받는다
  2. POST /web/intro/situation/rule/listAjax.do  (ruleSe = 탭의 data-rule)
        SC_0000000546 규정 · SC_0000000547 지침 · SC_0000000726 규정개정예고
  3. 응답은 jsonData.push({...}) 를 늘어놓은 JS 다. 편(G) → 규정(R) → 개정이력(F)
  4. 각 규정의 최신본은 cf_download('<암호화 id>') 로 걸려 있고,
     실제 경로는 /async/MultiFile/download.do?file=<id>  (common.js 에 있다)

★ 그런데 본문이 HWP 다
      학칙_2026.07.24(개정).hwp        236KB
      학사운영규정_2026.06.17(개정).hwp   278KB
  첫 바이트가 D0 CF 11 E0 — OLE 복합문서다. 지금 파서로는 한 글자도 못 읽는다.
  HWP 를 여는 것은 **새 작업**이다. 이 도구는 거기까지 가지 않고 범위만 잰다.
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse as up

sys.stdout.reconfigure(encoding="utf-8")

import httpx  # noqa: E402
from selectolax.parser import HTMLParser  # noqa: E402

BASE = "https://www.jbnu.ac.kr"
PAGE = BASE + "/web/intro/situation/rule.do"
LIST = BASE + "/web/intro/situation/rule/listAjax.do"
FILE = BASE + "/async/MultiFile/download.do"
UA = "Mozilla/5.0 (compatible; JBNU-StudentBot/1.0)"

# 학생 질문에 닿을 만한 규정인지 — 이름으로 1차 거른다.
# ★ 이름 기준이라 상한이다. 본문을 봐야 진짜 몇 개인지 안다.
STUDENT = ("학칙", "학사", "수업", "학생", "장학", "졸업", "등록", "휴학",
           "자퇴", "전과", "복수전공", "편입", "성적", "시험")


def tabs(html: str) -> list[tuple[str, str]]:
    d = HTMLParser(html)
    return [(a.attributes.get("data-rule") or "", a.text().strip())
            for a in d.css(".rule-tab-list a") if a.attributes.get("data-rule")]


def form_fields(html: str) -> dict:
    form = HTMLParser(html).css_first("#Form")
    if form is None:
        return {}
    return {i.attributes.get("name"): i.attributes.get("value") or ""
            for i in form.css("input") if i.attributes.get("name")}


def parse_nodes(js: str) -> list[dict]:
    """jsonData.push({...}) 를 이름·파일 짝으로 편다.

    ★ cf_download 는 push 블록 **밖**(앞의 name 변수)에 있다.
      블록 안만 보면 짝이 0개 나온다 — 처음에 그렇게 틀렸다.
    """
    out = []
    segs = js.split("jsonData.push(")
    for i in range(1, len(segs)):
        blk = segs[i][:600]
        nm = re.search(r"'realNm':\s*'([^']*)'", blk)
        grp = re.search(r"'group':\s*'([^']*)'", blk)
        dt = re.search(r"'realDate':\s*'([^']*)'", blk)
        fid = re.search(r"cf_download\(.{0,4}?([A-Za-z0-9+/=]{16,})",
                        segs[i - 1][-1200:])
        if nm:
            out.append({"name": nm.group(1).strip(),
                        "group": grp.group(1) if grp else "",
                        "date": dt.group(1) if dt else "",
                        "file": fid.group(1) if fid else ""})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true",
                    help="파일 하나를 실제로 받아 형식을 확인한다")
    args = ap.parse_args(argv)

    with httpx.Client(timeout=40.0, follow_redirects=True,
                      headers={"User-Agent": UA}, verify=False) as c:
        html = c.get(PAGE).text
        base = form_fields(html)
        print(f"csrf {'있음' if base.get('_csrf') else '없음'} · 탭 {tabs(html)}")
        print()
        total_student = 0
        for se, label in tabs(html):
            data = dict(base, ruleSe=se)
            js = c.post(LIST, data=data,
                        headers={"X-Requested-With": "XMLHttpRequest",
                                 "Referer": PAGE}).text
            nodes = parse_nodes(js)
            rules = [n for n in nodes if n["group"] == "R"]
            files = [n for n in nodes if n["file"]]
            stu = [n for n in rules if any(k in n["name"] for k in STUDENT)]
            total_student += len(stu)
            print(f"── {label}  노드 {len(nodes)} · 규정 {len(rules)} · "
                  f"내려받을 파일 {len(files)}")
            print(f"   학생 질문에 닿을 만한 것 {len(stu)}개 (이름 기준 — 상한이다)")
            for n in stu[:8]:
                print(f"     {n['name'][:34]:36} {n['date']}")
            if args.download and files:
                f = next((x for x in files if x["name"] in ("학칙", "학사운영규정")),
                         files[0])
                r = c.get(FILE, params={"file": f["file"]})
                cd = up.unquote(r.headers.get("content-disposition", ""))
                print(f"   ↓ {f['name']}: {r.status_code} "
                      f"{len(r.content):,}B {cd[-40:]}")
                print(f"     첫 바이트 {r.content[:8]!r}  "
                      f"{'← OLE(HWP)' if r.content[:4] == b'.....'[:0] + bytes([0xD0, 0xCF, 0x11, 0xE0]) else ''}")
            print()
        print(f"★ 학생 관련 규정 합계 {total_student}개 (이름 기준)")
        print("★ 본문은 HWP 다 — 여는 것은 새 작업이다. 여기서는 범위만 잰다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
