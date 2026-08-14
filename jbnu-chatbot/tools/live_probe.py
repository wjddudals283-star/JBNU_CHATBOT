"""46문항을 **배포 서버에 직접** 넣어 본다.

    set SKILL_TOKEN=...            (PowerShell:  $env:SKILL_TOKEN = "...")
    python tools/live_probe.py
    python tools/live_probe.py --out docs/live_46.md
    python tools/live_probe.py --freshness       원천별 마지막 성공만 본다

★ 로컬 DB 로 재지 않는다 (2026-08-14)
  로컬 스냅샷은 08-11 이고 서버 후보와 다른 걸 확인했다.
  학생이 받는 건 서버 응답이다. 로컬로 재면 우리는 존재하지 않는 챗봇을 재게 된다.
  이 파일은 **DB 를 열지 않는다** — 열 수 있는 코드를 두면 언젠가 그리로 샌다.

★ '어느 블록이 받았나' 를 카드 생김새로 짐작하지 않는다
  /admin/route 에 물어본다. 그건 handle() 이 실제로 쓰는 함수를 그대로 부른다.
  응답 모양으로 역추적하면 그건 관측이 아니라 추측이다.
  (서버가 그 엔드포인트를 아직 모르면 '-(구버전)' 으로 남긴다. 지어내지 않는다)

★ 토큰은 인자로 받지 않는다
  명령행에 쓰면 셸 히스토리에 남는다. 환경변수에서만 읽는다.

★ 판정 네 갈래 — 뭉쳐 세면 고칠 수 있는지 알 수가 없다
    ✅ 답함     기대값이 인용문 **안에** 있고, 그 인용문이 혼자서 말이 된다
    ⚠️ 반쪽     답은 나오는데 조각이거나(자족성 실패) 기대값이 경로에만 있다
    ❌ 못함     모른다고 했거나, 기대값이 어디에도 없다
    🔁 되물음    선택지나 형식 안내가 나갔다 — 정상일 수도, 오작동일 수도 있다

  ★ 🔁 를 ❌ 와 섞지 않는다. 되묻기는 실패가 아니라 **미완의 대화**다.
    다만 이 도구는 한 턴만 본다 — 되묻기가 옳았는지는 사람이 본다.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ★ 여기서 import 하는 것은 **판정기**뿐이다. 데이터는 서버에서만 온다.
from skill.selfcontained import is_self_contained          # noqa: E402
from tools.answerability_report import QUESTIONS           # noqa: E402

BASE = "https://jbnu-chatbot.onrender.com"
TOKEN_ENV = "SKILL_TOKEN"
HEADER = "X-Skill-Token"

# 첫 요청은 콜드 스타트에 걸릴 수 있다 — 한 번은 길게 기다려 준다.
FIRST_TIMEOUT = 60
TIMEOUT = 20
PAUSE = 0.4          # 배포본을 두드리는 속도. 학생 응답보다 우선일 수 없다.

# '모른다' 고 말한 문안. 정직한 실패다.
UNKNOWN_MARKS = ("못 찾았어요", "찾지 못했어요", "잘 모르겠어요",
                 "준비되지 않았어요", "확인하지 못했어요", "확인하지 못했습니다")
# 되묻기·형식 안내.
ASK_MARKS = ("골라 주시면", "여러 갈래로 나뉘어 있어요", "안내가 여러 곳에 있어요",
             "학과마다 달라요", "어느 식당을 볼까요", "어느 쪽인지")


def _post(path: str, body: dict, token: str, *, timeout: int) -> dict:
    req = urllib.request.Request(
        BASE + path, method="POST",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", HEADER: token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get(path: str, token: str, *, timeout: int) -> dict:
    req = urllib.request.Request(BASE + path,
                                 headers={HEADER: token})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def payload(utterance: str) -> dict:
    """카카오가 보내는 모양. 폴백 경로와 같게 — block 을 안 넣는다.

    ★ 실측: 폴백은 block 이름을 아예 안 보낸다. 학생 발화 대부분이 이 모양이다.
    """
    return {"userRequest": {"utterance": utterance, "user": {"id": "probe"}},
            "action": {"params": {}, "detailParams": {}}}


def flatten(resp: dict) -> tuple[str, list[str], str]:
    """응답 → (본문, 버튼 라벨, 출처 URL). 카드 종류가 달라도 같은 자리를 본다."""
    tpl = resp.get("template") or {}
    body_parts: list[str] = []
    source = ""
    for out in tpl.get("outputs") or []:
        if "simpleText" in out:
            body_parts.append(out["simpleText"].get("text", ""))
        elif "listCard" in out:
            card = out["listCard"]
            head = (card.get("header") or {}).get("title", "")
            items = " · ".join(i.get("title", "") for i in card.get("items") or [])
            body_parts.append(f"{head}: {items}")
            for b in (card.get("buttons") or []):
                source = source or b.get("webLinkUrl", "")
        elif "textCard" in out:
            c = out["textCard"]
            body_parts.append(f"{c.get('title','')} {c.get('description','')}")
    body = "\n".join(p for p in body_parts if p).strip()
    if not source:
        for tok in body.replace("\n", " ").split():
            if tok.startswith("http"):
                source = tok.rstrip(").,")
                break
    labels = [q.get("label", "") for q in tpl.get("quickReplies") or []]
    return body, labels, source


def quoted_part(body: str) -> str:
    """**학교 문서에서 옮겨 온 부분**만. 우리가 쓴 말은 뺀다.

    ★ 출처 줄을 빼는 이유
      '📄 전북대학교 본부 · 휴학 / 복학' 까지 넣고 재면
      조각도 자족처럼 보인다 — 자를 무디게 만든다.

    ★ 되읊는 첫 줄을 빼는 이유 (이게 더 중요하다)
      우리 템플릿은 "'수강신청'에 대한 안내예요." 로 시작한다.
      그 줄에는 학생이 물은 말이 **항상** 들어 있다.
      그걸 인용문으로 세면 '기대값이 인용문 안에 있나' 가 언제나 참이 된다 —
      자가 자기 자신을 재게 된다. 실제로 이 도구를 만들다 걸렸다.

    ★ 제목만 있는 줄도 뺀다
      '[조기시험]' 은 경로지 인용이 아니다. 경로가 맞다고 인용이 답인 건 아니다.
      ('[아침] 식단표에 …' 처럼 뒤에 내용이 붙은 줄은 남긴다)
    """
    keep = []
    for line in body.split("\n"):
        t = line.strip()
        if not t or t.startswith("http") or t.startswith("📄"):
            continue
        if t.startswith("(") and "확인" in t:
            continue
        if t.startswith("'") or t.startswith("“") or t.startswith("\""):
            continue                       # 우리가 질문을 되읊는 줄
        if t.startswith("[") and t.endswith("]"):
            continue                       # 제목만 있는 줄 = 경로
        keep.append(t)
    return "\n".join(keep)


def judge(body: str, labels: list[str], must: str, expect: str) -> tuple[str, str]:
    """네 갈래 판정. (기호, 이유)"""
    if not body:
        return "❌ 못함", "빈 응답"
    if any(m in body for m in ASK_MARKS):
        return "🔁 되물음", ("선택지 " + str(len(labels)) + "개" if labels else "형식 안내")
    if any(m in body for m in UNKNOWN_MARKS):
        # ★ 답하면 안 되는 문항이면 이건 성공이다. 그래도 칸은 '못함' 이 맞다 —
        #   학생이 받은 것은 '모른다' 이기 때문이다. 기대는 요약에서 따로 센다.
        return "❌ 못함", "모른다고 답함"

    quote = quoted_part(body)
    if must and must not in body:
        return "❌ 못함", f"기대값 '{must}' 이 응답에 없음"
    if must and must not in quote:
        # 경로·제목에만 있고 학생이 읽는 인용문에는 없다 = 조각
        return "⚠️ 반쪽", f"'{must}' 이 출처·제목에만 있음"
    if not is_self_contained(quote):
        return "⚠️ 반쪽", "인용이 혼자서 말이 안 됨"
    if expect == "defer":
        # 답하면 안 되는데 값이 나왔다. 판정은 '답함' 이지만 요약에서 경고로 센다.
        return "✅ 답함", "※ 보류 대상인데 답함 — 사람이 볼 것"
    return "✅ 답함", ""


def probe_one(q: str, must: str, expect: str, token: str, *,
              want_route: bool, timeout: int) -> dict:
    t0 = time.monotonic()
    try:
        resp = _post("/skill", payload(q), token, timeout=timeout)
    except urllib.error.HTTPError as e:
        return {"q": q, "route": "-", "body": f"HTTP {e.code}", "ask": "",
                "source": "", "mark": "❌ 못함", "why": f"HTTP {e.code}", "ms": 0}
    except Exception as e:                                   # noqa: BLE001
        return {"q": q, "route": "-", "body": f"{type(e).__name__}", "ask": "",
                "source": "", "mark": "❌ 못함", "why": str(e)[:60], "ms": 0}
    ms = round((time.monotonic() - t0) * 1000)

    route = "-(구버전)"
    if want_route:
        try:
            route = _post("/admin/route", payload(q), token,
                          timeout=TIMEOUT).get("route", "?")
        except Exception:                                    # noqa: BLE001
            route = "-(구버전)"

    body, labels, source = flatten(resp)
    mark, why = judge(body, labels, must, expect)
    return {"q": q, "route": route, "body": body, "ask": labels,
            "source": source, "mark": mark, "why": why, "ms": ms}


def _cell(s: str, n: int) -> str:
    """표 칸 하나. 줄바꿈과 파이프를 죽인다 — 안 그러면 표가 깨진다."""
    t = " ".join((s or "").split()).replace("|", "／")
    return t[:n] + ("…" if len(t) > n else "")


def freshness(token: str) -> int:
    """원천별 마지막 성공. '주 1회 갱신이 정상' 과 '스케줄러가 멈췄다' 를 가른다.

    ★ 인용에 찍히는 '확인 기준' 은 meal_service.observed_at 이고,
      그건 내용이 **바뀐** 시각이다. 내용이 같으면 갱신되지 않는다 (T5).
      그래서 그 도장만으로는 두 상태를 구별할 수 없다 — 여기를 봐야 한다.
    """
    st = _get("/admin/status", token, timeout=FIRST_TIMEOUT)
    if not st.get("ok"):
        print("✗ /admin/status 실패:", st.get("error"))
        return 1
    print(f"서버 시각  {st.get('now_kst')}")
    sch = st.get("scheduler") or {}
    if sch:
        print(f"스케줄러   {json.dumps(sch, ensure_ascii=False)[:200]}")
    print(f"\n{'원천':22} {'마지막 성공':22} {'stale':6}")
    print("─" * 56)
    for f in st.get("sources") or []:
        mark = "★STALE" if f.get("stale") else ""
        print(f"{f.get('source_key',''):22} "
              f"{str(f.get('last_success') or '-'):22} {mark}")
    stale = st.get("stale_sources") or []
    print()
    if stale:
        print(f"★ 오래된 원천 {len(stale)}개: {', '.join(stale)}")
    else:
        print("★ 오래된 원천 없음 — 스케줄러는 돌고 있다")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="마크다운 표를 파일로도 쓴다")
    ap.add_argument("--only", help="이 말이 든 문항만")
    ap.add_argument("--freshness", action="store_true",
                    help="원천별 마지막 성공만 본다")
    args = ap.parse_args(argv)

    token = (os.environ.get(TOKEN_ENV) or "").strip()
    if not token:
        print(f"✗ 환경변수 {TOKEN_ENV} 가 없습니다.")
        print("  PowerShell:  $env:SKILL_TOKEN = \"<Render 에 있는 값>\"")
        print("  ★ 명령행 인자로는 안 받습니다 — 셸 히스토리에 남습니다.")
        return 2

    if args.freshness:
        return freshness(token)

    items = [(t, q, e, m) for t, q, e, m in QUESTIONS
             if not args.only or args.only in q]

    # 라우트 엔드포인트가 이 배포본에 있는지 한 번만 본다.
    want_route = True
    try:
        _post("/admin/route", payload("확인"), token, timeout=FIRST_TIMEOUT)
    except Exception:                                        # noqa: BLE001
        want_route = False
        print("※ /admin/route 없음 — '어느 블록이 받았나' 는 비워 둡니다.")
        print("  (다음 배포 후 다시 돌리면 채워집니다. 짐작해서 적지 않습니다)\n")

    rows = []
    first = True
    for topic, q, expect, must in items:
        r = probe_one(q, must, expect, token,
                      want_route=want_route,
                      timeout=FIRST_TIMEOUT if first else TIMEOUT)
        first = False
        r["topic"] = topic
        rows.append(r)
        print(f"  {r['mark']}  {q:22} {r['route']:18} {r['ms']:>5}ms")
        time.sleep(PAUSE)

    lines = [f"# 배포 서버 46문항 실측 — {BASE}", "",
             "| 질문 | 어느 블록이 받았나 | 답변 첫 200자 | 되묻기 | 인용 출처 | 판정 |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        ask = f"○ {len(r['ask'])}개" if r["mark"].startswith("🔁") else "×"
        note = f" · {r['why']}" if r["why"] else ""
        lines.append(
            f"| {_cell(r['q'], 24)} | {_cell(r['route'], 20)} "
            f"| {_cell(r['body'], 200)} | {ask} "
            f"| {_cell(r['source'], 44)} | {r['mark']}{_cell(note, 40)} |")

    tally: dict[str, int] = {}
    for r in rows:
        tally[r["mark"]] = tally.get(r["mark"], 0) + 1
    lines += ["", "## 합계", "",
              " · ".join(f"{k} {v}" for k, v in sorted(tally.items())),
              "", f"문항 {len(rows)}개 · 평균 "
              f"{round(sum(r['ms'] for r in rows) / max(1, len(rows)))}ms"]

    out = "\n".join(lines)
    print("\n" + out)
    if args.out:
        pathlib.Path(args.out).write_text(out + "\n", encoding="utf-8")
        print(f"\n→ {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
