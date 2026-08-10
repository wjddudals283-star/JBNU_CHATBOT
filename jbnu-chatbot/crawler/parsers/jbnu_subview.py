"""본부·학과 정적 안내 페이지 파서 (subview.do 계열).

실측 근거 — /web/ 한국어 204페이지 전수 조사 (docs/probe/container_census.json)

    #sp-content 있음        199/204  98%   ← 컨테이너는 1종
      ├ div.com-box-04       40  20%
      ├ 표(table)만         128  63%
      └ 둘 다 없음           31  15%
    h2 의 부모 클래스: div.com-box-01 이 압도적, div.com-box-04 가 그 다음
    table 의 부모:     div.com-tbl-wrap

  표본 두세 개로 종류를 세는 건 관측이 아니라 추측이다. 이 목록은 전수에서 왔다.

★ 색인 단위와 인용 단위를 나눈다
    색인(검색)  잎 노드 / 표의 한 행    "1종 장학금 : 등록금 전액"
    인용(출력)  부모 블록 / 표 전체     "금액별 분류" 전체
  잎까지 색인하고 매칭되면 부모를 인용한다. 부모 관계를 저장해 두면
  인용 정책을 바꿔도 재크롤이 필요 없다.

★ 표도 같은 원리다
    행 단위 색인 + 표 전체 인용.
  행을 따로 인용하면 머리글이 사라져 숫자만 남는다. 맥락 없는 숫자는 오답이다.

★ 요약하지 않는다. 원문을 그대로 옮긴다.
  regulations 요약은 특히 위험하다. 길면 앞부분 + 전문 링크로 자른다.
  text 는 정규화본(매칭용), raw_text 는 줄바꿈을 살린 원문(인용용)이다.

★ 노이즈는 여기서 지우지 않는다.
  '만족도조사결과' 같은 목록을 코드에 박으면 CMS 가 바뀔 때 깨진다.
  여러 페이지에 반복 출현하는 섹션을 crawler/boilerplate.py 가 관측으로 가려낸다.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

from crawler import boilerplate
from crawler.validate import ParseError

SOURCE_KEY = "jbnu_subview"
EXTRACTION_METHOD = "html_selector"
CONFIDENCE = 0.95

CONTENT_SELECTORS = ("#sp-content .com-inner-1300", "#sp-content", "#content")
# 본문 블록 래퍼 — 전수 조사에서 확인된 두 종류. class 토큰으로 비교한다.
BLOCK_CLASSES = ("com-box-01", "com-box-04")
# 본문이 아닌 것 — 네비게이션·유틸·게시판. 구조적으로 확실한 것만 넣는다.
# 게시판(com-brd-list-*)은 별도 공지 크롤러가 담당한다.
DROP_SELECTORS = ("script", "style", "nav", ".dep-list-01", ".dep-list-02",
                  ".dep-list-03", ".util-list", ".breadcrumb", ".sns-list",
                  ".com-page-bottom-wrap-02", ".com-page-bottom-01",
                  ".hide-menu-list", ".com-brd-list-01", ".com-brd-list-04",
                  ".board_layerPop", ".com-btn-box-02")
_LAST_MODIFIED = re.compile(r"최종수정일.{0,200}?(\d{4}-\d{2}-\d{2})", re.S)
MIN_TABLE_ROWS = 2          # 이보다 적으면 레이아웃용 표로 본다


@dataclass
class Section:
    key: str
    page_url: str
    path: list[str]            # ['교내 장학금', '금액별 분류']
    text: str                  # 정규화본 — 매칭·해시용
    depth: int
    ordinal: int
    is_leaf: bool
    raw_text: str = ""         # 줄바꿈을 살린 원문 — 인용용 (비면 text 를 쓴다)
    kind: str = "list"         # list | table | table_row | block
    parent_key: str | None = None
    quote_key: str | None = None      # 인용할 블록 (보통 부모, 표 행이면 표)
    applies_to: str | None = None     # 적용 조건 — (학과×입학년도) 등
    block_hash: str = ""              # 최상위 블록 단위 해시
    section_hash: str = ""            # 이 섹션 자체의 해시 — 보일러플레이트 판정용

    @property
    def path_text(self) -> str:
        return " > ".join(self.path)

    @property
    def quote_text(self) -> str:
        return self.raw_text or self.text


@dataclass
class ParseResult:
    page_url: str = ""
    title: str = ""
    last_modified: str | None = None
    sections: list[Section] = field(default_factory=list)
    quarantined: list[tuple[object, str]] = field(default_factory=list)
    pruned: dict = field(default_factory=dict)   # 보일러플레이트 제거 보고
    # ingest 호환
    meals: list = field(default_factory=list)
    anchors: list = field(default_factory=list)
    week_start: str = ""

    @property
    def counts(self) -> tuple[int, int]:
        return len(self.sections), len(self.quarantined)

    @property
    def leaves(self) -> list[Section]:
        return [s for s in self.sections if s.is_leaf]

    @property
    def by_key(self) -> dict[str, Section]:
        return {s.key: s for s in self.sections}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", text).encode("utf-8")).hexdigest()[:16]


def _classes(node) -> set[str]:
    return set((node.attributes.get("class") or "").split())


def _direct_text(node) -> str:
    """중첩 <ul>/<ol>/<table> 을 뺀 자기 자신의 텍스트.

    <li><font>금액별 분류</font><ul>…</ul></li> 에서 '금액별 분류' 만 얻는다.
    """
    parts = []
    for child in node.iter(include_text=True):
        if child.tag in ("ul", "ol", "table"):
            continue
        parts.append(child.text() if child.tag != "-text" else (child.text() or ""))
    return _norm(" ".join(p for p in parts if p))


def _section_key(page_url: str, path: list[str], ordinal: int) -> str:
    raw = f"{page_url}|{'>'.join(path)}|{ordinal}"
    return "sec-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _find_blocks(body):
    """본문 블록을 문서 순서대로 찾는다.

    css('a, b') 는 문서 순서를 보장하지 않는다 — 예전에 td/th 에서 당한 적이 있다.
    traverse() 는 전위 순회라 순서가 보장되고, 부모가 먼저 나온다.
    """
    chosen, chosen_ids = [], set()
    for n in body.traverse(include_text=False):
        if n.tag != "div" or not (_classes(n) & set(BLOCK_CLASSES)):
            continue
        p = n.parent
        nested = False
        while p is not None:
            if id(p) in chosen_ids:
                nested = True
                break
            p = p.parent
        if not nested:
            chosen.append(n)
            chosen_ids.add(id(n))
    return chosen


# ---------------------------------------------------------------- 표

def _row_cells(row) -> list[str]:
    """한 행의 셀을 문서 순서대로. css('td,th') 는 순서를 섞는다."""
    return [_norm(c.text()) for c in row.iter(include_text=False)
            if c.tag in ("td", "th")]


def _table_rows(table) -> list[list[str]]:
    rows = []
    for tr in table.css("tr"):
        cells = _row_cells(tr)
        if any(c for c in cells):
            rows.append(cells)
    return rows


def _table_label(table, fallback: str) -> str:
    cap = table.css_first("caption")
    if cap is not None and _norm(cap.text()):
        return _norm(cap.text())
    summ = _norm(table.attributes.get("summary") or "")
    if summ:
        return summ
    # 바로 앞의 제목성 형제
    prev = table.parent
    while prev is not None:
        sib = prev.prev
        while sib is not None:
            if sib.tag in ("h2", "h3", "h4", "h5", "strong", "p"):
                t = _norm(sib.text())
                if t:
                    return t
            sib = sib.prev
        prev = prev.parent
        if prev is not None and _classes(prev) & set(BLOCK_CLASSES):
            break
    return fallback


def _render_table(rows: list[list[str]]) -> str:
    """표를 줄바꿈 있는 원문으로. 요약하지 않고 그대로 옮긴다."""
    return "\n".join(" | ".join(c for c in r) for r in rows)


def _emit_table(table, result: ParseResult, page_url: str, path: list[str],
                depth: int, ordinal: int, *, parent_key: str,
                block_hash: str) -> int:
    rows = _table_rows(table)
    if len(rows) < MIN_TABLE_ROWS:
        return ordinal          # 레이아웃용 표 — 본문이 아니다

    label = _table_label(table, path[-1] if path else "표")
    my_path = path + [label] if label not in path else list(path)
    raw = _render_table(rows)
    key = _section_key(page_url, my_path, ordinal)

    # 표 전체 = 인용 단위
    result.sections.append(Section(
        key=key, page_url=page_url, path=my_path, text=_norm(raw), raw_text=raw,
        depth=depth, ordinal=ordinal, is_leaf=False, kind="table",
        parent_key=parent_key, quote_key=key,
        block_hash=block_hash, section_hash=_hash(raw)))
    ordinal += 1

    # 각 행 = 색인 단위. 인용은 표 전체로 간다 (머리글 없는 숫자는 오답이다)
    header = rows[0]
    body_rows = rows[1:] if len(rows) > 1 else []
    for r in body_rows:
        line = " | ".join(c for c in r)
        if not _norm(line):
            continue
        row_path = my_path + ([r[0]] if r and _norm(r[0]) else [])
        result.sections.append(Section(
            key=_section_key(page_url, row_path, ordinal), page_url=page_url,
            path=row_path, text=_norm(line), raw_text=line,
            depth=depth + 1, ordinal=ordinal, is_leaf=True, kind="table_row",
            parent_key=key, quote_key=key,
            block_hash=block_hash, section_hash=_hash(line)))
        ordinal += 1
    _ = header      # 머리글은 표 인용에 이미 들어 있다. 행에 억지로 붙이지 않는다.
    return ordinal


# ---------------------------------------------------------------- 본체

def content_root(html: str):
    """본문 컨테이너를 찾아 네비게이션·게시판을 걷어낸 노드를 돌려준다.

    조각 수집과 파싱이 **같은 것**을 보게 하려면 이 함수를 공유해야 한다.
    보는 대상이 다르면 탐지한 해시가 파싱 때 안 맞는다.
    """
    tree = HTMLParser(html)
    body = None
    for sel in CONTENT_SELECTORS:
        body = tree.css_first(sel)
        if body is not None:
            break
    if body is None:
        raise ParseError(f"본문 컨테이너를 찾지 못했다 (selectors={CONTENT_SELECTORS})")

    for sel in DROP_SELECTORS:
        for n in body.css(sel):
            n.decompose()

    # 접근 거부 판정은 **스크립트를 걷어낸 본문 텍스트**로 한다.
    # 원문 전체에서 문자열을 찾으면 JS 안의 alert('접근권한이 없습니다…') 에 걸린다.
    # 실제로 107페이지 중 18개를 그렇게 오탐했다. 조건문 속 문구는 관측이 아니다.
    if re.search(r"접근이 거부|접근권한이 없", body.text() or ""):
        raise ParseError("403 — 접근 거부 페이지")
    return tree, body


def page_fragments(html: str) -> dict[str, str]:
    """보일러플레이트 1차 수집용 — 이 페이지 본문의 조각 해시."""
    _, body = content_root(html)
    return boilerplate.fragments(body)


def parse(html: str, *, page_url: str = "",
          boilerplate_report=None) -> ParseResult:
    """boilerplate_report 를 주면 파싱 **전에** 템플릿 조각을 DOM 에서 잘라낸다."""
    tree, body = content_root(html)
    prune_info = {"pruned": 0, "held": 0, "chars_removed": 0}
    if boilerplate_report is not None:
        prune_info = boilerplate.prune(body, boilerplate_report)

    title_node = tree.css_first("h1.com-title-01") or tree.css_first("title")
    result = ParseResult(page_url=page_url,
                         title=_norm(title_node.text() if title_node else ""))
    m = _LAST_MODIFIED.search(html)
    result.last_modified = m.group(1) if m else None
    result.pruned = prune_info

    ordinal = 0
    blocks = _find_blocks(body) or [body]
    for block in blocks:
        head_node = block.css_first("h2 .title") or block.css_first("h2")
        heading = _norm(head_node.text() if head_node else "") or result.title
        if head_node is not None:
            head_node.decompose()

        block_text = _norm(block.text())
        if not block_text:
            continue
        root_key = _section_key(page_url, [heading], ordinal)
        bhash = _hash(block_text)

        result.sections.append(Section(
            key=root_key, page_url=page_url, path=[heading], text=block_text,
            raw_text=block_text, depth=0, ordinal=ordinal, is_leaf=False,
            kind="block", quote_key=root_key,
            block_hash=bhash, section_hash=bhash))
        ordinal += 1

        # 표 먼저. 처리한 표는 떼어내서 리스트 순회에 섞이지 않게 한다.
        for table in list(block.css("table")):
            ordinal = _emit_table(table, result, page_url, [heading], 1, ordinal,
                                  parent_key=root_key, block_hash=bhash)
            table.decompose()

        seen_ul: set[int] = set()
        for ul in block.traverse(include_text=False):
            if ul.tag not in ("ul", "ol") or id(ul) in seen_ul:
                continue
            # 중첩 <ul> 은 _walk 가 재귀로 처리한다. 최상위만 시작점으로 삼는다.
            p, nested = ul.parent, False
            while p is not None and p is not block:
                if p.tag in ("ul", "ol", "li"):
                    nested = True
                    break
                p = p.parent
            if nested:
                continue
            seen_ul.add(id(ul))
            ordinal = _walk(ul, result, page_url, [heading], 1, ordinal,
                            parent_key=root_key, quote_key=root_key,
                            block_hash=bhash, seen_ul=seen_ul)

    if not result.sections:
        raise ParseError("섹션을 하나도 만들지 못했다 (셀렉터 깨짐 의심)")
    return result


def _walk(ul, result: ParseResult, page_url: str, path: list[str], depth: int,
          ordinal: int, *, parent_key: str, quote_key: str,
          block_hash: str, seen_ul: set[int]) -> int:
    for li in [n for n in ul.iter(include_text=False) if n.tag == "li"]:
        own = _direct_text(li)
        nested = [n for n in li.iter(include_text=False) if n.tag in ("ul", "ol")]
        for n in nested:
            seen_ul.add(id(n))
        if not own and not nested:
            continue

        my_path = path + ([own] if own and nested else [])
        key = _section_key(page_url, my_path or path, ordinal)

        if nested:
            # 가지 — 이 노드가 아래 잎들의 **인용 단위**가 된다
            raw = _norm(li.text())
            result.sections.append(Section(
                key=key, page_url=page_url, path=my_path, text=raw, raw_text=raw,
                depth=depth, ordinal=ordinal, is_leaf=False, kind="list",
                parent_key=parent_key, quote_key=key,
                block_hash=block_hash, section_hash=_hash(raw)))
            ordinal += 1
            for sub in nested:
                ordinal = _walk(sub, result, page_url, my_path, depth + 1,
                                ordinal, parent_key=key, quote_key=key,
                                block_hash=block_hash, seen_ul=seen_ul)
        else:
            # 잎 — 색인 단위. 인용은 부모 블록으로 간다
            result.sections.append(Section(
                key=key, page_url=page_url, path=path + [own], text=own,
                raw_text=own, depth=depth, ordinal=ordinal, is_leaf=True,
                kind="list", parent_key=parent_key, quote_key=quote_key,
                block_hash=block_hash, section_hash=_hash(own)))
            ordinal += 1
    return ordinal
