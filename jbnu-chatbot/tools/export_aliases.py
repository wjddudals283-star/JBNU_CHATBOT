"""aliases.yaml → 오픈빌더 엔티티 등록용 출력.

    python tools/export_aliases.py            # 보기 좋은 표
    python tools/export_aliases.py --csv      # CSV (대표어,동의어1,동의어2,…)
    python tools/export_aliases.py --plain    # 대표어 다음 줄에 동의어 (붙여넣기용)
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout.reconfigure(encoding="utf-8")

from skill import aliases as al  # noqa: E402

GROUPS = {"outlet": "facility", "meal_type": "meal_type"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", action="store_true")
    ap.add_argument("--plain", action="store_true")
    ap.add_argument("--group", choices=list(GROUPS), default=None)
    args = ap.parse_args(argv)

    doc = al.load()
    targets = [args.group] if args.group else list(GROUPS)

    for entity_name in targets:
        group = doc.get(GROUPS[entity_name]) or {}
        if not args.csv and not args.plain:
            print(f"\n=== 엔티티: {entity_name} ===")
            print("  오픈빌더 → 엔티티 → 엔티티 추가 → 아래를 대표어/동의어로 등록\n")

        for _cid, spec in group.items():
            canonical = spec["canonical"]
            syn = [a for a in (spec.get("aliases") or []) if a != canonical]
            if args.csv:
                print(",".join([canonical, *syn]))
            elif args.plain:
                print(f"{canonical}\t{', '.join(syn)}")
            else:
                print(f"  {canonical:12} ← {', '.join(syn)}")

    if not args.csv and not args.plain:
        print("\n※ 엔티티를 등록해도 오픈빌더는 **발화에 태깅된 경우에만** params 를 채운다.")
        print("  학생 자유 발화는 태깅이 안 되므로, 스킬서버가 utterance 에서")
        print("  같은 사전으로 보완한다 (skill/aliases.py). 둘이 같은 파일을 본다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
