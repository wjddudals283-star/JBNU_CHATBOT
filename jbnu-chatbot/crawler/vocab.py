"""어휘 사전을 만든다 — 붙여 쓴 말을 쪼개기 위한 **관측된 낱말**.

    python -m crawler.vocab                최신 코퍼스로 다시 만든다

★ 사전은 관측이다. 우리가 적는 목록이 아니다
  코퍼스에 **공백으로 구분되어 실제로 나타나는** 낱말을 센다.
  '어떤 낱말이 있을까' 를 떠올려 적으면 안 떠올린 것은 영영 안 들어온다.

★ 조사는 빈도로 진다 — 가설이 아니라 실측이다
  '성적이' 같은 조사 붙은 형태도 사전에 들어온다. 그래도 분해에서 진다.
      최장일치     성적이 | 의신청      ← 앞에서부터 긴 것을 떼니 조사가 붙는다
      DF 가중      성적 | 이의 | 신청   ← 흔한 낱말이 이긴다
  87,607문단에서 낱말 132,751종을 세어 확인했다.

★ 답변 경로는 **조회만** 한다
  만드는 데 8초쯤 걸린다. 학생을 8초 기다리게 할 수 없다 (T7: p95 < 300ms).
  그래서 크롤 단계에서 만들어 표에 넣고, 답변할 때는 읽기만 한다.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import logging
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from store import repo  # noqa: E402

log = logging.getLogger("jbnu.crawler.vocab")
KST = dt.timezone(dt.timedelta(hours=9))

# 낱말 경계. 검색 토큰화와 같은 규칙을 쓴다 — 다르면 사전에 없는 걸 찾게 된다.
SPLIT = re.compile(r"[^0-9A-Za-z가-힣+]+")
MIN_LEN, MAX_LEN = 2, 20
# ★ 한 번만 나온 말은 오타일 수 있다. 셋은 관측이라고 부를 만한 최소치다.
MIN_DF = 3


def build(conn, *, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(KST)
    df: collections.Counter = collections.Counter()
    n = 0
    for (txt,) in conn.execute("SELECT COALESCE(raw_text, text) FROM page_section"):
        if not txt:
            continue
        n += 1
        seen = {w for w in SPLIT.split(txt) if MIN_LEN <= len(w) <= MAX_LEN}
        df.update(seen)
    kept = {w: d for w, d in df.items() if d >= MIN_DF}
    stamp = now.isoformat()
    conn.execute("DELETE FROM term")
    conn.executemany("INSERT INTO term (term, df, built_at) VALUES (?,?,?)",
                     [(w, d, stamp) for w, d in kept.items()])
    conn.commit()
    out = {"paragraphs": n, "terms_seen": len(df), "terms_kept": len(kept)}
    log.info("[vocab] 문단 %s · 낱말 %s종 · DF>=%s 로 %s종 남김",
             n, len(df), MIN_DF, len(kept))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    from crawler.run import DB_PATH
    conn = repo.connect(pathlib.Path(args.db) if args.db else DB_PATH)
    try:
        repo.init_db(conn)
        for k, v in build(conn).items():
            print(f"  {k}: {v:,}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
