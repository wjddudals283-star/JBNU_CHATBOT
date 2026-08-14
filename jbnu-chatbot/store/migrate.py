"""스키마 파일과 **디스크에 있는 DB** 를 맞춘다.

★ 같은 종류가 두 번 나왔다 (2026-08-14)
    1차   council_post **표**가 없어서 `no such table` — 기동 때 맞추도록 막았다
    2차   council_post.categories **컬럼**이 없어서 `no such column`
          분류 칸을 붙이면서 컬럼을 추가했는데, 표는 이전 배포 때 만들어졌고
          디스크에 남아 있었다.

  `CREATE TABLE IF NOT EXISTS` 는 **이미 있는 표에는 아무것도 안 한다.**
  그래서 표는 막았는데 컬럼은 안 막혔다.
  두 번째면 개별로 고칠 일이 아니다 — 다음에 칸이 하나 더 늘면 또 같은 자리다.

★ 스키마 파일이 유일한 진실이다
  '어떤 컬럼이 있어야 하나' 를 코드에 또 적으면 두 벌이 되고, 두 벌은 갈라진다.
  schema.sql 을 읽어서 **거기 적힌 대로** 맞춘다.

★ 못 고치는 건 조용히 넘기지 않는다
  SQLite 의 ALTER TABLE ADD COLUMN 은 못 하는 게 있다 —
  UNIQUE·PRIMARY KEY 컬럼, 상수가 아닌 DEFAULT, DEFAULT 없는 NOT NULL.
  그런 건 손대지 않고 **이름을 남긴다.** 조용히 넘기면 다음 배포에서 또 터진다.
"""

from __future__ import annotations

import logging
import pathlib
import re
import sqlite3

log = logging.getLogger("jbnu.store.migrate")

# 컬럼이 아니라 제약인 줄들. 이걸 컬럼으로 세면 엉뚱한 ALTER 가 나간다.
_CONSTRAINT = re.compile(
    r"^(PRIMARY|UNIQUE|CHECK|FOREIGN|CONSTRAINT)\b", re.IGNORECASE)
_CREATE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?(\w+)[\"'`]?\s*\(",
    re.IGNORECASE)


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _split_top_level(body: str) -> list[str]:
    """괄호 깊이를 세며 최상위 쉼표로만 자른다.

    ★ CHECK(...) 안에도 쉼표가 있다. 그냥 split(',') 하면 조각이 난다.
    """
    out, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return out


def expected_columns(schema_sql: str) -> dict[str, dict[str, str]]:
    """schema.sql → {표이름: {컬럼이름: 정의}}"""
    sql = _strip_comments(schema_sql)
    out: dict[str, dict[str, str]] = {}
    for m in _CREATE.finditer(sql):
        table = m.group(1)
        # 여는 괄호부터 짝이 맞는 닫는 괄호까지
        i, depth = m.end(), 1
        while i < len(sql) and depth:
            if sql[i] == "(":
                depth += 1
            elif sql[i] == ")":
                depth -= 1
            i += 1
        body = sql[m.end():i - 1]
        cols: dict[str, str] = {}
        for part in _split_top_level(body):
            part = " ".join(part.split())
            if not part or _CONSTRAINT.match(part):
                continue
            name = part.split()[0].strip('"\'`')
            cols[name] = part
        if cols:
            out[table] = cols
    return out


def _can_add(defn: str) -> str:
    """ALTER TABLE ADD COLUMN 으로 붙일 수 있나. 못 붙이면 이유를 돌려준다."""
    d = defn.upper()
    if "PRIMARY KEY" in d:
        return "PRIMARY KEY 컬럼은 추가할 수 없다"
    if "UNIQUE" in d:
        return "UNIQUE 컬럼은 추가할 수 없다"
    if "NOT NULL" in d and "DEFAULT" not in d:
        return "NOT NULL 인데 DEFAULT 가 없다"
    if "REFERENCES" in d and "DEFAULT" in d:
        # 기본값이 있는 외래키를 붙이면 기존 행이 없는 부모를 가리키게 된다
        return "REFERENCES + DEFAULT 는 기존 행을 깨뜨린다"
    return ""


def plan(conn: sqlite3.Connection, schema_sql: str) -> list[tuple[str, str, str]]:
    """(표, 컬럼, 정의) — 디스크에 없는 컬럼만."""
    want = expected_columns(schema_sql)
    have_tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    todo: list[tuple[str, str, str]] = []
    for table, cols in want.items():
        if table not in have_tables:
            continue        # 표 자체가 없으면 CREATE 가 만든다 — 여기 일이 아니다
        actual = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, defn in cols.items():
            if name not in actual:
                todo.append((table, name, defn))
    return todo


def apply(conn: sqlite3.Connection,
          schema_path: pathlib.Path) -> dict[str, list[str]]:
    """빠진 컬럼을 붙인다. 붙인 것과 못 붙인 것을 둘 다 돌려준다.

    ★ 붙이는 것만 세면 못 붙인 게 안 보인다. 침묵이 가장 위험하다.
    """
    out: dict[str, list[str]] = {"added": [], "skipped": []}
    try:
        schema_sql = schema_path.read_text(encoding="utf-8")
    except OSError as e:
        log.error("[migrate] 스키마 파일을 못 읽었다 — %s", e)
        return out

    for table, name, defn in plan(conn, schema_sql):
        why = _can_add(defn)
        if why:
            out["skipped"].append(f"{table}.{name} ({why})")
            log.error("[migrate] 못 붙임 %s.%s — %s", table, name, why)
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {defn}")
            out["added"].append(f"{table}.{name}")
            log.info("[migrate] 컬럼 추가 %s.%s", table, name)
        except sqlite3.OperationalError as e:
            out["skipped"].append(f"{table}.{name} ({e})")
            log.error("[migrate] 추가 실패 %s.%s — %s", table, name, e)
    if out["added"]:
        conn.commit()
    return out
