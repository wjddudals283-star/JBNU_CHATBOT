# 배포 가이드 — Render

작성일 2026-08-10 · 목표: 개강(9/1) 전에 상시 크롤을 켠다

---

## 0. 왜 지금 배포하는가

노트북 작업 스케줄러는 **노트북이 꺼져 있으면 안 돈다.** 개강 전후 3주는
운영시간 변화를 관측할 수 있는 유일한 창이고(§`term='unspecified'` 해소),
이걸 놓치면 다음 학기 경계까지 기다려야 한다. 가동 시간을 노트북에 걸 수 없다.

---

## 1. ★ 설계 변경 — Cron Job 을 쓰지 않는다

당초 계획은 "Render Cron Job 에서 `--due` 를 15분마다, DB 는 같은 디스크"였다.
**Render 에서는 불가능하다.**

Render 공식 문서 확인 결과:

- "A persistent disk is accessible by only a **single service instance**"
- "You **can't add a disk to a cron job** service"
- 디스크는 유료 web service / private service / background worker 에만 붙는다

즉 별도 Cron Job 은 웹 서비스의 SQLite 파일을 **볼 수가 없다.**

### 채택안 — 웹 서비스 안의 인프로세스 스케줄러

```
[Render Web Service · Starter · 1 instance]
   ├ uvicorn (카카오 스킬 응답)
   ├ SchedulerLoop 스레드 (15분마다 due_sources → 크롤)
   └ /var/data  (영구 디스크: jbnu.db + 스냅샷)
```

서비스 1개 · 디스크 1개 · 요금 1건. 인수인계도 그만큼 단순하다.
`render.yaml` 에 블루프린트로 박아뒀다.

> 3단계에서 Postgres 로 옮기면 그때 Cron Job 분리가 가능해진다.
> 지금 그러려면 Postgres 비용이 먼저 든다.

### 켜지 말아야 할 것

| 항목 | 이유 |
|---|---|
| **Autoscaling** | 정액이 아니게 된다. 그리고 인스턴스가 여럿이면 SQLite 디스크를 동시에 써서 **데이터가 깨진다** |
| `numInstances > 1` | 위와 같음. 반드시 1 |

---

## 2. ⚠️ 지출 상한 — Render 에는 범용 기능이 없다

대표 지시는 "지출 상한을 반드시 걸어라"였는데, **그 기능이 존재하지 않는다.**
확인 결과 Render 의 spend limit 은 **빌드 파이프라인 분(minutes)** 에만 적용되고,
컴퓨트/대역폭을 포함한 계정 전체 상한은 커뮤니티 요청 단계다.

없는 기능을 걸라고 안내하는 건 이 프로젝트가 막으려는 실패다. 대신 **실재하는** 통제:

| 통제 | 방법 |
|---|---|
| **정액 인스턴스** | Starter 는 시간당 정액. 트래픽이 늘어도 요금이 안 뛴다 (이게 주된 안전장치) |
| **빌드 분 상한** | Workspace → Billing → 파이프라인 분 spend limit 설정 |
| **Autoscaling 끄기** | 켜지 않는다. `render.yaml` 에 `numInstances: 1` 고정 |
| **결제 카드 한도** | 총학 명의 체크카드/한도 낮은 카드를 쓴다. 플랫폼이 못 막으면 카드로 막는다 |
| **월 1회 점검** | Dashboard → Billing 에서 실제 청구액 확인. 인수인계 문서에 넣을 것 |

> 예상 고정비: Web Service Starter **약 $7/월** + 디스크 1GB (별도 소액).
> 대역폭·빌드분은 이 규모에서 무료 한도 안이다.

---

## 3. 대표가 직접 해야 하는 것

로그인·결제·개인정보 입력은 대신 못 한다.

1. **총학 공용 계정으로** GitHub 저장소 생성 → `jbnu-chatbot/` 푸시
   - 개인 계정으로 시작하면 임기 종료 시 다 옮겨야 한다 (채널·챗봇과 같은 원칙)
2. Render 가입 (같은 공용 계정 / GitHub 연동)
3. 결제 카드 등록 — **총학 명의, 한도 낮은 카드**
4. Billing → 파이프라인 분 spend limit 설정
5. New → **Blueprint** → 저장소 선택 → `render.yaml` 자동 인식 → Apply
6. 배포 후 `https://<서비스>.onrender.com/health` 접속해서 확인

### 엔드포인트와 인증

| 경로 | 공개 여부 | 내용 |
|---|---|---|
| `GET /health` | **공개** | `{"ok": true}` 뿐. Render 헬스체크용 |
| `GET /admin/status` | 인증 | 크롤 건수 · 스케줄러 상태 |
| `GET /admin/freshness` | 인증 | 소스별 마지막 성공 · stale 여부 |
| **`POST /skill`** | 인증 | **카카오 스킬 — 이것 하나만 등록한다** |
| `POST /skill/{block}` | 인증 | 구 경로. 이미 등록한 것이 있으면 계속 동작 |
| `POST /admin/ingest` | 인증 | 노트북 백필 밀어넣기 |
| `GET /admin/blocks` | 인증 | 블록 매핑 · 매핑 안 된 블록 |

인증은 `X-Skill-Token` 헤더다. `render.yaml` 이 `generateValue: true` 로
256비트 랜덤 값을 자동 생성하므로 **저장소에 값이 남지 않는다.**

> **Fail closed** — `SKILL_TOKEN` 이 없으면 열어두지 않고 **503** 을 준다.
> "설정을 깜빡했다"가 곧 "누구나 호출 가능"이 되면 안 된다.
> `/health` 는 계속 200 이라 Render 가 배포를 실패로 보지 않는다.

배포 후 확인:

```bash
curl https://<서비스>.onrender.com/health
# {"ok":true}

curl -H "X-Skill-Token: <Dashboard 에서 복사>" \
     https://<서비스>.onrender.com/admin/status
# {"ok":true,"meal_service":95,"scheduler":{"ticks":3,...}}
```

- 인증 없이 `/admin/status` → **401** 이어야 정상
- 인증 있는데 **503** → `SKILL_TOKEN` 이 안 걸렸다
- `scheduler` 가 `null` → `RUN_SCHEDULER=1` 이 안 걸렸다
- `last_error` 가 채워짐 → 크롤이 실패 중. 로그 확인
- `ticks` 가 안 늘어남 → 스레드가 죽었다. 재배포

### 토큰을 오픈빌더에 넣기

Render Dashboard → 서비스 → Environment → `SKILL_TOKEN` 값 복사 →
챗봇 관리자센터 → 스킬 → 해당 스킬 → **헤더 추가**

```
X-Skill-Token: <복사한 값>
```

이 값은 **총학 공용 계정 외에는 공유하지 않는다.** 슬랙·카톡에 붙여넣지 말 것.

---

## 4. 배포 후 첫 주 점검

```bash
# 하트비트 — 24시간 성공 크롤이 없으면 exit 1
python -m crawler.schedule --heartbeat

# 운영시간 관측 이력 ★개강 전후로 이걸 본다
python -m crawler.schedule --hours-drift
```

`--hours-drift` 가 **9/1 전후로 변화를 보고하면** 그 시간표가 학기 의존적이라는 게
관측된 것이다. 변화가 없으면 학기 무관인 게 관측된 것이다. 어느 쪽이든
`term='unspecified'` 를 추론이 아니라 관측으로 해소한다.

> Render 셸에서 돌리려면 Dashboard → Shell 탭.

---

## 5. 안전 분기 — 현재 열림 (등급 `official_site`)

2026-08-10 총학생회가 각 기관 **공식 홈페이지에서 확인** 완료. 배포 가능 상태다.

| 기관 | 번호 | 등급 |
|---|---|---|
| 자살예방상담전화 (24시간) | 109 | official_site |
| 전북대 행복드림센터 | 063-219-5301 (진수당 1층) | official_site |
| 여성긴급전화 (24시간) | 1366 | official_site |
| 전북대 인권센터 | 063-270-3025 (진수당 154호) | official_site |

> 국가인권위원회(1331)는 공식 사이트에서 확인되지 않아 **목록에서 뺐다.**
> 확인 안 된 번호를 남겨두면 안전 분기 **전체**가 막혀 급한 사람에게 목록을 못 준다.

### ⚠️ 개강 전에 할 일 — 전북대 두 곳을 `phone` 등급으로

교내 부서는 개편이 잦다. 직접 전화해서 확인한 뒤 `verified_method` 를 올린다.

```yaml
verified_method: phone
verified_at: "2026-08-__"
```

`config/safety_contacts.yaml` 만 고쳐 커밋하면 된다 — 코드 변경이 필요 없다.

### 규칙

한 항목이 '확인됨'이 되려면 **넷**이 다 있어야 한다.
`verified` / `verified_at` / `verified_by`(직책) / `verified_method`.
하나라도 빠지면 **예외를 던진다** — 조용히 강등하지 않는다.
그리고 하나라도 미확인이면 **전체가 차단**된다(전부 아니면 전무).

```python
from skill import safety
safety.load().verification_worksheet()   # 등급까지 나온다
```

---

## 6. 카카오 채널 연결 (배포 후)

### ★ 스킬은 **하나만** 등록한다

```
https://<서비스>.onrender.com/skill
```

헤더에 `X-Skill-Token` 을 넣는다. **블록을 추가할 때 스킬을 새로 만들 필요가 없다** —
블록의 봇 응답에서 이 스킬을 드롭다운으로 고르기만 하면 된다.
토큰도 한 번만 넣는다.

분기는 서버가 `userRequest.block` 으로 한다(`config/blocks.yaml`).

### 새 블록을 만들었는데 폴백이 나오면

블록 이름이 매핑에 없는 것이다. **추측해서 아무 핸들러로 보내지 않기 때문이다** —
비슷한 이름이라고 대충 맞히면 새 블록이 조용히 엉뚱한 답을 한다.

```bash
curl -H "X-Skill-Token: ..." https://<서비스>.onrender.com/admin/blocks
```

`unmapped` 에 들어온 이름이 그대로 나온다. `config/blocks.yaml` 의 `handlers` 에
한 줄 추가하고 커밋하면 된다. 코드 변경은 필요 없다.

```yaml
handlers:
  food.menu.today:
    - 오늘 학식
    - 학식            # ← 여기에 추가
```

> **이름 대신 `block.id` 를 쓰는 게 더 안전하다.** 총학이 블록 이름을 바꾸면
> 이름 매핑은 끊기지만 id 는 안 바뀐다. 봇테스트 payload 에서
> `userRequest.block.id` 를 확인해 `blocks.yaml` 의 `ids` 에 넣으면 된다.

`03_카카오_개설_실행가이드.md` STEP 5 를 따른다. 스킬서버는 공개 HTTPS 여야 하는데
Render 가 기본 제공한다.

---

## 7. 알려진 제약

| 항목 | 내용 |
|---|---|
| 디스크 있는 서비스는 **무중단 배포 불가** | 재배포 시 몇 초 내려간다. 카카오는 그 사이 폴백 처리 |
| SQLite | 인스턴스 1개 전제. 3단계 Postgres 이전까지 유지 |
| 리전 | `singapore` — 한국에서 가장 가깝다. 응답 p95 는 로컬 기준 6.7ms 라 여유 있다 |
| 스모크 테스트 | `pytest -m smoke` 는 실사이트에 붙는다. 별도 주기로 수동 실행 |
