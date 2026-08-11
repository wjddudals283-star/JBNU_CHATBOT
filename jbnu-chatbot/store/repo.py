"""저장소 계층 — upsert / 신선도 질의.

불변 규칙 (02_핸드오프.md §2)
  · 출처 메타 6개 NOT NULL — 스키마가 강제. 여기서는 얕은 삽입 헬퍼를 제공하지 않는다
  · T4 는 valid_to / author / approved_by NOT NULL — 스키마가 강제
  · 검증 실패는 삭제가 아니라 status='quarantine'
  · **조회 쿼리는 항상 status='verified' 필터. 예외 없음**
      → 관례로 두지 않고 _fact_select() 를 통해서만 fact 를 읽도록 강제한다.
        status 를 직접 조건에 넣으려 하면 예외를 던진다.

추론 금지 (01_설계.md §5)
  · 가격 조인은 name_normalized 완전 일치만. 유사도·부분일치 금지. 미매칭은 NULL
  · service_status 는 원천 관측값만. 여기서 유추해 채우지 않는다
"""

from __future__ import annotations

import datetime as dt
import pathlib
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

SCHEMA_PATH = pathlib.Path(__file__).with_name("schema.sql")

VERIFIED = "verified"

# 신선도 게이트 (01_설계.md §6). 단위: 시간
MAX_STALENESS_HOURS: dict[str, float] = {
    "meal_service": 24,
    "notice": 6,
    "pledge_progress": 30 * 24,
    "operating_hours": 30 * 24,
    "menu_price": 30 * 24,
    # 학사일정은 자주 안 바뀌지만, 크롤이 멈춘 걸 숨기면 안 된다.
    "academic_calendar": 30 * 24,
    "procedure": 180 * 24,
    "contact": 180 * 24,
    "place": 180 * 24,
}

MEAL_TYPES = ("breakfast", "lunch", "dinner")

# 원천 표기 → 정규 meal_type. 원천 어휘가 셋이라 셋 다 싣는다.
MEAL_TYPE_FROM_SOURCE: dict[str, str] = {
    # 생협 JSON / 학교 XHR
    "조식": "breakfast", "점심": "lunch", "석식": "dinner",
    # 생활관
    "아침": "breakfast", "저녁": "dinner",
    # 사용자 발화용 (오픈빌더 엔티티로도 내보낸다)
    "중식": "lunch", "브런치": "lunch",
}


# ═══════════════════════════════════════════════════════════════
# 연결
# ═══════════════════════════════════════════════════════════════

def connect(db_path: str | pathlib.Path = ":memory:") -> sqlite3.Connection:
    """SQLite 연결.

    ★ SQLite 는 외래키가 연결마다 기본 OFF 다. 끄고 쓰면 REFERENCES 가
      주석이나 다름없어진다. 여기서 반드시 켠다.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection, schema_path: pathlib.Path | None = None) -> None:
    conn.executescript((schema_path or SCHEMA_PATH).read_text(encoding="utf-8"))
    conn.commit()


def foreign_keys_on(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])


# ═══════════════════════════════════════════════════════════════
# 식별자 · 정규화
# ═══════════════════════════════════════════════════════════════

_SLUG_STRIP = re.compile(r"[\s/\\?#\[\]@!$&'()*+,;=]+")


def _slug(value: str) -> str:
    v = unicodedata.normalize("NFKC", (value or "").strip())
    v = _SLUG_STRIP.sub("-", v).strip("-")
    return v


def facility_local(facility_id: str) -> str:
    """'jbnu:facility/후생관-푸드코트' → '후생관-푸드코트'"""
    return facility_id.rsplit("/", 1)[-1]


def meal_service_id(facility_id: str, date: str, meal_type: str,
                    zone: str = "", corner: str = "") -> str:
    """결정론적 IRI. 같은 것을 두 번 수집해도 같은 ID → upsert 되고 중복이 없다."""
    base = f"jbnu:meal/{date}/{facility_local(facility_id)}/{meal_type}"
    tail = "/".join(_slug(x) for x in (zone, corner) if x)
    return f"{base}#{tail}" if tail else base


def menu_item_id(meal_service_id_: str, display_order: int) -> str:
    return f"{meal_service_id_}/item/{display_order:02d}"


def menu_price_id(facility_id: str, name_normalized: str, audience: str,
                  valid_from: str) -> str:
    """단가는 개정된다 → valid_from 이 식별자에 들어간다 (시계열 규칙)."""
    aud = _slug(audience) or "all"
    return f"jbnu:price/{facility_local(facility_id)}/{_slug(name_normalized)}/{aud}/{valid_from}"


# ═══════════════════════════════════════════════════════════════
# 시계열 테이블 식별자
#
# ★ 이력을 쌓는 테이블은 식별자에 **시점**이 있어야 한다.
#   같은 대상의 다른 시점 관측은 다른 레코드다.
#   시점이 빠지면 이튿날 크롤에서 PK 가 충돌해 파이프라인이 죽는다.
#   당일 테스트로는 절대 안 잡힌다 — 다른 날짜로 두 번 넣어봐야 드러난다.
#
#   시점 필요:  operating_hours / menu_price / pledge_progress / procedure
#   이미 안전:  meal_service·menu_item(ID 에 date 포함) / notice(published_at)
# ═══════════════════════════════════════════════════════════════

TIME_SERIES_TABLES = ("operating_hours", "menu_price", "pledge_progress", "procedure")


def procedure_id(title: str, valid_from: str) -> str:
    """절차·요강은 개정된다. 개정본은 이전 것과 별개 레코드다."""
    return f"jbnu:procedure/{_slug(title)}/{valid_from}"


def academic_calendar_id(ac_year: int, ac_semester: int, start_date: str,
                         title: str, valid_from: str) -> str:
    """학사일정은 개정된다 → 시계열 규칙에 따라 valid_from 이 들어간다."""
    return (f"jbnu:calendar/{ac_year}/{ac_semester}/{start_date}"
            f"/{_slug(title)[:40]}/{valid_from}")


def upsert_calendar(conn: sqlite3.Connection, entry, meta: SourceMeta) -> str:
    m = meta.as_row()
    cid = academic_calendar_id(entry.ac_year, entry.ac_semester,
                               entry.start_date, entry.title, m["valid_from"])
    conn.execute(
        """
        INSERT INTO academic_calendar
          (id, ac_year, ac_semester, title, start_date, end_date, raw_text,
           source_id, source_url, observed_at, valid_from, valid_to,
           confidence, extraction_method, status, tier)
        VALUES (:id,:y,:s,:title,:start,:end,:raw,
                :source_id,:source_url,:observed_at,:valid_from,:valid_to,
                :confidence,:extraction_method,:status,:tier)
        ON CONFLICT(ac_year, ac_semester, start_date, title, valid_from)
        DO UPDATE SET end_date = excluded.end_date, raw_text = excluded.raw_text,
                      source_id = excluded.source_id,
                      observed_at = excluded.observed_at,
                      status = excluded.status
        """,
        {"id": cid, "y": entry.ac_year, "s": entry.ac_semester,
         "title": entry.title, "start": entry.start_date, "end": entry.end_date,
         "raw": entry.raw_text, **m},
    )
    return cid


def query_calendar(conn: sqlite3.Connection, *, since: str, until: str,
                   limit: int = 50) -> list[dict[str, Any]]:
    """기간에 걸치는 일정. 진행 중인 것도 포함한다.

    (start <= until) AND (COALESCE(end, start) >= since)
    """
    rows = _fact_select(
        conn, "academic_calendar",
        "start_date <= ? AND COALESCE(end_date, start_date) >= ?",
        (until, since),
    )
    # 같은 (start, title) 이 valid_from 별로 여러 벌이면 최신 관측만 쓴다
    best: dict[tuple, sqlite3.Row] = {}
    for r in rows:
        key = (r["start_date"], r["title"])
        cur = best.get(key)
        if cur is None or r["valid_from"] > cur["valid_from"]:
            best[key] = r
    out = [dict(r) for r in best.values()]
    out.sort(key=lambda r: (r["start_date"], r["title"]))
    return out[:limit]


def pledge_progress_id(pledge_id_: str, valid_from: str) -> str:
    """공약 진행상황은 갱신될 때마다 새 관측이다.

    이전 상태를 덮어쓰면 "언제 진행중에서 완료로 바뀌었나"를 답할 수 없게 된다.
    투명성이 기능인 도메인에서 이력 소실은 특히 비싸다.
    """
    num = pledge_id_.rsplit("/", 2)
    tail = "/".join(num[-2:]) if len(num) >= 2 else _slug(pledge_id_)
    return f"jbnu:pledge-progress/{tail}/{valid_from}"


_WS = re.compile(r"\s+")
_PARENS = re.compile(r"[()\[\]{}（）［］｛｝]")


def normalize_name(name: str) -> str:
    """가격 조인 키.

    계약(§2 가격 조인 규칙): "정규화는 공백·괄호 제거 수준까지만.
    `통`, `신` 같은 접두어를 벗기는 규칙을 만들지 말 것."

    구현 판단 — **괄호 문자만 지우고 괄호 안 내용은 남긴다.**
    내용까지 지우면 '오므라이스류(기본)' → '오므라이스류' 가 되어
    서로 다른 상품이 같은 키로 접힌다. 그건 없는 가격을 만드는 경로다.
    이 함수는 공백·괄호·유니코드 표기 차이만 흡수한다. 그 이상은 추론이다.

    menu_item.name_normalized 와 menu_price.name_normalized 는
    반드시 이 함수 하나만 쓴다.
    """
    v = unicodedata.normalize("NFKC", (name or "").strip())
    v = _PARENS.sub("", v)
    v = _WS.sub("", v)
    return v


# ═══════════════════════════════════════════════════════════════
# 출처 메타
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SourceMeta:
    """모든 fact 에 붙는 공통 출처 메타. 빠뜨릴 수 없게 한 덩어리로 다닌다."""
    source_id: str
    source_url: str
    observed_at: str
    confidence: float
    extraction_method: str
    tier: str
    valid_from: str = ""
    valid_to: str | None = None
    status: str = VERIFIED

    def as_row(self, *, with_validity: bool = True) -> dict[str, Any]:
        row = {
            "source_id": self.source_id,
            "source_url": self.source_url,
            "observed_at": self.observed_at,
            "confidence": self.confidence,
            "extraction_method": self.extraction_method,
            "status": self.status,
            "tier": self.tier,
        }
        if with_validity:
            row["valid_from"] = self.valid_from or self.observed_at[:10]
            row["valid_to"] = self.valid_to
        return row


def insert_snapshot(conn: sqlite3.Connection, *, id: str, source_key: str, url: str,
                    fetched_at: str, http_status: int | None, content_hash: str,
                    content_path: str, media_type: str,
                    stable_hash: str = "") -> str:
    conn.execute(
        """INSERT OR REPLACE INTO source_snapshot
           (id, source_key, url, fetched_at, http_status, content_hash,
            stable_hash, content_path, media_type)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (id, source_key, url, fetched_at, http_status, content_hash,
         stable_hash, content_path, media_type),
    )
    return id


def last_snapshot(conn: sqlite3.Connection, source_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM source_snapshot WHERE source_key = ? ORDER BY fetched_at DESC LIMIT 1",
        (source_key,),
    ).fetchone()


# ═══════════════════════════════════════════════════════════════
# upsert — meal_service / menu_item
# ═══════════════════════════════════════════════════════════════

@dataclass
class ParsedItem:
    name: str
    category: str | None = None
    display_order: int = 0
    allergens: str | None = None
    is_vegetarian: bool = False


@dataclass
class ParsedMeal:
    """파서 출력 계약 (02 §3 개정판).

    계약의 dataclass 대비 변경 — 스키마와 맞춘 것이다.
      · zone 추가 (원천 코너가 2단이다)
      · corner: str|None → str (스키마가 NOT NULL DEFAULT '')
      · ParsedItem.price 삭제 (가격은 menu_price 로 분리됨)
    """
    facility_id: str
    date: str
    meal_type: str
    service_status: str
    zone: str = ""
    corner: str = ""
    items: list[ParsedItem] = field(default_factory=list)
    note: str | None = None
    # 분할 전 원본 셀 텍스트. 구분자 규칙을 바꿔도 재크롤 없이 다시 쪼갤 수 있다.
    raw_text: str | None = None

    def natural_key(self) -> tuple[str, str, str, str, str]:
        return (self.facility_id, self.date, self.meal_type, self.zone, self.corner)


def upsert_meal(conn: sqlite3.Connection, meal: ParsedMeal, meta: SourceMeta) -> str:
    """meal_service 1건 + 그 하위 menu_item 전체를 원자적으로 반영한다.

    · 충돌 대상은 자연키 UNIQUE(facility_id, date, meal_type, zone, corner).
      zone/corner 가 NOT NULL DEFAULT '' 라서 이 UNIQUE 가 실제로 동작한다 (T17).
    · menu_item 은 삭제 후 재삽입한다. 항목이 줄었을 때(5→3) 이전 항목이
      남아 답변에 섞이는 것을 막는다.
    """
    msid = meal_service_id(meal.facility_id, meal.date, meal.meal_type,
                           meal.zone, meal.corner)
    m = meta.as_row()

    conn.execute(
        """
        INSERT INTO meal_service
          (id, facility_id, date, meal_type, service_status, zone, corner,
           raw_text, note,
           source_id, source_url, observed_at, valid_from, valid_to,
           confidence, extraction_method, status, tier)
        VALUES
          (:id, :facility_id, :date, :meal_type, :service_status, :zone, :corner,
           :raw_text, :note,
           :source_id, :source_url, :observed_at, :valid_from, :valid_to,
           :confidence, :extraction_method, :status, :tier)
        ON CONFLICT(facility_id, date, meal_type, zone, corner) DO UPDATE SET
           service_status    = excluded.service_status,
           raw_text          = excluded.raw_text,
           note              = excluded.note,
           source_id         = excluded.source_id,
           source_url        = excluded.source_url,
           observed_at       = excluded.observed_at,
           valid_from        = excluded.valid_from,
           valid_to          = excluded.valid_to,
           confidence        = excluded.confidence,
           extraction_method = excluded.extraction_method,
           status            = excluded.status,
           tier              = excluded.tier
        """,
        {"id": msid, "facility_id": meal.facility_id, "date": meal.date,
         "meal_type": meal.meal_type, "service_status": meal.service_status,
         "zone": meal.zone, "corner": meal.corner, "note": meal.note,
         "raw_text": meal.raw_text, **m},
    )

    # 갱신 시 id 가 기존 행의 것으로 유지되도록 실제 id 를 다시 읽는다.
    row = conn.execute(
        """SELECT id FROM meal_service
           WHERE facility_id=? AND date=? AND meal_type=? AND zone=? AND corner=?""",
        meal.natural_key(),
    ).fetchone()
    msid = row["id"]

    conn.execute("DELETE FROM menu_item WHERE meal_service_id = ?", (msid,))
    im = meta.as_row(with_validity=False)
    for i, it in enumerate(meal.items):
        order = it.display_order if it.display_order else i
        conn.execute(
            """INSERT INTO menu_item
                 (id, meal_service_id, name, name_normalized, category, allergens,
                  is_vegetarian, display_order,
                  source_id, source_url, observed_at, confidence,
                  extraction_method, status, tier)
               VALUES (:id, :msid, :name, :norm, :category, :allergens,
                       :veg, :order,
                       :source_id, :source_url, :observed_at, :confidence,
                       :extraction_method, :status, :tier)""",
            {"id": menu_item_id(msid, order), "msid": msid, "name": it.name,
             "norm": normalize_name(it.name), "category": it.category,
             "allergens": it.allergens, "veg": int(it.is_vegetarian),
             "order": order, **im},
        )
    return msid


def operating_hours_id(facility_id: str, term: str, weekday: int,
                       meal_type: str, valid_from: str) -> str:
    """★ valid_from 이 식별자에 들어가야 한다.

    운영시간은 주간 크롤로 **이력이 쌓이는** 데이터다. valid_from 을 빼면
    이튿날 크롤에서 PK 가 충돌해 크롤 자체가 죽는다 (UNIQUE 에는 valid_from 이
    있는데 PK 에는 없어서 생기는 불일치다).
    """
    return (f"jbnu:hours/{facility_local(facility_id)}/{_slug(term)}"
            f"/{weekday}/{meal_type or 'all'}/{valid_from}")


def upsert_hours(conn: sqlite3.Connection, *, facility_id: str, term: str,
                 weekday: int, meal_type: str, is_closed: bool,
                 meta: SourceMeta, open_time: str | None = None,
                 close_time: str | None = None, note: str | None = None) -> str:
    m = meta.as_row()
    hid = operating_hours_id(facility_id, term, weekday, meal_type, m["valid_from"])
    conn.execute(
        """
        INSERT INTO operating_hours
          (id, facility_id, term, weekday, meal_type, is_closed,
           open_time, close_time, note,
           source_id, source_url, observed_at, valid_from, valid_to,
           confidence, extraction_method, status, tier)
        VALUES (:id,:fid,:term,:wd,:meal,:closed,:open,:close,:note,
                :source_id,:source_url,:observed_at,:valid_from,:valid_to,
                :confidence,:extraction_method,:status,:tier)
        ON CONFLICT(facility_id, term, weekday, meal_type, valid_from) DO UPDATE SET
           is_closed = excluded.is_closed, open_time = excluded.open_time,
           close_time = excluded.close_time, note = excluded.note,
           source_id = excluded.source_id, observed_at = excluded.observed_at,
           confidence = excluded.confidence, status = excluded.status
        """,
        {"id": hid, "fid": facility_id, "term": term, "wd": weekday,
         "meal": meal_type, "closed": int(is_closed), "open": open_time,
         "close": close_time, "note": note, **m},
    )
    return hid


def set_hours_coverage(conn: sqlite3.Connection, facility_id: str,
                       coverage: str) -> None:
    """폐쇄세계 가정을 켜는 유일한 통로.

    'complete' 는 **시간표 전체를 파싱한 크롤러만** 세울 수 있다.
    일부만 넣고 complete 로 두면 "행이 없다"가 곧 "안 한다"가 되어
    운영 중인 끼니를 미운영으로 답하게 된다.
    """
    if coverage not in ("complete", "partial"):
        raise ValueError(f"hours_coverage 는 complete|partial: {coverage!r}")
    conn.execute("UPDATE facility SET hours_coverage = ? WHERE id = ?",
                 (coverage, facility_id))


AUDIENCE_ALL = "전체"


def upsert_price(conn: sqlite3.Connection, *, facility_id: str, name: str,
                 price_text: str, price_min: int, meta: SourceMeta,
                 price_max: int | None = None, audience: str = AUDIENCE_ALL,
                 category: str | None = None, corner: str | None = None,
                 note: str | None = None) -> str:
    """단가표 1행.

    price_text 는 원문 표기 그대로 저장한다. 답변은 이걸 렌더한다.
    price_min/price_max 는 검증·정렬 전용이며 답변에 직접 쓰지 않는다.
    """
    norm = normalize_name(name)
    m = meta.as_row()
    pid = menu_price_id(facility_id, norm, audience, m["valid_from"])
    conn.execute(
        """
        INSERT INTO menu_price
          (id, facility_id, name, name_normalized, category, corner,
           price_text, price_min, price_max, audience, note,
           source_id, source_url, observed_at, valid_from, valid_to,
           confidence, extraction_method, status, tier)
        VALUES (:id, :fid, :name, :norm, :category, :corner,
                :price_text, :price_min, :price_max, :audience, :note,
                :source_id, :source_url, :observed_at, :valid_from, :valid_to,
                :confidence, :extraction_method, :status, :tier)
        ON CONFLICT(facility_id, name_normalized, audience, valid_from) DO UPDATE SET
           price_text = excluded.price_text, price_min = excluded.price_min,
           price_max = excluded.price_max, note = excluded.note,
           category = excluded.category, corner = excluded.corner,
           source_id = excluded.source_id, observed_at = excluded.observed_at,
           confidence = excluded.confidence, status = excluded.status
        """,
        {"id": pid, "fid": facility_id, "name": name, "norm": norm,
         "category": category, "corner": corner, "price_text": price_text,
         "price_min": price_min, "price_max": price_max, "audience": audience,
         "note": note, **m},
    )
    return pid


# ═══════════════════════════════════════════════════════════════
# 조회 — status='verified' 강제
# ═══════════════════════════════════════════════════════════════

class VerifiedFilterBypass(RuntimeError):
    """조회에서 status 필터를 우회하려는 시도. 불변 규칙 위반이다."""


def _fact_select(conn: sqlite3.Connection, table: str, where: str,
                 params: Sequence[Any]) -> list[sqlite3.Row]:
    """모든 fact 조회의 단일 통로. status='verified' 를 여기서 붙인다.

    호출자가 where 에 status 를 직접 넣으면 예외. 관례가 아니라 코드로 막는다.
    """
    if re.search(r"\bstatus\b", where):
        raise VerifiedFilterBypass(
            f"status 조건을 직접 넣지 마라 (table={table}). _fact_select 가 붙인다."
        )
    sql = f"SELECT * FROM {table} WHERE status = ? AND ({where})"
    return conn.execute(sql, (VERIFIED, *params)).fetchall()


def _parse_ts(value: str) -> dt.datetime:
    v = (value or "").strip().replace("Z", "+00:00")
    d = dt.datetime.fromisoformat(v)
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def staleness_hours(observed_at: str, now: dt.datetime) -> float:
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    return (now - _parse_ts(observed_at)).total_seconds() / 3600.0


@dataclass
class MealFacts:
    """조회 결과. A/B/C 분기 판단은 스킬 계층이 한다 — 여기서는 사실만 준다."""
    rows: list[dict[str, Any]]
    stale: bool
    max_age_hours: float | None
    expired_t4: bool = False

    @property
    def found(self) -> bool:
        return bool(self.rows)


def query_meal(conn: sqlite3.Connection, *, facility_id: str, date: str,
               meal_type: str, now: dt.datetime,
               max_staleness_h: float | None = None) -> MealFacts:
    """식단 조회 + 신선도 게이트.

    반환 rows 는 zone/corner 단위다. 후생관은 한 끼니에 10개 이상 나온다.
    가격은 여기서 붙이지 않는다 — attach_prices() 를 명시적으로 호출해야 한다.
    """
    limit = MAX_STALENESS_HOURS["meal_service"] if max_staleness_h is None else max_staleness_h
    meals = _fact_select(
        conn, "meal_service",
        "facility_id = ? AND date = ? AND meal_type = ? "
        "AND (valid_to IS NULL OR valid_to >= ?)",
        (facility_id, date, meal_type, date),
    )
    if not meals:
        return MealFacts(rows=[], stale=False, max_age_hours=None)

    ages = [staleness_hours(r["observed_at"], now) for r in meals]
    max_age = max(ages)

    out: list[dict[str, Any]] = []
    for r in sorted(meals, key=lambda x: (x["zone"], x["corner"])):
        items = _fact_select(conn, "menu_item", "meal_service_id = ?", (r["id"],))
        rec = dict(r)
        rec["items"] = [dict(i) for i in sorted(items, key=lambda x: x["display_order"])]
        out.append(rec)

    return MealFacts(rows=out, stale=max_age > limit, max_age_hours=max_age)


def query_operating_hours(conn: sqlite3.Connection, *, facility_id: str,
                          meal_type: str | None = None,
                          on_date: str | None = None) -> list[dict[str, Any]]:
    """운영시간 조회.

    ★ 이걸 식단 파싱에 쓰면 안 된다. service_status 는 원천 관측값만이다(§5).
      "진수원은 아침을 운영하지 않아요" 같은 답변을 **답변 시점에** 만들기 위한 것이고,
      크롤 시점에 meal_service 레코드를 만들거나 지우는 근거로 쓰지 않는다.

    ★ 주간 크롤로 관측 이력이 쌓이면 같은 (요일, 끼니) 에 valid_from 이 다른
      행이 여러 개 생긴다. 이력은 보존하되 **질의에는 그 날짜에 적용되는
      가장 최근 관측 한 벌만** 쓴다. 안 그러면 개강 전 시간표와 개강 후 시간표가
      동시에 답을 주장한다.
    """
    where = "facility_id = ?"
    params: list[Any] = [facility_id]
    if meal_type:
        where += " AND meal_type = ?"
        params.append(meal_type)
    rows = [dict(r) for r in _fact_select(conn, "operating_hours", where, params)]
    if on_date is None or not rows:
        return rows

    applicable = [r for r in rows if r["valid_from"] <= on_date]
    if not applicable:
        # 질문 날짜보다 나중에 관측된 것뿐이다 → 그 날짜엔 근거가 없다.
        return []
    latest = max(r["valid_from"] for r in applicable)
    return [r for r in applicable if r["valid_from"] == latest]


def hours_observation_dates(conn: sqlite3.Connection, facility_id: str) -> list[str]:
    """이 시설의 운영시간이 관측된 날짜들 (오름차순)."""
    rows = conn.execute(
        """SELECT DISTINCT valid_from FROM operating_hours
            WHERE facility_id = ? AND status = ? ORDER BY valid_from""",
        (facility_id, VERIFIED),
    ).fetchall()
    return [r["valid_from"] for r in rows]


def hours_changes(conn: sqlite3.Connection, facility_id: str) -> list[dict[str, Any]]:
    """관측 사이에 시간표가 바뀐 지점을 찾는다.

    ★ 이게 `term='unspecified'` 를 **추론이 아니라 관측으로** 해소하는 경로다.
      학기 경계를 지나며 시간표가 바뀌는 걸 실제로 보면, 그 시간표가
      학기 의존적이라는 사실이 관측된다. 안 바뀌면 학기 무관인 게 관측된다.
      그래서 개강 전후로 주간 크롤을 돌려두는 게 중요하다.
    """
    dates = hours_observation_dates(conn, facility_id)
    out: list[dict[str, Any]] = []
    prev_sig: set | None = None
    prev_date: str | None = None
    for d in dates:
        rows = [r for r in _fact_select(conn, "operating_hours",
                                        "facility_id = ? AND valid_from = ?",
                                        (facility_id, d))]
        sig = {(r["term"], r["weekday"], r["meal_type"], r["is_closed"],
                r["open_time"], r["close_time"]) for r in rows}
        if prev_sig is not None and sig != prev_sig:
            out.append({
                "from_date": prev_date, "to_date": d,
                "added": sorted(sig - prev_sig), "removed": sorted(prev_sig - sig),
            })
        prev_sig, prev_date = sig, d
    return out


def weekday_index(date: str) -> int:
    """스키마 규약: 0=일 .. 6=토 (파이썬 weekday 와 다르다)."""
    d = dt.date.fromisoformat(date)
    return (d.weekday() + 1) % 7


UNSPECIFIED_TERM = "unspecified"


def has_price_table(conn: sqlite3.Connection, facility_id: str) -> bool:
    """이 시설에 단가표가 수집돼 있는가.

    없는데 '단가표 참고'라고 안내하면 있지도 않은 곳으로 보내는 셈이다.
    (생활관 식당은 단가표 원천 자체가 없다.)
    """
    row = conn.execute(
        "SELECT 1 FROM menu_price WHERE facility_id = ? AND status = ? LIMIT 1",
        (facility_id, VERIFIED),
    ).fetchone()
    return row is not None


def hours_coverage(conn: sqlite3.Connection, facility_id: str) -> str:
    row = conn.execute("SELECT hours_coverage FROM facility WHERE id = ?",
                       (facility_id,)).fetchone()
    return row["hours_coverage"] if row else "partial"


def serves_meal(conn: sqlite3.Connection, *, facility_id: str, date: str,
                meal_type: str, term: str | None = None) -> bool | None:
    """이 시설이 그 날짜에 해당 끼니를 애초에 운영하는가.

    True / False 는 운영시간 원천의 **관측 결과**다. 근거가 없으면 None.

    ── 비대칭 규칙 (01_설계.md §5) ────────────────────────────────
    긍정 단정에는 높은 근거를, 부정·유보에는 낮은 근거를 요구한다.
      · 긍정을 잘못 쓰면 → 학생이 갔는데 문이 닫혀 있다. 우리가 보낸 것이다
      · 부정을 잘못 쓰면 → 학생이 안 간다. 원문 링크로 확인할 수 있다
    헛걸음을 만드는 쪽이 훨씬 나쁘다.

    적용 두 곳
      1) term='unspecified' 행에서 나온 결론
         미운영(False) → 채택.  운영(True) → None 으로 **강등**
         근거는 단조성이다. 방학에 운영이 줄지 늘지는 않는다(실측: 후생관
         방학 조식·석식 '운영없음'). 그래서 미상 시간표의 "안 한다"는
         방학에도 성립하지만 "한다"는 성립하지 않을 수 있다.
      2) 행 자체가 없을 때
         hours_coverage='complete' → False (시간표를 통째로 수집했으므로 부재는 미운영)
         hours_coverage='partial'  → None  (안 긁은 것과 구분이 안 된다)
    """
    rows = query_operating_hours(conn, facility_id=facility_id, on_date=date)
    if not rows:
        return None

    coverage = hours_coverage(conn, facility_id)
    wd = weekday_index(date)
    rows_wd = [r for r in rows if r["weekday"] == wd]

    def absent() -> bool | None:
        """해당 행이 없을 때. 폐쇄세계 가정을 켠 시설만 부정을 말할 수 있다."""
        return False if coverage == "complete" else None

    if not rows_wd:
        return absent()

    if term is not None:
        exact = [r for r in rows_wd if r["term"] == term]
        if exact:
            chosen, demote = exact, False
        else:
            chosen = [r for r in rows_wd if r["term"] == UNSPECIFIED_TERM]
            demote = True
    else:
        terms = {r["term"] for r in rows_wd}
        if terms == {UNSPECIFIED_TERM}:
            chosen, demote = rows_wd, True
        else:
            # 실제 term 행이 있는데 그 날짜가 어느 term 인지 모른다.
            # 모든 term 에서 '안 한다'로 일치할 때만 부정을 말한다.
            concrete = [r for r in rows_wd if r["term"] != UNSPECIFIED_TERM]
            verdicts = {
                t: _verdict(concrete, t, meal_type)
                for t in {r["term"] for r in concrete}
            }
            resolved = {v if v is not None else absent() for v in verdicts.values()}
            return False if resolved == {False} else None

    if not chosen:
        return absent()

    v = _verdict(chosen, None, meal_type)
    if v is None:
        return absent()
    if v is True and demote:
        return None      # ★ 강등. 미상 시간표의 "한다"는 방학에 성립 안 할 수 있다
    return v


def _verdict(rows, term: str | None, meal_type: str) -> bool | None:
    """해당 끼니에 대해 이 행들이 말하는 것. 모르면 None.

    · is_closed=1 인 행이 걸리면 → False (원천이 명시한 미운영. 항상 이긴다)
    · 여는 행이 걸리면 → True
    · 아무 행도 안 걸리면 → None (판단 보류. 호출자가 coverage 로 결정한다)
    """
    cand = [r for r in rows if term is None or r["term"] == term]
    # meal_type='' 은 시설 전체를 뜻하므로 모든 끼니에 적용된다
    cand = [r for r in cand if r["meal_type"] in ("", meal_type)]
    if not cand:
        return None
    if any(r["is_closed"] for r in cand):
        return False
    return True


# ═══════════════════════════════════════════════════════════════
# 가격 조인 — 정확 일치만
# ═══════════════════════════════════════════════════════════════

@dataclass
class PriceJoinResult:
    matched: int
    total: int

    @property
    def match_rate(self) -> float:
        return (self.matched / self.total) if self.total else 0.0


def attach_prices(conn: sqlite3.Connection, meal_rows: Iterable[dict[str, Any]],
                  *, facility_id: str, on_date: str) -> PriceJoinResult:
    """menu_item 에 가격을 붙인다. **name_normalized 완전 일치만.**

    유사도·부분일치·접두어 제거 전부 금지 (§2 가격 조인 규칙, T16).
    미매칭은 price=None 으로 남기고 답변에서 '—' 로 표시한다.
    조인은 추론이므로, 결정론적으로 확실할 때만 한다.
    """
    prices = _fact_select(
        conn, "menu_price",
        "facility_id = ? AND valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)",
        (facility_id, on_date, on_date),
    )
    # 같은 이름에 대상별 가격이 여럿이면 **구성원가를 기본**으로 노출한다.
    # 사용자가 학생이기 때문이다. 외부인가는 note 에 병기한다.
    AUDIENCE_RANK = {"구성원": 0, AUDIENCE_ALL: 1, "외부인": 2}
    by_name: dict[str, list[sqlite3.Row]] = {}
    for p in prices:
        by_name.setdefault(p["name_normalized"], []).append(p)

    matched = total = 0
    for meal in meal_rows:
        for item in meal.get("items", []):
            total += 1
            cands = by_name.get(item["name_normalized"])
            if not cands:
                # 미매칭. 값을 만들어내지 않는다.
                item["price_text"] = None
                item["price_display"] = "—"
                continue
            matched += 1
            cands = sorted(cands, key=lambda r: AUDIENCE_RANK.get(r["audience"], 9))
            hit = cands[0]
            # ★ 원문 표기를 그대로 쓴다. 범위를 하한으로 접지 않는다.
            item["price_text"] = hit["price_text"]
            item["price_display"] = hit["price_text"]
            item["price_audience"] = hit["audience"]
            item["price_note"] = hit["note"]
            others = [f"{c['audience']} {c['price_text']}" for c in cands[1:]]
            item["price_other_audiences"] = others or None
    return PriceJoinResult(matched=matched, total=total)


# ═══════════════════════════════════════════════════════════════
# 크롤 실행 기록 · 지표
# ═══════════════════════════════════════════════════════════════

def start_crawl(conn: sqlite3.Connection, *, run_id: str, source_key: str,
                started_at: str) -> str:
    conn.execute(
        """INSERT INTO crawl_run (id, source_key, started_at, outcome)
           VALUES (?,?,?,'fetch_error')""",
        (run_id, source_key, started_at),
    )
    return run_id


def finish_crawl(conn: sqlite3.Connection, run_id: str, *, outcome: str,
                 finished_at: str, items_parsed: int = 0,
                 items_quarantined: int = 0, error_message: str | None = None) -> None:
    conn.execute(
        """UPDATE crawl_run SET finished_at=?, outcome=?, items_parsed=?,
                                items_quarantined=?, error_message=?
           WHERE id = ?""",
        (finished_at, outcome, items_parsed, items_quarantined, error_message, run_id),
    )


def record_metric(conn: sqlite3.Connection, run_id: str, metric: str, value: float,
                  *, numerator: int | None = None, denominator: int | None = None,
                  note: str | None = None) -> None:
    """크롤 지표 기록. 급변이 곧 파서 고장 신호다.

    price_match_rate 하락 → 작명 규칙 변경
    conflict_rate 급등    → 1차·2차 중 한쪽 파서 고장
    """
    conn.execute(
        """INSERT OR REPLACE INTO crawl_metric
             (crawl_run_id, metric, value, numerator, denominator, note)
           VALUES (?,?,?,?,?,?)""",
        (run_id, metric, value, numerator, denominator, note),
    )


def metric_history(conn: sqlite3.Connection, source_key: str, metric: str,
                   limit: int = 30) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT r.started_at, m.value, m.numerator, m.denominator
             FROM crawl_metric m JOIN crawl_run r ON r.id = m.crawl_run_id
            WHERE r.source_key = ? AND m.metric = ?
            ORDER BY r.started_at DESC LIMIT ?""",
        (source_key, metric, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def quarantine(conn: sqlite3.Connection, table: str, record_id: str,
               reason: str | None = None) -> None:
    """검증 실패는 삭제가 아니라 격리다."""
    if table not in {"meal_service", "menu_item", "menu_price",
                     "operating_hours", "notice", "procedure", "pledge_progress"}:
        raise ValueError(f"알 수 없는 fact 테이블: {table}")
    conn.execute(f"UPDATE {table} SET status = 'quarantine' WHERE id = ?", (record_id,))
    if reason and table == "meal_service":
        conn.execute("UPDATE meal_service SET note = ? WHERE id = ?", (reason, record_id))


# ─────────────────────────────────────────────────────────────────────────
# 페이지 커버리지 레지스트리
#
# 인용 테이블이지 사실 테이블이 아니다. verified 필터를 태우지 않는다.
# 대신 source_url 과 observed_at 을 반드시 들고 다닌다 — 인용은 출처가 전부다.

PARSE_STATUSES = ("not_attempted", "ok", "empty", "parse_error",
                  "fetch_error", "blocked", "skipped")


def upsert_page(conn: sqlite3.Connection, *, page_url: str, host: str, path: str,
                discovered_at: str, kind: str = "static_page",
                parse_status: str = "not_attempted",
                last_attempt_at: str | None = None,
                last_success_at: str | None = None,
                http_status: int | None = None,
                section_count: int = 0, leaf_count: int = 0,
                table_count: int = 0, empty_block_count: int = 0,
                content_chars: int = 0, pruned_nodes: int = 0,
                last_modified: str | None = None, title: str = "",
                error_message: str | None = None,
                note: str | None = None) -> None:
    if parse_status not in PARSE_STATUSES:
        raise ValueError(f"알 수 없는 parse_status: {parse_status}")
    conn.execute(
        """
        INSERT INTO page_registry (page_url, host, path, kind, discovered_at,
            last_attempt_at, last_success_at, http_status, parse_status,
            section_count, leaf_count, table_count, empty_block_count,
            content_chars, pruned_nodes, last_modified, title,
            error_message, note)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(page_url) DO UPDATE SET
            host=excluded.host, path=excluded.path, kind=excluded.kind,
            last_attempt_at=excluded.last_attempt_at,
            -- 성공 시각은 되돌리지 않는다. 이번에 실패해도 '언제 마지막으로 됐나'는
            -- 남아 있어야 한다 — 그게 없으면 고장인지 원래 없는 건지 못 가른다.
            last_success_at=COALESCE(excluded.last_success_at,
                                     page_registry.last_success_at),
            http_status=excluded.http_status,
            parse_status=excluded.parse_status,
            section_count=excluded.section_count,
            leaf_count=excluded.leaf_count,
            table_count=excluded.table_count,
            empty_block_count=excluded.empty_block_count,
            content_chars=excluded.content_chars,
            pruned_nodes=excluded.pruned_nodes,
            last_modified=excluded.last_modified,
            title=excluded.title,
            error_message=excluded.error_message,
            note=excluded.note
        """,
        (page_url, host, path, kind, discovered_at, last_attempt_at,
         last_success_at, http_status, parse_status, section_count, leaf_count,
         table_count, empty_block_count, content_chars, pruned_nodes,
         last_modified, title, error_message, note))


def replace_sections(conn: sqlite3.Connection, *, page_url: str,
                     sections: Iterable[Any], observed_at: str,
                     page_last_modified: str | None = None) -> int:
    """섹션은 그 페이지의 **스냅샷**이다. 통째로 갈아끼운다.

    부분 갱신을 하면 사라진 섹션이 남아 유령 인용이 된다.
    없어진 문장을 인용하는 것은 지어내는 것과 구별되지 않는다.
    """
    old = [r[0] for r in conn.execute(
        "SELECT section_key FROM page_section WHERE page_url = ?", (page_url,))]
    conn.execute("DELETE FROM page_section WHERE page_url = ?", (page_url,))
    fts = has_fts(conn)
    if fts and old:
        conn.executemany(
            "DELETE FROM page_section_fts WHERE section_key = ?",
            [(k,) for k in old])
    n = 0
    for s in sections:
        conn.execute(
            """
            INSERT INTO page_section (section_key, page_url, ordinal, depth,
                kind, path, text, raw_text, is_leaf, parent_key, quote_key,
                applies_to, section_hash, observed_at, source_url,
                page_last_modified)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(section_key) DO NOTHING
            """,
            (s.key, page_url, s.ordinal, s.depth, s.kind, s.path_text, s.text,
             s.quote_text, 1 if s.is_leaf else 0, s.parent_key, s.quote_key,
             s.applies_to, s.section_hash, observed_at, page_url,
             page_last_modified))
        if fts and s.is_leaf:
            conn.execute(
                "INSERT INTO page_section_fts (section_key, text, path) "
                "VALUES (?,?,?)", (s.key, s.text, s.path_text))
        n += 1
    return n


def coverage_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """상태별 페이지 수. 이 표가 커버리지의 유일한 근거다."""
    rows = conn.execute(
        "SELECT parse_status, COUNT(*) FROM page_registry GROUP BY parse_status"
    ).fetchall()
    by_status = {r[0]: r[1] for r in rows}
    total = sum(by_status.values())
    ok = by_status.get("ok", 0)
    agg = conn.execute(
        "SELECT COUNT(*), SUM(is_leaf) FROM page_section").fetchone()

    # ★ '본문이 없다' 와 '게시판이라 본문이 없다' 는 다른 상태다.
    #   섞으면 커버리지가 실제보다 나빠 보이고 뭘 더 해야 할지도 알 수 없다.
    boards = 0
    if "board_items" in {r[1] for r in conn.execute(
            "PRAGMA table_info(page_registry)")}:
        boards = conn.execute(
            "SELECT COUNT(*) FROM page_registry "
            "WHERE board_items > 0 AND parse_status <> 'ok'").fetchone()[0]
    empty = by_status.get("empty", 0)
    return {
        "total_pages": total,
        "by_status": {s: by_status.get(s, 0) for s in PARSE_STATUSES},
        "boards": boards,                     # 게시판 — 공지 크롤러가 담당
        "empty_no_content": max(empty - boards, 0),
        "answerable_ratio": round(ok / total, 3) if total else 0.0,
        "covered_ratio": round((ok + boards) / total, 3) if total else 0.0,
        "sections": agg[0] or 0,
        "indexed_leaves": agg[1] or 0,
    }


def coverage_gaps(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict]:
    """답할 수 없는 페이지 목록. 왜 못 하는지까지 같이 준다."""
    rows = conn.execute(
        """
        SELECT page_url, path, title, parse_status, section_count,
               empty_block_count, last_attempt_at, last_success_at, error_message
        FROM page_registry
        WHERE parse_status <> 'ok'
        ORDER BY CASE parse_status
                   WHEN 'parse_error' THEN 0 WHEN 'empty' THEN 1
                   WHEN 'fetch_error' THEN 2 WHEN 'not_attempted' THEN 3
                   ELSE 4 END, path
        LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def page_status(conn: sqlite3.Connection, page_url: str) -> dict | None:
    r = conn.execute("SELECT * FROM page_registry WHERE page_url = ?",
                     (page_url,)).fetchone()
    return dict(r) if r else None


# ─────────────────────────────────────────────────────────────────────────
# 섹션 검색 — 색인은 잎, 인용은 부모
#
# 지금은 LIKE 스캔이다. 4천 섹션에서는 충분하고, 14,000페이지(≈60만 섹션)로 가면
# FTS5 로 바꿔야 한다. 그때 바뀌는 것은 이 함수 안이지 호출부가 아니다.

# trigram 은 3글자 미만을 매칭하지 못한다. 그 밑은 LIKE 로 간다.
FTS_MIN_CHARS = 3


def has_fts(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'page_section_fts'"
    ).fetchone())


def rebuild_fts(conn: sqlite3.Connection) -> int:
    """검색 색인을 통째로 다시 만든다. 스키마가 뒤늦게 붙은 DB 를 위해서다."""
    if not has_fts(conn):
        return 0
    conn.execute("DELETE FROM page_section_fts")
    conn.execute(
        """INSERT INTO page_section_fts (section_key, text, path)
           SELECT section_key, text, path FROM page_section WHERE is_leaf = 1""")
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM page_section_fts").fetchone()[0]


def _fts_phrase(token: str) -> str:
    """FTS5 질의 문자열. 따옴표를 이스케이프하지 않으면 구문 오류가 난다."""
    return '"' + token.replace('"', '""') + '"'


def section_total(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM page_section WHERE is_leaf = 1").fetchone()[0]


def token_doc_freq(conn: sqlite3.Connection, token: str) -> int:
    """이 토큰이 몇 개 섹션에 나오나. 흔한 말에 낮은 가중치를 주기 위해 센다."""
    if len(token) >= FTS_MIN_CHARS and has_fts(conn):
        return conn.execute(
            "SELECT COUNT(*) FROM page_section_fts WHERE page_section_fts MATCH ?",
            (_fts_phrase(token),)).fetchone()[0]
    like = f"%{token}%"
    return conn.execute(
        "SELECT COUNT(*) FROM page_section "
        "WHERE is_leaf = 1 AND (text LIKE ? OR path LIKE ?)",
        (like, like)).fetchone()[0]


def search_sections(conn: sqlite3.Connection, tokens: Sequence[str], *,
                    limit: int = 600, host: str | None = None
                    ) -> list[dict[str, Any]]:
    """토큰이 하나라도 들어간 색인 섹션을 모은다. 순위는 질의 계층에서 매긴다.

    ★ 자를 때는 **관련도 순으로** 자른다.
      예전에는 아무 순서로 400개를 자르고 그 안에서 순위를 매겼다.
      섹션이 6만 개가 되자 정작 본부의 '1종 장학금 : 등록금 전액' 이
      잘려나가고 학과 복사본만 남았다. 조용한 절단은 커버리지 착시를 만든다.
      bm25 는 FTS 가 매기는 값이고, 최종 순위는 여전히 질의 계층에서 정한다.
    """
    if not tokens:
        return []
    long_toks = [t for t in tokens if len(t) >= FTS_MIN_CHARS]
    short_toks = [t for t in tokens if len(t) < FTS_MIN_CHARS]

    # 3글자 이상이 하나라도 있으면 FTS 로 후보를 좁힌다. 짧은 토큰은
    # 그 후보 안에서만 LIKE 로 확인하므로 전체 스캔이 사라진다.
    if long_toks and has_fts(conn):
        match = " OR ".join(_fts_phrase(t) for t in long_toks)
        rows = conn.execute(
            """
            SELECT s.*, r.host AS host, r.title AS page_title, r.last_modified AS page_modified,
                   r.parse_status
              FROM page_section_fts f
              JOIN page_section s ON s.section_key = f.section_key
              JOIN page_registry r ON r.page_url = s.page_url
             WHERE page_section_fts MATCH ?
               AND (? IS NULL OR r.host = ?)
             ORDER BY bm25(page_section_fts)
             LIMIT ?
            """, (match, host, host, limit)).fetchall()
        out = [dict(r) for r in rows]
        if out or not short_toks:
            return out
        # FTS 가 빈손이면 짧은 토큰만 남은 셈이라 아래로 떨어진다

    clause = " OR ".join(["(s.text LIKE ? OR s.path LIKE ?)"] * len(tokens))
    args: list[Any] = []
    for t in tokens:
        args += [f"%{t}%", f"%{t}%"]
    args += [host, host, limit]
    rows = conn.execute(
        f"""
        SELECT s.*, r.host AS host, r.title AS page_title, r.last_modified AS page_modified,
               r.parse_status
          FROM page_section s
          JOIN page_registry r ON r.page_url = s.page_url
         WHERE s.is_leaf = 1 AND ({clause}) AND (? IS NULL OR r.host = ?)
         LIMIT ?
        """, args).fetchall()
    return [dict(r) for r in rows]


def get_section(conn: sqlite3.Connection, section_key: str) -> dict | None:
    r = conn.execute(
        """SELECT s.*, r.host AS host, r.title AS page_title, r.last_modified AS page_modified
             FROM page_section s
             JOIN page_registry r ON r.page_url = s.page_url
            WHERE s.section_key = ?""", (section_key,)).fetchone()
    return dict(r) if r else None


# ─────────────────────────────────────────────────────────────────────────
# 공지 목록 — 제목·게시일·링크·게시판만. 구조화하지 않는다.

def notice_total(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM notice_item").fetchone()[0]


def has_notice_fts(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'notice_item_fts'").fetchone())


def replace_notices(conn: sqlite3.Connection, *, board_url: str, items,
                    host: str, board_name: str, site_name: str,
                    observed_at: str) -> int:
    """한 게시판의 목록을 통째로 갈아끼운다.

    게시판은 페이지가 넘어가면 글이 밀려난다. 부분 갱신을 하면
    이미 사라진 글이 계속 남아 유령 링크가 된다.
    """
    old = [r[0] for r in conn.execute(
        "SELECT item_key FROM notice_item WHERE board_url = ?", (board_url,))]
    conn.execute("DELETE FROM notice_item WHERE board_url = ?", (board_url,))
    fts = has_notice_fts(conn)
    if fts and old:
        conn.executemany("DELETE FROM notice_item_fts WHERE item_key = ?",
                         [(k,) for k in old])
    n = 0
    for it in items:
        conn.execute(
            """INSERT INTO notice_item (item_key, url, title, published_at,
                   category, author, board_url, board_name, host, site_name,
                   observed_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(item_key) DO UPDATE SET
                   title=excluded.title, published_at=excluded.published_at,
                   observed_at=excluded.observed_at""",
            (it.key, it.url, it.title, it.published_at, it.category, it.author,
             board_url, board_name, host, site_name, observed_at))
        if fts:
            conn.execute(
                "INSERT INTO notice_item_fts (item_key, title, board_name) "
                "VALUES (?,?,?)", (it.key, it.title, board_name))
        n += 1
    return n


def search_notices(conn: sqlite3.Connection, tokens: Sequence[str], *,
                   limit: int = 60, host: str | None = None
                   ) -> list[dict[str, Any]]:
    """제목에서 찾는다. 최신 글이 먼저다 — 공지는 오래된 것이 덜 쓸모 있다."""
    if not tokens:
        return []
    long_toks = [t for t in tokens if len(t) >= FTS_MIN_CHARS]
    if long_toks and has_notice_fts(conn):
        match = " OR ".join(_fts_phrase(t) for t in long_toks)
        rows = conn.execute(
            """SELECT n.* FROM notice_item_fts f
                 JOIN notice_item n ON n.item_key = f.item_key
                WHERE notice_item_fts MATCH ? AND (? IS NULL OR n.host = ?)
                ORDER BY n.published_at DESC NULLS LAST
                LIMIT ?""", (match, host, host, limit)).fetchall()
        if rows:
            return [dict(r) for r in rows]
    clause = " OR ".join(["title LIKE ?"] * len(tokens))
    args: list[Any] = [f"%{t}%" for t in tokens] + [host, host, limit]
    rows = conn.execute(
        f"""SELECT * FROM notice_item
             WHERE ({clause}) AND (? IS NULL OR host = ?)
             ORDER BY published_at DESC NULLS LAST LIMIT ?""", args).fetchall()
    return [dict(r) for r in rows]


def rebuild_notice_fts(conn: sqlite3.Connection) -> int:
    if not has_notice_fts(conn):
        return 0
    conn.execute("DELETE FROM notice_item_fts")
    conn.execute(
        """INSERT INTO notice_item_fts (item_key, title, board_name)
           SELECT item_key, title, board_name FROM notice_item""")
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM notice_item_fts").fetchone()[0]


def ensure_columns(conn: sqlite3.Connection) -> list[str]:
    """이미 만들어진 DB 에 뒤늦게 생긴 칸을 붙인다.

    CREATE TABLE IF NOT EXISTS 는 기존 테이블을 고치지 않는다.
    스키마가 자란 뒤에 배포하면 조용히 옛 구조로 돈다 — 그게 더 위험하다.
    """
    added = []
    have = {r[1] for r in conn.execute("PRAGMA table_info(page_registry)")}
    for col, ddl in (("board_items", "INTEGER NOT NULL DEFAULT 0"),):
        if col not in have:
            conn.execute(f"ALTER TABLE page_registry ADD COLUMN {col} {ddl}")
            added.append(col)
    if added:
        conn.commit()
    return added


def mark_boards(conn: sqlite3.Connection) -> int:
    """공지를 담고 있는 페이지에 게시판 표시를 단다.

    '본문이 없다' 로 남아 있던 페이지의 상당수가 게시판이었다.
    구분하지 않으면 커버리지가 실제보다 나빠 보이고,
    무엇을 더 해야 하는지도 알 수 없다.
    """
    ensure_columns(conn)
    conn.execute("UPDATE page_registry SET board_items = 0")
    conn.execute(
        """UPDATE page_registry SET board_items = (
               SELECT COUNT(*) FROM notice_item n
                WHERE n.board_url = page_registry.page_url)""")
    conn.commit()
    return conn.execute(
        "SELECT COUNT(*) FROM page_registry WHERE board_items > 0").fetchone()[0]
