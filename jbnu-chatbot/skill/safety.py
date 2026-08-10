"""D11 안전 분기 — 인권·성폭력·긴급.

★ 이 분기는 **인텐트 분류보다 먼저** 돌아야 한다. 절대 뒤로 옮기지 말 것 (T13).
  정확성 문제가 아니라 안전 문제이고, 잘못된 안내의 대가가 다른 도메인과 비교가 안 된다.

설계 원칙 (01_설계.md §7)
  · 챗봇이 상담하려 들지 않는다. 사람에게 연결만 한다
  · 대화를 이어가지 않는다 — 후속 질문·추천질문으로 붙잡지 않는다
  · 이 분기는 자동화·학습 대상이 아니다. 키워드는 사람이 관리한다

연락처는 코드에 두지 않고 config/safety_contacts.yaml 에서 읽는다.
번호가 바뀌었을 때 배포 없이 고칠 수 있어야 한다.
"""

from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

import yaml

CONFIG_PATH = pathlib.Path(__file__).resolve().parents[1] / "config" / "safety_contacts.yaml"


class SafetyConfigError(ValueError):
    """안전 분기 설정이 스스로 모순된다. 조용히 넘기지 않는다."""


@dataclass(frozen=True)
class SafetyMatch:
    category: str      # emergency | violence | harassment | mental_health
    matched: str


class SafetyConfig:
    def __init__(self, doc: dict):
        self.categories: dict[str, dict] = doc["categories"]
        self.footer: str = doc.get("footer", "")
        self.unverified_fallback: str = doc.get("unverified_fallback", "").strip()
        self._patterns: list[tuple[str, re.Pattern]] = []
        # 위험도가 높은 범주를 먼저 검사한다 (긴급 > 폭력 > 괴롭힘 > 정신건강)
        for name in doc.get("priority", list(self.categories)):
            kws = self.categories[name]["keywords"]
            pat = re.compile("|".join(re.escape(k) for k in kws))
            self._patterns.append((name, pat))
        self._check_provenance()

    def match(self, utterance: str) -> SafetyMatch | None:
        # 공백을 지워 '성 폭력' 같은 우회를 막는다
        norm = re.sub(r"\s+", "", utterance or "")
        for name, pat in self._patterns:
            m = pat.search(norm)
            if m:
                return SafetyMatch(category=name, matched=m.group(0))
        return None

    # ── 배포 차단 ────────────────────────────────────────────────
    # 확인 등급. 위로 갈수록 강하다.
    #   official_site — 각 기관 공식 홈페이지에서 확인
    #   phone         — 직접 전화해서 확인 (최고 등급)
    VERIFY_METHODS = ("official_site", "phone")

    @staticmethod
    def _provenance_ok(c: dict) -> bool:
        """확인 이력이 갖춰졌는가.

        ★ `verified: true` 만으로는 부족하다. **누가 · 언제 · 어떻게** 확인했는지가
          있어야 한다. T4 레코드에 author/approved_by 를 강제한 것과 같은 이유다.
          출처 없는 검증은 검증이 아니라 주장이다.

        verified_method 를 같이 받는 이유 — 나중에 누가 봐도 **어느 수준의 확인인지**
        알 수 있어야 한다. 'official_site' 로 열어두고 나중에 'phone' 으로
        등급만 올리는 경로가 열린다.
        """
        return bool(str(c.get("verified_at") or "").strip()
                    and str(c.get("verified_by") or "").strip()
                    and str(c.get("verified_method") or "").strip())

    def _check_provenance(self) -> None:
        """verified: true 인데 확인 이력이 없으면 **예외**.

        조용히 '미확인'으로 강등하지 않는다. 절반만 채운 항목은 누군가 확인을
        시작했다가 멈춘 흔적이고, 그 상태로 배포되는 게 가장 위험하다.
        """
        for name, cat in self.categories.items():
            for c in cat["contacts"]:
                if c.get("verified", False) and not self._provenance_ok(c):
                    raise SafetyConfigError(
                        f"[{name}] {c['label']} — verified: true 인데 "
                        f"verified_at/verified_by/verified_method 중 빠진 게 있다. "
                        f"누가 언제 어떻게 확인했는지 없는 검증은 검증이 아니다."
                    )
                m = str(c.get("verified_method") or "").strip()
                if m and m not in self.VERIFY_METHODS:
                    raise SafetyConfigError(
                        f"[{name}] {c['label']} — 알 수 없는 verified_method={m!r}. "
                        f"허용: {self.VERIFY_METHODS}"
                    )

    def unverified_contacts(self) -> list[tuple[str, str]]:
        """총학이 직접 확인하지 않은 번호 목록. 하나라도 있으면 배포 불가."""
        out = []
        for name, cat in self.categories.items():
            for c in cat["contacts"]:
                if not (c.get("verified", False) and self._provenance_ok(c)):
                    phone = c.get("phone") or "(번호 미기입)"
                    out.append((name, f"{c['label']} {phone}"))
        return out

    def verification_worksheet(self) -> list[dict]:
        """총학이 전화를 걸며 채울 목록. 차단만 하지 않고 해제 경로를 준다."""
        seen: dict[str, dict] = {}
        for name, cat in self.categories.items():
            for c in cat["contacts"]:
                key = c["label"]
                if key in seen:
                    seen[key]["categories"].append(name)
                    continue
                seen[key] = {
                    "label": key,
                    "phone_candidate": c.get("phone") or "",
                    "note": c.get("note") or "",
                    "verified": bool(c.get("verified") and self._provenance_ok(c)),
                    "verified_at": c.get("verified_at") or "",
                    "verified_by": c.get("verified_by") or "",
                    "verified_method": c.get("verified_method") or "",
                    "categories": [name],
                }
        return list(seen.values())

    @property
    def deployable(self) -> bool:
        """연락처 목록을 내보내도 되는가.

        ★ 미확인 번호가 하나라도 있으면 False.
          번호별로 골라 내보내지 않는다 — 급한 사람에게 '일부만 맞는 목록'을
          주는 건, 어느 줄이 맞는지 판단하라고 떠넘기는 것이다.
        """
        return not self.unverified_contacts()

    def response_text(self, match: SafetyMatch) -> str:
        if not self.deployable:
            # 미확인 상태 — 번호를 나열하지 않는다. 사람에게 넘긴다.
            return self.unverified_fallback
        c = self.categories[match.category]
        lines = [c["lead"].strip(), ""]
        for x in c["contacts"]:
            note = f" ({x['note']})" if x.get("note") else ""
            lines.append(f"· {x['label']} {x['phone']}{note}")
        if self.footer:
            lines += ["", self.footer]
        return "\n".join(lines)


_config: SafetyConfig | None = None


def load(path: pathlib.Path | None = None) -> SafetyConfig:
    global _config
    if _config is None or path is not None:
        doc = yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8"))
        cfg = SafetyConfig(doc)
        if path is None:
            _config = cfg
        return cfg
    return _config


def is_sensitive(utterance: str) -> bool:
    return load().match(utterance) is not None


def check(utterance: str) -> SafetyMatch | None:
    return load().match(utterance)


def response(utterance: str) -> dict:
    """카카오 스킬 응답. **quickReplies 를 붙이지 않는다.**

    추천질문을 달면 대화를 이어가자는 신호가 된다. 여기서는 사람에게 넘기고 끝낸다.
    """
    m = load().match(utterance)
    if m is None:
        raise ValueError("안전 분기 대상이 아니다")
    return {
        "version": "2.0",
        "template": {"outputs": [{"simpleText": {"text": load().response_text(m)}}]},
    }
