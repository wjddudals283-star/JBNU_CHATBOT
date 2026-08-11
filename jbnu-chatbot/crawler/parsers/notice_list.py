"""공지 게시판 목록 파서 — 제목·게시일·링크·게시판만.

★ 구조화하지 않는다
  본문을 읽지 않고, 분류도 추론하지 않는다. 목록에 적힌 것만 그대로 옮긴다.
  게시글은 형식이 제각각이라 구조화하면 틀리기 시작한다.
  학생에게는 '이런 공지가 있고 여기서 볼 수 있다' 까지가 정직한 답이다.

실측 구조 (전수 조사에서 확인)

  학과·기관 CMS (207곳)
    <table class="artclTable">
      <tr><th>번호</th><th>제목</th><th>작성자</th><th>작성일</th>…</tr>
      <tr><td>9</td><td><a class="artclLinkView" href="/bbs/agct/3670/363965/artclView.do">
          제목</a></td><td>관리자</td><td>2025.07.29</td>…</tr>

  본부 www.jbnu.ac.kr
    <div class="com-brd-list-01"><table>
      <tr>…<a onclick="pf_DetailMove('216090')">제목</a>…2026-08-11…</tr>
    링크는 onclick 의 번호로 만든다 → /web/Board/<번호>/detailView.do
    (같은 페이지에 그 주소가 실제 href 로도 들어 있어 규칙을 확인했다)
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse as up
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

SOURCE_KEY = "jbnu_notice_list"
EXTRACTION_METHOD = "html_selector"

_DATE = re.compile(r"(20\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})")
_DETAIL_ID = re.compile(r"pf_DetailMove\(\s*'(\d+)'\s*\)")
# 목록 표의 머리글·안내 문구는 글이 아니다
NOT_A_ROW = ("게시물이", "등록된 게시물", "검색된 게시물", "총 0 건")
# 앵커 안에 섞여 있는 게시판 UI 표시. 제목이 아니라 화면 장식이다.
#   <span class="newArtcl">새글</span>
# 문자열이 아니라 **구조**로 걷어낸다 — CMS 가 문구를 바꿔도 안 깨진다.
UI_MARK_SELECTORS = (".newArtcl", ".ico", ".icon", ".label", ".badge",
                     ".blind", ".hidden", "img")


@dataclass
class NoticeItem:
    title: str
    url: str
    published_at: str | None
    author: str = ""
    category: str = ""          # 목록에 '분류' 칸이 있을 때만. 추론하지 않는다.

    @property
    def key(self) -> str:
        return "ntc-" + hashlib.sha256(self.url.encode("utf-8")).hexdigest()[:16]


@dataclass
class NoticeList:
    board_url: str = ""
    board_name: str = ""
    items: list[NoticeItem] = field(default_factory=list)
    skipped: int = 0            # 링크나 제목이 없어 못 담은 행

    @property
    def counts(self) -> tuple[int, int]:
        return len(self.items), self.skipped


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _date_in(text: str) -> str | None:
    m = _DATE.search(text or "")
    if not m:
        return None
    y, mo, d = m.groups()
    return f"{y}-{int(mo):02d}-{int(d):02d}"


def _row_cells(tr) -> list[str]:
    """문서 순서대로. css('td,th') 는 순서를 섞는다 — 예전에 식단표에서 당했다."""
    return [_norm(c.text()) for c in tr.iter(include_text=False)
            if c.tag in ("td", "th")]


def _link_for(a, base: str) -> str | None:
    href = a.attributes.get("href") or ""
    if href and not href.lower().startswith(("javascript:", "#")):
        return up.urljoin(base, href)
    m = _DETAIL_ID.search(a.attributes.get("onclick") or "")
    if m:
        # 본부 게시판 — onclick 의 번호로 상세 주소를 만든다.
        # 같은 페이지에 이 주소가 실제 href 로도 들어 있어 규칙을 확인했다.
        return up.urljoin(base, f"/web/Board/{m.group(1)}/detailView.do")
    return None


def parse(html: str, *, page_url: str = "") -> NoticeList:
    tree = HTMLParser(html)
    title_node = (tree.css_first("h2 .title") or tree.css_first("h1.com-title-01")
                  or tree.css_first("title"))
    out = NoticeList(board_url=page_url,
                     board_name=_norm(title_node.text() if title_node else ""))

    table = (tree.css_first("table.artclTable")
             or tree.css_first(".com-brd-list-01 table")
             or tree.css_first(".com-brd-list-04 table"))
    if table is None:
        return out

    header = [c.lower() for c in _row_cells(table.css_first("tr"))] \
        if table.css_first("tr") else []
    cat_idx = next((i for i, h in enumerate(header) if "분류" in h), None)
    who_idx = next((i for i, h in enumerate(header) if "작성자" in h), None)

    for tr in table.css("tr"):
        cells = _row_cells(tr)
        joined = " ".join(cells)
        if not cells or any(w in joined for w in NOT_A_ROW):
            continue
        a = tr.css_first("a.artclLinkView") or tr.css_first("a")
        if a is None:
            continue
        for sel in UI_MARK_SELECTORS:
            for n in a.css(sel):
                n.decompose()
        # 제목은 <strong> 안에 있다. 그 앞의 [분류] 는 제목이 아니다.
        strong = a.css_first("strong")
        title = _norm(strong.text()) if strong is not None else _norm(a.text())
        inline_cat = ""
        if strong is not None:
            before = _norm(a.text()).replace(title, "")
            m = re.search(r"\[\s*([^\]]{1,20})\s*\]", before)
            if m:
                inline_cat = _norm(m.group(1))
        url = _link_for(a, page_url or out.board_url)
        if not title or not url:
            out.skipped += 1
            continue
        out.items.append(NoticeItem(
            title=title, url=url, published_at=_date_in(joined),
            author=(cells[who_idx] if who_idx is not None
                    and who_idx < len(cells) else ""),
            category=(cells[cat_idx] if cat_idx is not None
                      and cat_idx < len(cells) else inline_cat)))
    return out


def is_board_page(html: str) -> bool:
    """이 페이지가 게시판 목록인가. 구조로만 판단한다."""
    tree = HTMLParser(html)
    return bool(tree.css_first("table.artclTable")
                or tree.css_first(".com-brd-list-01 table")
                or tree.css_first(".com-brd-list-04 table"))
