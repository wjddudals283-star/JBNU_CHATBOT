"""스키마 파일과 디스크 DB 를 맞춘다 — 표도, 컬럼도.

★ 같은 종류가 두 번 나왔다 (2026-08-14, 둘 다 배포 뒤 실제 오류)
    1차   no such table: council_post
    2차   table council_post has no column named categories

  `CREATE TABLE IF NOT EXISTS` 는 **이미 있는 표에는 아무것도 안 한다.**
  표는 막았는데 컬럼은 안 막혔다.
  두 번째면 개별로 고칠 일이 아니다 — 다음에 칸이 하나 더 늘면 또 같은 자리다.

★ 그래서 이 파일은 **전수로** 본다
  '이번에 추가한 컬럼' 만 재면 다음에 추가할 컬럼은 못 잡는다.
  스키마에 있는 컬럼을 하나씩 지워 보고, 기동이 되살리는지 확인한다.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from store import migrate, repo

SCHEMA = pathlib.Path(repo.SCHEMA_PATH)
SCHEMA_SQL = SCHEMA.read_text(encoding="utf-8")


@pytest.fixture()
def conn(tmp_path):
    c = repo.connect(tmp_path / "m.db")
    repo.init_db(c)
    return c


# ═══════════════════════════════════════════════════════════════
# 스키마 파일 읽기 — 유일한 진실을 제대로 읽어야 한다
# ═══════════════════════════════════════════════════════════════

def test_스키마에서_컬럼을_읽는다():
    cols = migrate.expected_columns(SCHEMA_SQL)
    assert "council_post" in cols
    assert "categories" in cols["council_post"]
    assert "meal_service" in cols and "service_status" in cols["meal_service"]


def test_CHECK_안의_쉼표에_안_속는다():
    """★ CHECK(x IN ('a','b')) 안에도 쉼표가 있다.

    그냥 split(',') 하면 조각이 나서 엉뚱한 이름이 컬럼으로 잡힌다.
    """
    sql = """CREATE TABLE t (
      a TEXT NOT NULL CHECK (a IN ('x','y','z')),
      b INTEGER DEFAULT 0,
      UNIQUE(a, b)
    );"""
    cols = migrate.expected_columns(sql)["t"]
    assert set(cols) == {"a", "b"}, cols


def test_제약_줄을_컬럼으로_세지_않는다():
    sql = """CREATE TABLE t (
      a TEXT PRIMARY KEY,
      b TEXT,
      FOREIGN KEY (b) REFERENCES other(id),
      CHECK (a <> ''),
      CONSTRAINT c1 UNIQUE (a, b)
    );"""
    assert set(migrate.expected_columns(sql)["t"]) == {"a", "b"}


def test_주석을_무시한다():
    sql = """CREATE TABLE t (
      -- 이건 주석이고 fake 는 컬럼이 아니다, 쉼표도 있다
      a TEXT,   -- b TEXT
      c TEXT
    );"""
    assert set(migrate.expected_columns(sql)["t"]) == {"a", "c"}


# ═══════════════════════════════════════════════════════════════
# 실제로 겪은 오류
# ═══════════════════════════════════════════════════════════════

def test_배포_뒤_실제_오류를_재현하고_고친다(conn):
    """★ table council_post has no column named categories

    분류 칸을 붙이면서 컬럼을 추가했는데, 표는 이전 배포 때 만들어졌고
    디스크에 남아 있었다.
    """
    conn.execute("ALTER TABLE council_post DROP COLUMN categories")
    conn.commit()
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("SELECT categories FROM council_post")

    out = repo.init_db(conn)          # 기동을 흉내
    assert out["added"] == ["council_post.categories"], out
    conn.execute("SELECT categories FROM council_post")   # 이제 된다


def test_고친_뒤에_실제로_쓸_수_있다(conn):
    """컬럼만 붙이고 끝이 아니다 — upsert 가 통과해야 한다."""
    conn.execute("ALTER TABLE council_post DROP COLUMN categories")
    conn.commit()
    repo.init_db(conn)
    conn.execute("""INSERT INTO source_snapshot (id, source_key, url, fetched_at,
                    http_status, content_hash, content_path, media_type)
                    VALUES ('s','council_sheet','u','2026-08-14T09:00:00+09:00',
                            200,'h','','html')""")
    repo.upsert_council_posts(conn, [{
        "post_key": "k", "published_at": "2026-08-12", "title": "댄스제",
        "body": "본문", "link": "https://x", "deadline": "2026-08-25",
        "categories": "교내행사", "bureau": "무대운영국", "row_no": 2,
    }], source_id="s", source_url="u", observed_at="2026-08-14T09:00:00+09:00")
    conn.commit()
    got = conn.execute("SELECT categories FROM council_post").fetchone()[0]
    assert got == "교내행사"


# ═══════════════════════════════════════════════════════════════
# ★ 전수 — 다음에 칸이 늘어도 같은 자리에서 안 터지게
# ═══════════════════════════════════════════════════════════════

def _addable() -> list[tuple[str, str, str]]:
    out = []
    for table, cols in migrate.expected_columns(SCHEMA_SQL).items():
        for name, defn in cols.items():
            if not migrate._can_add(defn):
                out.append((table, name, defn))
    return out


def test_붙일_수_있는_컬럼은_전부_되살아난다(tmp_path):
    """스키마의 모든 컬럼을 하나씩 지워 보고 기동이 되살리는지 본다.

    ★ '이번에 추가한 컬럼' 만 재면 다음에 추가할 컬럼은 못 잡는다.
    """
    targets = _addable()
    assert len(targets) > 20, f"전수라기엔 너무 적다: {len(targets)}"
    failed = []
    for table, name, _defn in targets:
        c = repo.connect(tmp_path / f"t.db")
        repo.init_db(c)
        try:
            c.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
            c.commit()
        except sqlite3.OperationalError:
            c.close()
            (tmp_path / "t.db").unlink(missing_ok=True)
            continue          # 인덱스가 걸려 못 지우는 컬럼 — 재현이 안 된다
        repo.init_db(c)
        back = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
        if name not in back:
            failed.append(f"{table}.{name}")
        c.close()
        (tmp_path / "t.db").unlink(missing_ok=True)
    assert not failed, f"기동이 되살리지 못한 컬럼: {failed}"


def test_못_붙이는_컬럼은_이름을_남긴다(tmp_path):
    """★ 조용히 넘기면 다음 배포에서 또 터진다.

    SQLite 는 UNIQUE·PRIMARY KEY 컬럼, DEFAULT 없는 NOT NULL 을 못 붙인다.
    그런 게 새로 생기면 **사람이 손으로 옮겨야** 하고, 그걸 알아야 한다.
    """
    assert migrate._can_add("id TEXT PRIMARY KEY")
    assert migrate._can_add("x TEXT NOT NULL")
    assert migrate._can_add("y TEXT UNIQUE")
    assert not migrate._can_add("z TEXT NOT NULL DEFAULT ''")

    c = repo.connect(tmp_path / "s.db")
    repo.init_db(c)
    c.execute("CREATE TABLE tmp_t (a TEXT)")
    c.commit()
    out = migrate.apply(c, SCHEMA)
    assert out["skipped"] == [], out          # 지금 스키마엔 못 붙일 게 없다
    c.close()


def test_표가_없으면_여기_일이_아니다(tmp_path):
    """표 자체가 없으면 CREATE 가 만든다 — 컬럼 마이그레이션이 끼어들면 안 된다."""
    c = repo.connect(tmp_path / "n.db")
    repo.init_db(c)
    c.execute("DROP TABLE council_post")
    c.commit()
    todo = migrate.plan(c, SCHEMA_SQL)
    assert not [t for t in todo if t[0] == "council_post"]
    repo.init_db(c)
    assert c.execute(
        "SELECT name FROM sqlite_master WHERE name='council_post'").fetchone()
    c.close()


def test_스키마_파일이_없어도_안_죽는다(tmp_path):
    """★ 맞추려다 서버를 못 뜨게 하면 안 된다."""
    c = repo.connect(tmp_path / "x.db")
    repo.init_db(c)
    out = migrate.apply(c, tmp_path / "없는파일.sql")
    assert out == {"added": [], "skipped": []}
    c.close()
