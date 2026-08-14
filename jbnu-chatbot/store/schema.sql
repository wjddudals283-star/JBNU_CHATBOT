-- 전북대 총학 챗봇 — DB 스키마 (1단계)
-- 계약: 02_클로드코드_핸드오프.md §2
--
-- 계약 대비 추가분 (필드 추가만. 불변 규칙·티어 배정 무변경)
--   a) 모든 fact 테이블에 CHECK 제약으로 enum 을 강제 (주석에만 있던 것을 실제 제약으로)
--   b) 모든 fact 테이블에 CHECK (tier <> 'T4' OR valid_to IS NOT NULL)
--      — "T4 는 valid_to NOT NULL" 불변 규칙을 pledge_progress 밖에서도 강제
--   c) source_type 에 'dorm' 추가 (생활관). 답변 제외는 'thirdparty' 만 유지
--   d) operating_hours 에 meal_type 추가
--      — §4 B분기 판정표가 serves_meal(facility, date, meal_type) 를 요구한다.
--        실데이터도 끼니별이다(진수원 점심 11:30~14:00 / 석식 17:30~19:00).
--        이 컬럼이 없으면 "진수원은 아침을 운영하지 않아요"(B)를 만들 수 없다.
--
-- ★ SQLite 는 외래키가 기본 OFF 다. 반드시 연결마다 PRAGMA foreign_keys = ON.
--   repo.connect() 가 이를 강제한다.

PRAGMA foreign_keys = ON;


-- ═══════════════════════════════════════════════════════════════
-- Source Layer
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS source_snapshot (
  id            TEXT PRIMARY KEY,
  source_key    TEXT NOT NULL,
  url           TEXT NOT NULL,
  fetched_at    TEXT NOT NULL,
  http_status   INTEGER,
  content_hash  TEXT NOT NULL,          -- 원문 바이트 해시. 감사 추적용
  -- 캐시버스터·CSRF 토큰 등 매 요청 변하는 부분을 지운 뒤의 해시.
  -- ★ 변경 감지는 이걸로 한다. 원문 해시로 하면 likehome 처럼
  --   ?ver=<유닉스타임> 을 붙이는 사이트에서 'unchanged' 가 영영 성립하지 않는다.
  stable_hash   TEXT NOT NULL DEFAULT '',
  content_path  TEXT NOT NULL,
  media_type    TEXT NOT NULL
                CHECK (media_type IN ('html','image','json','pdf'))
);


-- ═══════════════════════════════════════════════════════════════
-- 마스터 (출처 메타 없음 — 사실이 아니라 식별자다)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS organization (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  aliases       TEXT,
  type          TEXT NOT NULL
                CHECK (type IN ('총학생회','단과대학생회','생협','생활관',
                                '본부부서','동아리연합회')),
  parent_org_id TEXT REFERENCES organization(id),
  official_url  TEXT,
  contact_id    TEXT
);

CREATE TABLE IF NOT EXISTS place (
  id       TEXT PRIMARY KEY,
  name     TEXT NOT NULL,
  aliases  TEXT,
  campus   TEXT, building TEXT, floor TEXT,
  lat      REAL, lng REAL,
  entrance_note TEXT
);

CREATE TABLE IF NOT EXISTS facility (
  id            TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  aliases       TEXT,
  place_id      TEXT REFERENCES place(id),
  operated_by   TEXT REFERENCES organization(id),
  facility_type TEXT NOT NULL
                CHECK (facility_type IN ('열람실','강의실','체육시설','식당',
                                         '카페','매점','사무실')),
  capacity             INTEGER,
  reservation_required INTEGER NOT NULL DEFAULT 0,
  reservation_url      TEXT,
  source_url    TEXT NOT NULL,
  -- 'dorm' 추가. 답변에서 제외되는 것은 'thirdparty' 뿐이다.
  source_type   TEXT NOT NULL
                CHECK (source_type IN ('official','coop','council','dorm','thirdparty')),
  -- ★ 운영시간표를 통째로 수집했는가. 폐쇄세계 가정을 명시적으로 만든다.
  --   complete: 행이 없으면 = 미운영 (부정 결론을 내려도 된다)
  --   partial : 행이 없으면 = 모름(None). 아직 안 긁은 것과 구분이 안 된다
  --   기본값은 partial. complete 는 시간표 전체를 파싱한 크롤러만 세울 수 있다.
  hours_coverage TEXT NOT NULL DEFAULT 'partial'
                 CHECK (hours_coverage IN ('complete','partial'))
);


-- ═══════════════════════════════════════════════════════════════
-- Fact Layer
--   공통 출처 메타 8개는 모든 fact 테이블에 동일하게 들어간다.
--   source_id / source_url / observed_at / confidence / extraction_method / tier
--   = NOT NULL (불변 규칙). valid_from 도 NOT NULL.
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS operating_hours (
  id          TEXT PRIMARY KEY,
  facility_id TEXT NOT NULL REFERENCES facility(id),
  -- ★ 원천이 학기를 안 밝히면 'unspecified'. 미상을 미상으로 적는다.
  --   '학기중'으로 좁히거나 전 학기 적용으로 넓히는 건 둘 다 원천이 말하지 않은 걸 채우는 것이다.
  term        TEXT NOT NULL
              CHECK (term IN ('학기중','방학','시험기간','공휴일','unspecified')),
  weekday     INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 7),  -- 0=일 .. 6=토, 7=공휴일
  -- 어느 끼니의 운영시간인가. 빈 문자열이면 시설 전체.
  -- ★ 이 값이 있어야 "진수원은 아침을 운영하지 않아요"(B분기)를 답변 시점에 판정할 수 있다.
  meal_type   TEXT NOT NULL DEFAULT ''
              CHECK (meal_type IN ('','breakfast','lunch','dinner')),
  -- ★ 원천이 **명시한** 미운영("주말·공휴일 미운영")을 행으로 남긴다.
  --   행의 부재로 미운영을 추론하지 않기 위해서다. 명시된 부정은 관측이다.
  is_closed   INTEGER NOT NULL DEFAULT 0 CHECK (is_closed IN (0,1)),
  open_time   TEXT, close_time TEXT, break_start TEXT, break_end TEXT, note TEXT,

  source_id         TEXT NOT NULL REFERENCES source_snapshot(id),
  source_url        TEXT NOT NULL,
  observed_at       TEXT NOT NULL,
  valid_from        TEXT NOT NULL,
  valid_to          TEXT,
  confidence        REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  extraction_method TEXT NOT NULL
                    CHECK (extraction_method IN ('html_selector','json_api',
                                                 'pdf_parse','vlm_ocr','manual_admin')),
  status            TEXT NOT NULL DEFAULT 'verified'
                    CHECK (status IN ('verified','quarantine','needs_review',
                                      'superseded','expired','conflict')),
  tier              TEXT NOT NULL CHECK (tier IN ('T1','T2','T3','T4')),
  CHECK (tier <> 'T4' OR valid_to IS NOT NULL),
  -- 여는 행이면 시각이 있어야 하고, 시각이 없으면 명시적 미운영 행이어야 한다.
  CHECK (is_closed = 1 OR open_time IS NOT NULL),
  UNIQUE(facility_id, term, weekday, meal_type, valid_from)
);

CREATE TABLE IF NOT EXISTS meal_service (
  id             TEXT PRIMARY KEY,
  facility_id    TEXT NOT NULL REFERENCES facility(id),
  date           TEXT NOT NULL,
  meal_type      TEXT NOT NULL
                 CHECK (meal_type IN ('breakfast','lunch','dinner')),
  -- ★ closed_vacation / closed_holiday 폐기.
  --   빈 칸은 관측값이 아니라 관측의 부재다 → unknown.
  --   "방학이라 쉰다"는 판단은 저장하지 않는다. 답변 시점에 operating_hours 와 조인한다.
  service_status TEXT NOT NULL
                 CHECK (service_status IN ('operating','closed_temporary','unknown')),
  -- ★ NULL 금지. NULL 이면 SQLite·Postgres 모두 UNIQUE 가 무력화돼
  --   매 크롤마다 같은 조합이 중복 삽입된다 (T17).
  --   가상의 위험이 아니다 — 의대식당 조식은 원천이 cate2='' cate3='' 를 준다.
  zone           TEXT NOT NULL DEFAULT '',
  corner         TEXT NOT NULL DEFAULT '',
  -- ★ 분할 전 원본 셀 텍스트. 품목 구분자('/'·줄바꿈)에 모호함이 있어
  --   나중에 분할 규칙을 바꿔도 **재크롤 없이** 다시 쪼갤 수 있게 남긴다.
  --   stable_hash 에서 "저장은 원문 그대로"라고 한 것과 같은 이유다.
  raw_text       TEXT,
  -- ★ price_default 삭제. 날짜 키 테이블에 가격을 두지 않는다.
  note           TEXT,

  source_id         TEXT NOT NULL REFERENCES source_snapshot(id),
  source_url        TEXT NOT NULL,
  observed_at       TEXT NOT NULL,
  valid_from        TEXT NOT NULL,
  valid_to          TEXT,
  confidence        REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  extraction_method TEXT NOT NULL
                    CHECK (extraction_method IN ('html_selector','json_api',
                                                 'pdf_parse','vlm_ocr','manual_admin')),
  status            TEXT NOT NULL DEFAULT 'verified'
                    CHECK (status IN ('verified','quarantine','needs_review',
                                      'superseded','expired','conflict')),
  tier              TEXT NOT NULL CHECK (tier IN ('T1','T2','T3','T4')),
  CHECK (tier <> 'T4' OR valid_to IS NOT NULL),
  UNIQUE(facility_id, date, meal_type, zone, corner)
);

CREATE TABLE IF NOT EXISTS menu_item (
  id              TEXT PRIMARY KEY,
  meal_service_id TEXT NOT NULL REFERENCES meal_service(id) ON DELETE CASCADE,
  name            TEXT NOT NULL,
  name_normalized TEXT NOT NULL,
  -- ★ price 컬럼 없음. 가격은 날짜에 붙어 있지 않다 → menu_price.
  category        TEXT,
  allergens       TEXT,
  is_vegetarian   INTEGER NOT NULL DEFAULT 0,
  display_order   INTEGER NOT NULL DEFAULT 0,

  source_id         TEXT NOT NULL REFERENCES source_snapshot(id),
  source_url        TEXT NOT NULL,
  observed_at       TEXT NOT NULL,
  confidence        REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  extraction_method TEXT NOT NULL
                    CHECK (extraction_method IN ('html_selector','json_api',
                                                 'pdf_parse','vlm_ocr','manual_admin')),
  status            TEXT NOT NULL DEFAULT 'verified'
                    CHECK (status IN ('verified','quarantine','needs_review',
                                      'superseded','expired','conflict')),
  tier              TEXT NOT NULL CHECK (tier IN ('T1','T2','T3','T4')),
  UNIQUE(meal_service_id, display_order)
);

CREATE TABLE IF NOT EXISTS menu_price (
  id              TEXT PRIMARY KEY,
  facility_id     TEXT NOT NULL REFERENCES facility(id),
  name            TEXT NOT NULL,
  -- 조인 키. 정규화 규칙은 menu_item.name_normalized 와 반드시 동일해야 한다.
  -- 정확 일치만 조인한다 (유사도·부분일치 금지). 미매칭은 NULL.
  name_normalized TEXT NOT NULL,
  category        TEXT,     -- 단가표 '분류' (한식|양식|분식|포크프리존)
  corner          TEXT,     -- 단가표 '코너'
  -- ★ 답변은 price_text 를 그대로 렌더한다. 범위를 하한 단일값으로 접지 않는다.
  --   6,000~6,500원짜리를 "6,000원"이라 답하면 학생이 6,000원 들고 갔다가 모자란다.
  --   가격을 낮게 말하는 건 높게 말하는 것보다 나쁘다.
  price_text      TEXT NOT NULL,      -- 원문 표기 그대로. "6,000원 - 6,500원", "6,000원 부터"
  price_min       INTEGER NOT NULL,   -- 검증·정렬 전용
  price_max       INTEGER,            -- 범위면 상한, 단일가면 min 과 동일, '부터'면 NULL
  -- ★ audience 를 UNIQUE 에 넣지 않으면 "구성원 7,000 / 외부인 8,500" 에서 한쪽이 덮어쓴다.
  --   외부인 행이 이기면 학생 전원에게 8,500원을 안내하게 된다. 실제 오답 경로다.
  audience        TEXT NOT NULL DEFAULT '전체'
                  CHECK (audience IN ('전체','구성원','외부인')),
  note            TEXT,     -- '곱빼기(+)500', '천원의 아침밥 이벤트시 1,000원'
  currency        TEXT NOT NULL DEFAULT 'KRW',

  source_id         TEXT NOT NULL REFERENCES source_snapshot(id),
  source_url        TEXT NOT NULL,
  observed_at       TEXT NOT NULL,
  valid_from        TEXT NOT NULL,
  valid_to          TEXT,
  confidence        REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  extraction_method TEXT NOT NULL
                    CHECK (extraction_method IN ('html_selector','json_api',
                                                 'pdf_parse','vlm_ocr','manual_admin')),
  status            TEXT NOT NULL DEFAULT 'verified'
                    CHECK (status IN ('verified','quarantine','needs_review',
                                      'superseded','expired','conflict')),
  tier              TEXT NOT NULL DEFAULT 'T2' CHECK (tier IN ('T1','T2','T3','T4')),
  CHECK (tier <> 'T4' OR valid_to IS NOT NULL),
  CHECK (price_max IS NULL OR price_max >= price_min),
  CHECK (price_text <> ''),
  UNIQUE(facility_id, name_normalized, audience, valid_from)
);

CREATE TABLE IF NOT EXISTS academic_calendar (
  id           TEXT PRIMARY KEY,
  ac_year      INTEGER NOT NULL,
  ac_semester  INTEGER NOT NULL CHECK (ac_semester IN (1, 2)),
  title        TEXT NOT NULL,
  start_date   TEXT NOT NULL,
  end_date     TEXT,                 -- 단일 날짜면 NULL. '~' 로 기간이 명시된 것만 채운다
  category     TEXT,
  -- ★ 원문 그대로. '<dd>제2학기 개강, 일반대학원 종합시험</dd>' 처럼 콤마로 묶인 항목을
  --   **쪼개지 않는다.** 콤마를 항목 구분자로 보는 것 자체가 추론이다 —
  --   원천이 "이건 두 건"이라고 말한 적이 없다.
  --   여러 해를 백필해 같은 항목이 단독으로 나타나면 그때 관측으로 판단한다.
  raw_text     TEXT,

  source_id         TEXT NOT NULL REFERENCES source_snapshot(id),
  source_url        TEXT NOT NULL,
  observed_at       TEXT NOT NULL,
  valid_from        TEXT NOT NULL,
  valid_to          TEXT,
  confidence        REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  extraction_method TEXT NOT NULL
                    CHECK (extraction_method IN ('html_selector','json_api',
                                                 'pdf_parse','vlm_ocr','manual_admin')),
  status            TEXT NOT NULL DEFAULT 'verified'
                    CHECK (status IN ('verified','quarantine','needs_review',
                                      'superseded','expired','conflict')),
  tier              TEXT NOT NULL DEFAULT 'T1' CHECK (tier IN ('T1','T2','T3','T4')),
  CHECK (tier <> 'T4' OR valid_to IS NOT NULL),
  CHECK (end_date IS NULL OR end_date >= start_date),
  -- ★ 학사일정은 개정된다 → 시계열 규칙에 따라 valid_from 이 식별자·UNIQUE 에 들어간다
  UNIQUE(ac_year, ac_semester, start_date, title, valid_from)
);

CREATE TABLE IF NOT EXISTS notice (
  id            TEXT PRIMARY KEY,
  issuer_org_id TEXT REFERENCES organization(id),
  title         TEXT NOT NULL,
  body          TEXT,
  published_at  TEXT NOT NULL,
  period_start  TEXT, period_end TEXT,
  target_audience TEXT, apply_url TEXT,
  supersedes    TEXT REFERENCES notice(id),

  source_id         TEXT NOT NULL REFERENCES source_snapshot(id),
  source_url        TEXT NOT NULL,
  observed_at       TEXT NOT NULL,
  confidence        REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  extraction_method TEXT NOT NULL
                    CHECK (extraction_method IN ('html_selector','json_api',
                                                 'pdf_parse','vlm_ocr','manual_admin')),
  status            TEXT NOT NULL DEFAULT 'verified'
                    CHECK (status IN ('verified','quarantine','needs_review',
                                      'superseded','expired','conflict')),
  tier              TEXT NOT NULL CHECK (tier IN ('T1','T2','T3','T4'))
);


-- ═══════════════════════════════════════════════════════════════
-- 2단계 이후. 1단계엔 테이블만 만들어 둔다.
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS procedure (
  id           TEXT PRIMARY KEY,
  title        TEXT NOT NULL,
  aliases      TEXT,
  owner_org_id TEXT REFERENCES organization(id),
  domain       TEXT,
  steps        TEXT, required_docs TEXT,   -- JSON
  period_start TEXT, period_end TEXT,
  eligibility  TEXT, fee TEXT, contact_id TEXT,

  source_id         TEXT NOT NULL REFERENCES source_snapshot(id),
  source_url        TEXT NOT NULL,
  observed_at       TEXT NOT NULL,
  valid_from        TEXT NOT NULL,
  valid_to          TEXT,
  confidence        REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  extraction_method TEXT NOT NULL
                    CHECK (extraction_method IN ('html_selector','json_api',
                                                 'pdf_parse','vlm_ocr','manual_admin')),
  status            TEXT NOT NULL DEFAULT 'verified'
                    CHECK (status IN ('verified','quarantine','needs_review',
                                      'superseded','expired','conflict')),
  tier              TEXT NOT NULL CHECK (tier IN ('T1','T2','T3','T4')),
  CHECK (tier <> 'T4' OR valid_to IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS pledge (
  id       TEXT PRIMARY KEY,
  term     INTEGER NOT NULL,
  number   INTEGER NOT NULL,
  category TEXT,
  title    TEXT NOT NULL,
  summary  TEXT,
  target_date TEXT,
  UNIQUE(term, number)
);

CREATE TABLE IF NOT EXISTS pledge_progress (
  id            TEXT PRIMARY KEY,
  pledge_id     TEXT NOT NULL REFERENCES pledge(id),
  status_value  TEXT NOT NULL
                CHECK (status_value IN ('계획','진행중','완료','보류','철회')),
  progress_note TEXT,
  evidence_url  TEXT,
  -- ★ T4 필수. 만료 없는 수기 정보는 저장 불가.
  author        TEXT NOT NULL,
  approved_by   TEXT NOT NULL,

  source_id         TEXT NOT NULL REFERENCES source_snapshot(id),
  source_url        TEXT NOT NULL,
  observed_at       TEXT NOT NULL,
  valid_from        TEXT NOT NULL,
  valid_to          TEXT NOT NULL,     -- ★ T4 는 NOT NULL
  confidence        REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  extraction_method TEXT NOT NULL
                    CHECK (extraction_method IN ('html_selector','json_api',
                                                 'pdf_parse','vlm_ocr','manual_admin')),
  status            TEXT NOT NULL DEFAULT 'verified'
                    CHECK (status IN ('verified','quarantine','needs_review',
                                      'superseded','expired','conflict')),
  tier              TEXT NOT NULL DEFAULT 'T4' CHECK (tier IN ('T1','T2','T3','T4')),
  CHECK (author <> '' AND approved_by <> '')
);
-- 주: 계약의 pledge_progress.status(계획|진행중|…)는 공통 출처 메타의
--     status(verified|quarantine|…)와 이름이 충돌한다. 전자를 status_value 로 분리했다.

CREATE TABLE IF NOT EXISTS alias (
  surface_form TEXT NOT NULL,
  canonical_id TEXT NOT NULL,
  entity_type  TEXT NOT NULL,
  weight       REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (surface_form, canonical_id)
);


-- ═══════════════════════════════════════════════════════════════
-- 운영 관측 (크롤 실행 기록 + 지표)
-- ═══════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS crawl_run (
  id          TEXT PRIMARY KEY,
  source_key  TEXT NOT NULL,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  outcome     TEXT NOT NULL
              CHECK (outcome IN ('success','unchanged','parse_error',
                                 'fetch_error','quarantined')),
  items_parsed      INTEGER NOT NULL DEFAULT 0,
  items_quarantined INTEGER NOT NULL DEFAULT 0,
  error_message     TEXT
);

-- 크롤 지표. 급변이 곧 파서 고장 신호다 (핸드오프 §2 가격 조인 규칙 / 교차 검증).
--   price_match_rate  떨어지면 단가표·식단표 작명 규칙이 바뀐 신호
--   conflict_rate     급등하면 1차·2차 중 한쪽 파서가 깨진 신호
CREATE TABLE IF NOT EXISTS crawl_metric (
  crawl_run_id TEXT NOT NULL REFERENCES crawl_run(id) ON DELETE CASCADE,
  metric       TEXT NOT NULL
               CHECK (metric IN ('price_match_rate','conflict_rate',
                                 'items_parsed','anchor_check')),
  value        REAL NOT NULL,
  numerator    INTEGER,
  denominator  INTEGER,
  note         TEXT,
  PRIMARY KEY (crawl_run_id, metric)
);


CREATE INDEX IF NOT EXISTS idx_meal_lookup  ON meal_service(facility_id, date, meal_type);
CREATE INDEX IF NOT EXISTS idx_meal_status  ON meal_service(status, date);
CREATE INDEX IF NOT EXISTS idx_menu_meal    ON menu_item(meal_service_id);
CREATE INDEX IF NOT EXISTS idx_price_join   ON menu_price(facility_id, name_normalized);
CREATE INDEX IF NOT EXISTS idx_hours_lookup ON operating_hours(facility_id, meal_type);
CREATE INDEX IF NOT EXISTS idx_alias        ON alias(surface_form);
CREATE INDEX IF NOT EXISTS idx_notice_pub   ON notice(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_calendar_date ON academic_calendar(start_date);
CREATE INDEX IF NOT EXISTS idx_calendar_term ON academic_calendar(ac_year, ac_semester);
CREATE INDEX IF NOT EXISTS idx_crawl_source ON crawl_run(source_key, started_at DESC);


-- ─────────────────────────────────────────────────────────────────────────
-- 페이지 커버리지 레지스트리
--
-- "없다" 는 한 가지가 아니다. 학생에게는 다 '모른다' 로 보이지만
-- 우리는 왜 모르는지 구분할 수 있어야 한다. 구분 못 하는 부재는 고칠 수 없다.
--
--   ok            파싱 성공, 내용 있음        → 인용한다
--   empty         파싱 성공, 내용이 비었음     → "그 내용이 없어요" + 원문 링크
--                 (JS 로 그리는 페이지가 여기 온다. 실제로 certificate.do 가 그렇다)
--   parse_error   구조가 안 맞아 못 읽음       → "아직 준비 중" + 페이지 링크
--   fetch_error   가져오지 못함 (403/타임아웃)
--   blocked       robots/정책상 안 긁음        → 안 한 것이지 못 한 게 아니다
--   not_attempted 발견은 했으나 아직 시도 안 함
--
-- 1000페이지가 되면 사람이 못 따라간다. 그래서 상태를 행으로 남긴다.
CREATE TABLE IF NOT EXISTS page_registry (
  page_url        TEXT PRIMARY KEY,
  host            TEXT NOT NULL,
  path            TEXT NOT NULL,
  kind            TEXT NOT NULL DEFAULT 'static_page',
  discovered_at   TEXT NOT NULL,
  last_attempt_at TEXT,
  last_success_at TEXT,
  http_status     INTEGER,
  parse_status    TEXT NOT NULL DEFAULT 'not_attempted'
                  CHECK (parse_status IN ('not_attempted','ok','empty',
                                          'parse_error','fetch_error',
                                          'blocked','skipped')),
  section_count     INTEGER NOT NULL DEFAULT 0,
  leaf_count        INTEGER NOT NULL DEFAULT 0,
  table_count       INTEGER NOT NULL DEFAULT 0,
  empty_block_count INTEGER NOT NULL DEFAULT 0,
  content_chars     INTEGER NOT NULL DEFAULT 0,
  pruned_nodes      INTEGER NOT NULL DEFAULT 0,
  -- 이 페이지가 게시판이고 공지를 몇 건 담고 있나.
  -- '본문이 없다(empty)' 와 '게시판이라 본문이 없다' 는 다른 상태다.
  -- 전자는 우리가 못 읽은 것이고 후자는 공지 크롤러가 읽은 것이다.
  board_items       INTEGER NOT NULL DEFAULT 0,
  last_modified   TEXT,             -- 페이지가 스스로 표시한 최종수정일
  title           TEXT NOT NULL DEFAULT '',
  error_message   TEXT,
  note            TEXT
);

-- 페이지 섹션. **사실 테이블이 아니라 인용 테이블이다.**
-- 여기 담기는 것은 "이 문장이 이 URL 에 있었다" 는 관측이지 우리의 주장이 아니다.
-- 그래서 verified 필터를 타지 않는다. 대신 출처와 관측 시각을 반드시 들고 다닌다.
--
--   색인(검색)  is_leaf=1 인 행 — 잎, 표의 한 행
--   인용(출력)  quote_key 가 가리키는 행 — 부모 블록, 표 전체
-- 부모 관계를 저장해 두므로 인용 정책을 바꿔도 재크롤이 필요 없다.
CREATE TABLE IF NOT EXISTS page_section (
  section_key   TEXT PRIMARY KEY,
  page_url      TEXT NOT NULL REFERENCES page_registry(page_url) ON DELETE CASCADE,
  ordinal       INTEGER NOT NULL,
  depth         INTEGER NOT NULL,
  kind          TEXT NOT NULL
                CHECK (kind IN ('block','list','table','table_row',
                                'deflist','deflist_row','paragraph')),
  path          TEXT NOT NULL,      -- '교내 장학금 > 금액별 분류'
  text          TEXT NOT NULL,      -- 정규화본 — 매칭용
  raw_text      TEXT NOT NULL,      -- 원문 — 인용용 (요약 금지)
  is_leaf       INTEGER NOT NULL CHECK (is_leaf IN (0,1)),
  parent_key    TEXT,
  quote_key     TEXT,
  applies_to    TEXT,               -- 적용 조건 (학과·입학년도 등)
  section_hash  TEXT NOT NULL,
  observed_at   TEXT NOT NULL,
  source_url    TEXT NOT NULL,
  page_last_modified TEXT
);

CREATE INDEX IF NOT EXISTS idx_section_page  ON page_section(page_url, ordinal);
CREATE INDEX IF NOT EXISTS idx_section_leaf  ON page_section(is_leaf);
CREATE INDEX IF NOT EXISTS idx_section_quote ON page_section(quote_key);
CREATE INDEX IF NOT EXISTS idx_registry_stat ON page_registry(parse_status);

-- 섹션 전문 검색.
--
-- ★ trigram 토크나이저를 쓰는 이유
--   한국어는 조사가 붙어 '교내장학금만' 처럼 한 낱말이 된다.
--   기본(unicode61) 은 낱말 단위라 '장학금' 으로 못 찾는다. trigram 은 찾는다.
--
-- ★ 다만 trigram 은 **3글자 이상**만 매칭한다.
--   '휴학' '성적' 같은 2글자 질의는 여기서 답이 안 나와 LIKE 로 떨어진다.
--   그 사실을 숨기지 않고 질의 계층에서 갈라 쓴다.
CREATE VIRTUAL TABLE IF NOT EXISTS page_section_fts USING fts5(
  section_key UNINDEXED,
  text,
  path,
  tokenize = 'trigram'
);


-- ─────────────────────────────────────────────────────────────────────────
-- 공지 목록.
--
-- ★ 구조화하지 않는다. 목록에 적힌 것만 그대로 옮긴다.
--   본문을 읽지 않고 분류도 추론하지 않는다. 게시글은 형식이 제각각이라
--   구조화하면 틀리기 시작한다. '이런 공지가 있고 여기서 볼 수 있다' 까지가
--   우리가 정직하게 말할 수 있는 전부다.
--
-- 사실 테이블이 아니라 **목록 테이블**이다. verified 필터를 타지 않고,
-- 대신 어느 게시판에서 언제 봤는지를 반드시 들고 다닌다.
CREATE TABLE IF NOT EXISTS notice_item (
  item_key     TEXT PRIMARY KEY,
  url          TEXT NOT NULL,
  title        TEXT NOT NULL,
  published_at TEXT,                 -- 목록에 날짜가 없으면 NULL. 지어내지 않는다.
  category     TEXT NOT NULL DEFAULT '',   -- 목록에 '분류' 칸이 있을 때만
  author       TEXT NOT NULL DEFAULT '',
  board_url    TEXT NOT NULL,
  board_name   TEXT NOT NULL DEFAULT '',
  host         TEXT NOT NULL,
  site_name    TEXT NOT NULL DEFAULT '',
  observed_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notice_item_pub  ON notice_item(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_notice_item_brd  ON notice_item(board_url);
CREATE INDEX IF NOT EXISTS idx_notice_item_host ON notice_item(host);

-- 제목 검색. 섹션과 같은 이유로 trigram 을 쓴다 —
-- 한국어는 조사가 붙어 '수강신청은' 처럼 한 낱말이 된다.
CREATE VIRTUAL TABLE IF NOT EXISTS notice_item_fts USING fts5(
  item_key UNINDEXED,
  title,
  board_name,
  tokenize = 'trigram'
);


-- 페이지가 **언제 바뀌었나**. 신선도 기준을 손으로 정하지 않기 위해서다.
--
-- ★ 왜 필요한가
--   날짜형 답('수강신청 언제야')은 값으로 못 잰다 — 학기마다 바뀐다.
--   형태 + 출처 + **신선도** 로 재기로 했는데, 그 '신선도 N일' 을 또 손으로
--   정하면 같은 실수다. 페이지별 변경 주기를 세면 N 이 계산으로 나온다.
--   600 상한·본부 유무 때처럼, 재보면 정할 게 없을 수도 있다.
--
-- ★ 지금 데이터로는 못 잰다 — 그래서 기록부터 시작한다
--   last_modified 는 2.7% 만 채워져 있고, section_hash 는 현재 값만 있다.
--
-- ★ 답변 경로는 이 표를 안 읽는다
--   읽기 전용 연결이 마이그레이션을 못 한다는 함정에 안 걸리게, 수집 쪽에서만 쓴다.
CREATE TABLE IF NOT EXISTS page_change (
  page_url      TEXT NOT NULL,
  changed_at    TEXT NOT NULL,      -- 바뀐 걸 관측한 시각
  content_hash  TEXT NOT NULL,
  PRIMARY KEY (page_url, changed_at)
);
CREATE INDEX IF NOT EXISTS idx_change_page ON page_change(page_url, changed_at);


-- ═══════════════════════════════════════════════════════════════
-- 총학 공지·행사 (T4 — 총학이 구글시트에 직접 넣는다)
-- ═══════════════════════════════════════════════════════════════
-- ★ 왜 별도 테이블인가
--   manual_answers 는 '질문 → 답' 매핑이다. 이건 **피드**다.
--   같은 T4 지만 모양이 다르다. 억지로 합치면 둘 다 이상해진다.
--
-- ★ 신뢰 등급은 크롤보다 높다
--   총학이 직접 확인해 넣은 것이다 — '학점포기 없음' 과 같은 자리다.
--   그래서 tier T4 이고, 답변에서 크롤 결과보다 먼저 나간다.
--
-- ★ deadline 이 지나면 후보에서 뺀다 (지우지 않는다)
--   9월에 "8월 25일까지 신청하세요" 가 나가면 크롤 오답보다 나쁘다 —
--   총학이 직접 넣은 것이라 학생이 더 믿는다.
--   행은 남긴다. 지운 것과 지난 것은 다른 사실이고, 나중에 왜 안 나갔는지 봐야 한다.
--
-- ★ body 는 인스타 캡션 원문이다. 요약하지 않는다.
--   8/14 에 자족성 판정기로 확인했다 — 날짜·대상·방법·금액·마감시각이 전부 있다.
--   우리가 줄이면 그 값들이 사라진다.
CREATE TABLE IF NOT EXISTS council_post (
  post_key     TEXT PRIMARY KEY,          -- 게시일+제목 해시. 시트에 행 ID가 없다.
  published_at TEXT NOT NULL,             -- 게시일 (YYYY-MM-DD)
  title        TEXT NOT NULL,
  body         TEXT NOT NULL DEFAULT '',  -- 인스타 캡션 원문. 그대로.
  link         TEXT NOT NULL DEFAULT '',  -- 인스타 permalink
  deadline     TEXT,                      -- 마감일. 없으면 NULL — 지어내지 않는다.
  bureau       TEXT NOT NULL DEFAULT '',  -- 작성국
  -- ★ 분류는 **사람이 적은 것만** 믿는다. 제목·본문으로 추측하지 않는다.
  --   추측이 틀린 전례가 둘 있다 —
  --     laws.jbnu.ac.kr   학칙인 줄 알았는데 법무대학원이었다
  --     교내공지          총학 게시물이 0건이었다
  --   이름이 그럴듯하다고 내용이 그런 건 아니다.
  --   쉼표로 여러 개. 저장은 원문 그대로, 조회할 때 쪼갠다.
  categories   TEXT NOT NULL DEFAULT '',
  row_no       INTEGER NOT NULL DEFAULT 0,-- 시트 행 번호. 문제 생기면 여기를 본다.

  source_id         TEXT NOT NULL REFERENCES source_snapshot(id),
  source_url        TEXT NOT NULL,
  observed_at       TEXT NOT NULL,
  confidence        REAL NOT NULL DEFAULT 1.0,
  extraction_method TEXT NOT NULL DEFAULT 'manual_admin',
  status            TEXT NOT NULL DEFAULT 'verified',
  tier              TEXT NOT NULL DEFAULT 'T4'
);

CREATE INDEX IF NOT EXISTS idx_council_pub ON council_post(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_council_dl  ON council_post(deadline);

CREATE VIRTUAL TABLE IF NOT EXISTS council_post_fts USING fts5(
  post_key UNINDEXED,
  title,
  body,
  tokenize = 'trigram'
);
