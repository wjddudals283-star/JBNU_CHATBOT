# 전북대학교 총학생회 카카오톡 챗봇

학생 생활 전반을 안내하는 카카오톡 챗봇. 식단은 12개 도메인 중 하나다.

> **이 프로젝트의 핵심은 "정확한 답"이 아니라 "모를 때 모른다고 말하는 것"이다.**
> 추측한 값을 내놓지 않는 것이 기능이다.

---

## 설계 원칙

| 원칙 | 뜻 |
|---|---|
| 출처 없는 사실은 저장할 수 없다 | 모든 fact 테이블에 출처 메타 6개가 NOT NULL |
| 저장하는 건 관측, 결합은 질의 계층 | 파싱 시점에 추론한 결론을 굳히지 않는다 |
| 추론으로 채운 값은 사실이 아니다 | 가격 조인은 정확 일치만. 미매칭은 NULL |
| 관측의 부재는 관측이 아니다 | 빈 칸은 `unknown`. "미운영"이라고 단정하지 않는다 |
| 부정도 관측이다 | "주말 미운영"을 행으로 남긴다. 부재로 표현하지 않는다 |
| 긍정 단정에는 높은 근거를 | 헛걸음을 만드는 오류가 훨씬 비싸다 |
| 이력을 쌓는 테이블은 식별자에 시점이 있어야 한다 | 같은 대상의 다른 시점 관측은 다른 레코드다 |

자세한 근거는 [`01_정보정확성_설계.md`](01_정보정확성_설계.md),
구현 계약은 [`02_클로드코드_핸드오프.md`](02_클로드코드_핸드오프.md).

---

## 구조

```
[카카오 오픈빌더]  발화 → 블록 매칭 + 엔티티 추출     ← 플랫폼이 한다
        ↓ action.params
[스킬서버]        params → 조회 → 게이트 → 템플릿    ← 순수 결정론
        ↑
[크롤러]          T1~T4 데이터 공급
```

**실시간 답변 경로에 생성 모델이 없다.** 방침이 아니라 구조다.

---

## 답변 4분기

| 상황 | 응답 |
|---|---|
| **A** 메뉴가 관측됨 | 값 + 출처 링크 + 관측 시각 |
| **B** 미운영이 관측됨 | "운영하지 않아요" + 근거(운영시간) |
| **C-1** 운영은 하는데 메뉴만 없음 | "아직 올라오지 않았어요" |
| **C-2** 모름 | "확인하지 못했어요" + 원문 링크 |

---

## 원천

| 원천 | 방식 | 역할 |
|---|---|---|
| 생협 | `get_cafeteria_menu.php` (JSON) | **1차.** 날짜 지정 가능 → 백필 가능 |
| 학교 | `dataAjax.do?type=day` (HTML) | 2차 + 가격 + 운영시간 |
| 생활관 | `inner.php?sMenu=B7100` (HTML) | 기숙사 식당 |

두 원천이 같은 주를 독립 제공하므로 교차검증이 켜져 있다.
실측 기준선: 내용 불일치 **0.0%**, 커버리지 차이 11.1%.

---

## 실행

```bash
pip install -r jbnu-chatbot/requirements.txt
cd jbnu-chatbot

python -m crawler.run --source coop_week_menu        # 크롤
python -m crawler.run --list                          # 원천 목록
python -m crawler.schedule --due                      # 스케줄러 (15분마다 호출)
python -m crawler.schedule --heartbeat                # 침묵 감지
python -m crawler.schedule --hours-drift              # 운영시간 변화 관측

uvicorn skill.server:app --reload                     # 스킬서버
```

### 테스트

```bash
python -m pytest tests            # 162개. 스모크 제외
python -m pytest tests -m smoke   # 실사이트에 실제로 붙는다
```

---

## 배포

[`jbnu-chatbot/docs/DEPLOY.md`](jbnu-chatbot/docs/DEPLOY.md) 참고. Render Blueprint(`render.yaml`)로 배포한다.

> **주의** — `autoscaling`을 켜지 말 것. 정액이 깨지는 것도 문제지만,
> 인스턴스가 여럿이면 SQLite 디스크를 동시에 써서 데이터가 깨진다.

---

## 안전 분기 (D11)

인권·성폭력·긴급 발화는 **인텐트 분류보다 먼저** 가로채 사람에게 연결한다.
챗봇이 상담하지 않고, 대화를 이어가지 않는다(추천질문 없음).

연락처는 넷을 다 갖춰야 '확인됨'이 된다 —
`verified` / `verified_at` / `verified_by`(직책) / `verified_method`.
**하나라도 미확인이면 전체가 차단**되고 "총학에 직접 문의"만 나간다.

현재: 2026-08-10 공식 홈페이지 확인 완료(`official_site`), 열림.
개강 전 전북대 두 곳은 전화 확인으로 `phone` 등급 승급 예정.

```bash
python -c "from skill import safety; [print(x) for x in safety.load().verification_worksheet()]"
```

## 인증

`/skill/*`, `/admin/*` 은 `X-Skill-Token` 헤더가 필요하다.
토큰이 설정 안 되면 **열지 않고 503** 을 준다(fail closed).
`/health` 만 공개이며 `{"ok": true}` 외에는 아무것도 담지 않는다.

---

## 인수인계 주의

- 채널·챗봇·저장소·Render 계정은 **총학 공용 계정**으로 둔다. 개인 계정이면 임기 종료 시 전부 옮겨야 한다.
- `verified_by`에는 실명이 아니라 **직책**을 쓴다. 이 저장소는 Public이다.
- 월 1회 Render 청구액을 확인한다. 계정 단위 지출 상한 기능이 없다.
