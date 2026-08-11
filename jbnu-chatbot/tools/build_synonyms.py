"""관측된 제목 동의어를 config/title_synonyms.yaml 로 내보낸다.

★ 관측이 원천이고, 사람이 끌 수 있다
  자동으로 뽑되 근거(사이트 수·본문 겹침)를 함께 적는다.
  이상한 묶음이 보이면 disabled 에 넣으면 된다 — 코드를 고칠 필요 없다.
  관측을 지우는 게 아니라 **쓰지 않기로 표시**하는 것이므로,
  다음에 다시 뽑아도 그 판단이 남는다.

    python tools/build_synonyms.py --db data/jbnu.db
"""

from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from crawler import synonyms  # noqa: E402
from store import repo  # noqa: E402

OUT = ROOT / "config" / "title_synonyms.yaml"
KST = dt.timezone(dt.timedelta(hours=9))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--min-sites", type=int, default=synonyms.MIN_SITES)
    args = ap.parse_args(argv)

    old = {}
    if OUT.exists():
        old = yaml.safe_load(OUT.read_text(encoding="utf-8")) or {}
    disabled = list(old.get("disabled") or [])

    conn = repo.connect(args.db)
    try:
        groups = synonyms.observe(conn, min_sites=args.min_sites, verbose=True)
    finally:
        conn.close()

    doc = {
        "note": ("제목 동의어 — 관측으로 뽑았다. 손으로 적은 목록이 아니다.\n"
                 "신호 셋: 어휘 겹침 · 상보 분포(한 사이트에 둘 다 없음) · 본문 겹침.\n"
                 "이상한 묶음은 disabled 에 label 을 넣으면 쓰지 않는다."),
        "generated_at": dt.datetime.now(KST).isoformat(),
        "signals": {
            "min_sites": args.min_sites,
            "max_host_overlap": synonyms.MAX_HOST_OVERLAP,
            "min_content": synonyms.MIN_CONTENT,
        },
        # ★ 사람이 끈 것은 그대로 유지한다. 다시 뽑아도 판단이 살아남는다.
        "disabled": disabled,
        "groups": [
            {"label": g["label"], "members": g["members"],
             "sites": g["site_count"], "content": g["content_min"]}
            for g in groups
        ],
    }
    OUT.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                   encoding="utf-8")
    print(f"그룹 {len(groups)} · 끈 것 {len(disabled)} → {OUT}")
    for g in groups[:8]:
        print(f"  [{g['site_count']:3}곳] {' ≡ '.join(g['members'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
