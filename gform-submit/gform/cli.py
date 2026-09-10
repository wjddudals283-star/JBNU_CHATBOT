"""명령줄.

  python -m gform inspect  <폼주소>                          문항·entry id·선택지 출력
  python -m gform template <폼주소> -o answers.csv           CSV 헤더 생성
  python -m gform submit   <폼주소> answers.csv [--dry-run]  CSV 한 줄 = 응답 하나

submit 은 보내기 전에 모든 줄을 먼저 검증한다. 한 줄이라도 틀리면 한 건도 보내지 않는다 —
절반만 들어간 상태가 가장 고치기 어렵다.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Iterable

from .client import FormClient, SubmitResult
from .payload import PayloadError, build_payload, header_for, split_multi
from .schema import Form, QuestionType, SchemaError

TYPE_KO = {
    QuestionType.SHORT: "단답", QuestionType.PARAGRAPH: "장문", QuestionType.CHOICE: "객관식",
    QuestionType.DROPDOWN: "드롭다운", QuestionType.CHECKBOX: "체크박스", QuestionType.SCALE: "배율",
    QuestionType.GRID: "그리드", QuestionType.DATE: "날짜", QuestionType.TIME: "시각",
}


def describe(form: Form, out=None) -> None:
    out = out or sys.stdout   # 기본 인자에 sys.stdout 을 묶으면 캡처·리다이렉트가 안 먹는다
    print(f"폼: {form.title}   (id {form.form_id}, {form.page_count}페이지)", file=out)
    if form.collects_email:
        print("★ 이메일 수집 폼 — CSV 에 emailAddress 칸이 필요합니다", file=out)
    for q in form.inputs:
        req = "필수" if q.required else "선택"
        line = f"  {q.field_name:<16} {TYPE_KO.get(q.qtype, q.qtype.name):<5} {req}  {q.label}"
        print(line, file=out)
        if q.options:
            opts = " / ".join(q.options)
            print(f"      선택지: {opts}" + ("  (+기타 입력 가능)" if q.allows_other else ""), file=out)
        if q.qtype is QuestionType.DATE:
            print(f"      형식: {'YYYY-MM-DD' if q.date_has_year else 'MM-DD'}"
                  + (" HH:MM" if q.date_has_time else ""), file=out)
        if q.qtype is QuestionType.TIME:
            print(f"      형식: {'HH:MM:SS (기간)' if q.time_is_duration else 'HH:MM'}", file=out)
    for t in form.skipped:
        print(f"  ✗ 건너뜀(파일 업로드는 지원 안 함): {t}", file=out)


def read_rows(path: Path, form: Form, sep: str) -> list[tuple[int, dict]]:
    """CSV → (CSV 행 번호, {질문키: 값 또는 [값...]}). 첫 칸이 # 로 시작하는 줄은 메모로 보고 건넌다.

    행 번호는 헤더를 1행으로 센 실제 줄 번호다 — 건너뛴 줄이 있어도 오류 메시지가 파일의 그 줄을 가리켜야 한다."""
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise SchemaError("CSV 헤더가 없습니다")
        multi_cols = set()
        labels = {q.label: q for q in form.inputs}
        for col in reader.fieldnames:
            q = labels.get(col)
            if q is None and col.endswith("]"):
                # '제목 [entry.N]'
                m = re.search(r"\[entry\.(\d+)\]\s*$", col)
                q = form.by_entry(int(m.group(1))) if m else None
            if q is None and col.startswith("entry."):
                q = form.by_entry(int(col[6:])) if col[6:].isdigit() else None
            if q is not None and q.multi:
                multi_cols.add(col)
        rows = []
        for raw in reader:
            line_no = reader.line_num
            first = next(iter(raw.values()), None)
            if isinstance(first, str) and first.startswith("#"):
                continue
            row = {}
            for col, cell in raw.items():
                if col is None or cell is None:
                    continue
                cell = cell.strip()
                if cell == "":
                    continue
                row[col] = split_multi(cell, sep) if col in multi_cols else cell
            if row:
                rows.append((line_no, row))
        return rows


def build_all(form: Form, rows: list[tuple[int, dict]]) -> list[tuple[int, list[tuple[str, str]]]]:
    payloads, errors = [], []
    for i, row in rows:
        email = row.pop("emailAddress", None)
        try:
            payloads.append((i, build_payload(form, row, email=email if isinstance(email, str) else None)))
        except PayloadError as e:
            errors.append(f"  {i}행: {e}")
    if errors:
        raise SchemaError("CSV 검증 실패 — 한 건도 보내지 않았습니다\n" + "\n".join(errors))
    return payloads


def write_report(path: Path, results: Iterable[tuple[int, SubmitResult]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["csv_row", "ok", "status", "reason", "elapsed_s"])
        for row_no, r in results:
            w.writerow([row_no, int(r.ok), r.status, r.reason, f"{r.elapsed:.2f}"])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gform", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_ins = sub.add_parser("inspect", help="문항 구조 출력")
    p_ins.add_argument("url")

    p_tpl = sub.add_parser("template", help="CSV 헤더 생성")
    p_tpl.add_argument("url")
    p_tpl.add_argument("-o", "--output", type=Path, default=Path("answers.csv"))

    p_sub = sub.add_parser("submit", help="CSV 의 각 줄을 응답으로 제출")
    p_sub.add_argument("url")
    p_sub.add_argument("csv", type=Path)
    p_sub.add_argument("--dry-run", action="store_true", help="검증·페이로드 출력만. 보내지 않음")
    p_sub.add_argument("--sep", default="|", help="체크박스 여러 값 구분자 (기본 |)")
    p_sub.add_argument("--interval", type=float, default=2.0, help="제출 간 최소 간격(초). 기본 2")
    p_sub.add_argument("--limit", type=int, default=None, help="앞에서 N줄만")
    p_sub.add_argument("--report", type=Path, default=None, help="결과 CSV 경로")
    p_sub.add_argument("--yes", action="store_true", help="건수 확인 질문 생략")
    p_sub.add_argument("--stop-on-fail", action="store_true", help="첫 실패에서 중단")

    a = ap.parse_args(argv)
    try:
        with FormClient(min_interval=getattr(a, "interval", 0.0)) as client:
            form = client.fetch_form(a.url)
            if a.cmd == "inspect":
                describe(form)
                return 0
            if a.cmd == "template":
                with a.output.open("w", newline="", encoding="utf-8-sig") as f:
                    csv.writer(f).writerow(header_for(form))
                print(f"{a.output} 에 헤더 {len(header_for(form))}칸을 썼습니다. "
                      f"선택지는 `inspect` 로 확인하세요.")
                return 0
            return _submit(a, client, form)
    except SchemaError as e:
        print(f"오류: {e}", file=sys.stderr)
        return 2


def _submit(a, client: FormClient, form: Form) -> int:
    rows = read_rows(a.csv, form, a.sep)
    if a.limit is not None:
        rows = rows[: a.limit]
    if not rows:
        print("보낼 줄이 없습니다.", file=sys.stderr)
        return 1
    payloads = build_all(form, rows)
    print(f"폼 '{form.title}' — {len(payloads)}건 검증 통과")
    if a.dry_run:
        for i, p in payloads:
            print(f"--- {i}행")
            for k, v in p:
                print(f"  {k} = {v}")
        print("(dry-run: 보내지 않았습니다)")
        return 0
    if not a.yes:
        ans = input(f"{len(payloads)}건을 '{form.title}' 에 제출합니다. 계속? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("취소했습니다.")
            return 1
    results: list[tuple[int, SubmitResult]] = []
    ok = 0
    for i, p in payloads:
        r = client.submit(form, p)
        results.append((i, r))
        ok += int(r.ok)
        mark = "✓" if r.ok else "✗"
        print(f"  {mark} {i}행  {r.reason}  ({r.elapsed:.1f}s)")
        if not r.ok and a.stop_on_fail:
            print("첫 실패에서 중단했습니다.")
            break
    print(f"완료: {ok}/{len(results)} 성공")
    if a.report:
        write_report(a.report, results)
        print(f"결과: {a.report}")
    return 0 if ok == len(payloads) else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
