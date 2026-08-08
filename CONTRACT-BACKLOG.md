# 계약 저장소나 TypeScript 축을 함께 고쳐야 닫히는 항목

이 문서는 이 저장소만으로는 닫을 수 없는 항목을 한자리에 모은다. 각 항목은 무엇이 막고 있는지와
계약의 어느 파일을 어떻게 고쳐야 하는지와 TypeScript 축에 무엇이 필요한지를 적는다.

## 이 문서가 여기 있는 이유

`CLAUDE.md`는 실행 구조 문서를 설명하는 코드와 같은 디렉터리의 `README.md`에 두고 `docs/`
디렉터리로 모으지 않는다고 정한다. 이 문서는 그 규칙의 대상이 아니다.

1. 실행 구조 문서가 아니다. 지금 코드가 하는 일이 아니라 **다른 저장소가 먼저 움직여야 하는 일**을
   적는다. 실행 구조 문서와 달리 이 문서의 항목은 코드와 어긋나 있는 것이 정상이다.
2. 항목이 `shared/`, `worker/agents/`, `worker/workflows/`, 그리고 계약 저장소와 TypeScript 축에
   걸쳐 있다. 이 문서를 소유할 코드 디렉터리가 하나가 아니다.
3. 저장소 뿌리는 이미 같은 성격의 `STRUCTURE-MIGRATION.md`를 갖는 자리다. 그 문서는 이 저장소가
   미룬 구조 변경을, 이 문서는 이 저장소가 **혼자서는 끝낼 수 없는** 변경을 갖는다.

새 디렉터리를 만들지 않고 `docs/`도 만들지 않고 저장소 뿌리에 둔다. 항목이 닫히면 그 절을 지운다.

## 읽는 법

경로는 저장소 뿌리 기준이다. `contract/`로 시작하는 경로는 계약 submodule 아래이며 이 문서를 쓴
시점의 revision 은 `e9355526`이다. 인용한 자리는 전부 실제 파일을 열어 확인했다. 실행하거나
재현한 것은 없다.

계약을 바꿀 때는 `CLAUDE.md`의 순서를 따른다 — 계약 선언과 적합성 케이스 → 계약 자체 검사 → 두
구현체 → 소비 표면 → 각 저장소 submodule revision → `agent-tracer-stack` 검증.

---

## 1. 계약이 선언해야 닫히는 항목

### 1.1 접수·조회가 원장에 닿지 못한 경우의 응답

- **막고 있는 것.** `LedgerUnavailable`(`src/tracer_agent/shared/agents/runtime/ledger.py:29`)과
  `AnchorUnavailable`(`src/tracer_agent/shared/workflows/jobs_anchor.py:23`)은 접수 창구의
  `except JobRejected`에 걸리지 않아 봉투 없는 500으로 나간다. 접수 경로가 선언한 상태는
  400·404·409뿐이다(`shared/workflows/jobs_intake.py:65`).
- **계약.** `contract/http/agent-api.openapi.yaml`이 접수·조회 경로에 5xx 오류 봉투를 선언한다.
  선언이 없으면 원장 타임아웃과 드라이버 오류가 계약 어휘 밖에서 같은 모양으로 떨어진다.
- **TypeScript.** 같은 경로가 원장 장애에서 무엇을 내는지 확인한 뒤 두 축을 같은 상태로 맞춘다.

### 1.2 `workerReport`에 `truncated`가 없다

- **막고 있는 것.** `ProbeReport.truncated`(`shared/agents/recipe_scan/models.py:135`)는 상한을 넘겨
  잘라 세운 보고를 예산 소진(`exhausted`)과 가르는 칸인데, 계약의 필수 칸 목록
  (`contract/agent/recipe-scan/tool.json:276-283`)은 `probe`·`verdict`·`excerpts`·`exhausted`만 갖는다.
- **계약.** 그 목록에 `truncated`를 더하거나, 자르지 않고 거절하는 쪽으로 뜻을 정한다.
- **TypeScript.** 같은 칸을 보고에 실어야 두 축의 빈 결과 사유가 같아진다.

### 1.3 인용할 수 없는 식별자의 접미사 문구

- **막고 있는 것.** `recipe_scan/prompts.py:168`이 `(+N more)`로 줄이는데, 모델은 그 N개를 인용할 수
  없다는 사실을 읽지 못한다.
- **계약.** 적합성 케이스 `contract/conformance/cases/recipe.prompt.json`이 그 문구를 고정하고 있어
  코드만 `(+N more, not citable in this run)`으로 바꾸면 러너가 깨진다. 케이스를 먼저 고친다.
- **TypeScript.** 같은 문장을 내야 하므로 함께 바꾼다.

### 1.4 모델에게 알리지 않는 수치 상한

- **막고 있는 것.** 스키마에만 있고 프롬프트에 없는 상한은 공급자가 강제하지 않으므로 모델이 지킬
  수단이 없다. 지금 그런 자리가 셋이다.
  - `RecipeCandidate.steps`의 `max_length=20`(`shared/agents/recipe_scan/models.py:272`)과 계약
    프롬프트가 적는 `1-10 entries`(`contract/agent/recipe-scan/prompt.json:101`)가 다르다.
  - `TriagePlan.assignments`의 `max_length=MAX_SUGGESTIONS`(50,
    `shared/agents/task_cleanup/models.py:19, 131-133`)는 후보를 그보다 많이 보여 준 뒤에 걸린다.
  - `use_when`·`inputs`·`outputs`와 `reason` 길이도 코드가 하드코딩한다.
- **계약.** 이 수들은 `contract/agent/{agent}/tool.json`의 `limits`가 갖고, 두 축이 그 값을 스키마와
  프롬프트에 함께 실어야 한다. 지금은 코드가 값을 소유해 두 축이 갈릴 수 있다.
- **TypeScript.** 같은 `limits`를 읽어 같은 상한을 프롬프트에 싣는다.

### 1.5 `providerBackstop`이 배수인지 덧셈인지

- **막고 있는 것.** 계약은 "공급자에게 넘기는 자기 상한은 이 **배수**만큼 위에 둔다"고 적고
  (`contract/agent/shared/execution.budget.json`의 `pacing.landingReserve`), 코드는 그 값을
  **더한다**(`worker/agents/runtime/llm/middleware_stack.py:60`). 지금 값이 2라 `maxTurns=2`에서만
  두 해석이 같다.
- **계약.** 한 문장으로 못박는다. 값을 읽는 코드는 이미 계약에서 읽으므로 문장만 정해지면 닫힌다.
- **TypeScript.** 이 이름이 TS 축에 없다. 계약이 뜻을 정한 뒤 그 축도 같은 상한을 세운다.

### 1.6 `weightAllocation.meaning`이 사라진 메서드 이름을 인용한다

- **막고 있는 것.** 계약의 그 문장이 `leaseMany(...)`라는 이름을 인용하는데 이 저장소에는 그 메서드가
  없다. 배분은 `runtime/llm/budget.py`의 `lease_shares`가 한다.
- **계약.** 문장을 동작 기준으로 다시 쓰면 두 축의 대조가 이름에 매이지 않는다.
- **TypeScript.** 없음. 문장만 바뀐다.

### 1.7 계약의 잡 접수 본문에 규칙 생성 입력이 남아 있다

- **막고 있는 것.** `contract/http/agent-api.openapi.yaml:1315`가 접수 본문의 `oneOf`에
  `RuleGenerationJobInput`을 두고 `:1323`이 그 스키마를 정의한다. 이 저장소는 규칙 생성 잡을 갖지
  않고 `contract/wire/job.kinds.json`도 `title.suggestion`·`recipe.scan`·`task.cleanup` 셋만 갖는다.
- **계약.** 두 자리를 지우거나, 그 종류를 되살릴 계획이 있으면 `job.kinds.json`에 함께 선언한다.
- **TypeScript.** 그 축이 이 입력을 아직 받는지 확인한 뒤 정한다.

### 1.8 타임라인 응답에서 필수인 칸

- **막고 있는 것.** `runtime/scoped_event_reader.py:35-40`의 `slim_event`가 `id`·`seq`·`kind`·
  `title`·`occurredAt`을 첨자로 읽는다. 상류가 그중 하나를 빼면 도구 안에서 `KeyError`가 난다.
  옆의 선택 칸은 `.get`으로 방어해 규약이 갈린다.
- **계약.** 그 다섯 칸의 필수 여부는 `contract/http/`의 타임라인 응답이 갖는다. 코드가 기본값을
  만들면 계약이 필수로 정한 칸을 구현이 선택으로 바꾸는 셈이다.
- **TypeScript.** 같은 응답을 읽는 자리를 함께 확인한다.

### 1.9 재개가 예약을 어느 잔량에 곱하는가

- **막고 있는 것.** 이어받은 실행의 `repair`·`survey` 예약이 남은 잔량의 비율로 다시 계산돼 원래
  약속된 몫보다 작아진다. 방향은 보수적이고 상한을 넘기지 않는다.
- **계약.** `execution.budget.json`의 `turnLedger.reservationReturn`은 정산의 되돌림만 말하고 재개
  때의 기준 잔량을 말하지 않는다. "수리는 언제나 최소 N턴을 갖는다"가 뜻이라면 그것을 적는다.
- **TypeScript.** 같은 상황에서 그 축이 내는 값을 먼저 확인한 뒤 정한다.

### 1.10 jobs 큐의 부팅 복구

- **막고 있는 것.** chat 큐만 부팅 스윕을 갖고 jobs·generate는 `_nothing_to_resume`이다
  (`worker/worker.py:211, 232-236`). `claim`은 됐는데 기동이 실패한 행이 영원히 `pending`으로 남는다.
- **계약.** 무엇을 언제 되살릴지는 `contract/workflow/queues.yaml`이 chat과 같은 뜻을 갖는지 먼저
  정해야 한다. 자세한 설계와 마이그레이션 순서는 `STRUCTURE-MIGRATION.md`의 P3이 갖는다.
- **TypeScript.** 그 축이 접수와 기동 사이에 보정을 갖는지 확인한 뒤 계약에 보장을 적을지 표를
  적을지 정한다.

### 1.11 생성 도중 취소가 부분 결과를 남기지 못한다

- **막고 있는 것.** 생성 액티비티가 도는 도중 취소가 닿으면 `GeneratedAgentJob`이 없어 워크플로에
  적을 산출·사용량 자체가 없다. 준비·종결 구간은 이미 `_closing`으로 감싸 완주한다.
- **계약.** `contract/workflow/queues.yaml`의 `generateAgentJob`·`settleCanceledAgentJob`이 갖는 뜻과
  결과 타입을 함께 고쳐야 취소가 부분 결과를 실을 수 있다.
- **TypeScript.** 같은 액티비티의 결과 타입을 함께 바꾼다.

### 1.12 승인 뒤 알림을 누구에게 보내는가

- **막고 있는 것.** `chat/surface/resolution.py:106`의 알림은 방금 세운 이어 말할 턴으로 간다. 앞선
  실행의 스트림을 열어 둔 다른 탭은 재전송 주기까지 옛 프레임을 본다. TS 축도 같은 순서다.
- **계약.** 알림 대상 규칙이 계약 어휘가 되어야 두 축이 같은 시점에 같은 연결을 깨운다.
- **TypeScript.** 규칙이 정해지면 함께 바꾼다.

### 1.13 `idleSeconds`가 낡았고 아무도 그 칸을 대조하지 않는다

- **막고 있는 것.** `contract/workflow/queues.yaml:43`은 스레드 워크플로의 유휴 상한을 `5`로
  선언하는데 **두 구현체가 모두 120초를 쓴다** — 이 축은 `shared/workflows/chat_spec.py:50`의
  `THREAD_IDLE_S = 120.0`, TypeScript 축은
  `services/agent-worker/src/domain/chat/model/chat.workflow.spec.ts:15`의
  `CHAT_THREAD_IDLE_TIMEOUT = "2 minutes"`다. 두 축이 갈린 것이 아니라 **계약 값 하나가 낡았다.**
- **왜 아무도 못 잡았나.** `idleSeconds`는 계약 전체에서 그 선언 한 줄에만 나오고
  `contract/conformance` 아래 어디에도 없다. 대조하는 적합성 케이스가 없으므로 러너가 통과해도
  이 칸은 검사되지 않는다.
- **계약.** 값을 `120`으로 고치고, 그 칸을 두 구현체의 상수와 비교하는 적합성 케이스를 함께 세운다.
  케이스 없이 값만 고치면 다음에 같은 방식으로 다시 낡는다.
- **TypeScript.** 값이 이미 120초라 코드 변경은 없고 새 케이스를 통과하는지만 확인한다.
- **딸린 자리.** `STRUCTURE-MIGRATION.md`의 P4 3단계와 P5는 배포 창을 이 값으로 재므로 5초가 아니라
  120초를 기준으로 읽어야 한다. 그 문서의 해당 대목은 이미 그렇게 고쳤다.

### 1.14 `requiredByAction`을 아무도 읽지 않는다

- **막고 있는 것.** `contract/agent/chat/tool.json`이 확인 도구 여섯 자리에 `requiredByAction`을
  선언한다(`:445`, `:502`, `:582`, `:663`, `:701`, `:734`). 그런데 이 저장소의 `src`·`tests` 어디도
  그 칸을 읽지 않고, TypeScript 축의 `services`·`libs` 어디도 읽지 않는다. 적합성 케이스
  `contract/conformance/cases/chat.tools.json:14`가 산문으로 그 뜻을 적을 뿐 값을 대조하지 않는다.
- **왜 문제인가.** action 마다 필요한 인자를 계약이 선언해 두었지만 강제하는 자리가 없어, 두 축이
  각자 자기 계획표로 인자를 고른다. 계약을 읽는 사람은 그 칸이 지켜진다고 믿는다.
- **두 갈래.** 둘 중 하나를 고른다. (a) 두 구현체가 그 칸을 읽어 action 별 인자 검사를 세우고
  적합성 케이스가 그것을 대조한다. (b) 칸을 계약에서 지우고 인자 검사를 도구 인자 스키마 하나로
  일원화한다. **그대로 두는 것이 가장 나쁘다** — 선언은 약속으로 읽히는데 지키는 코드가 없다.
- **TypeScript.** 어느 쪽을 고르든 두 축이 함께 움직인다.

---

## 2. `divergence.json`에 근거가 필요한 항목

작업 공간 규칙은 두 구현체의 차이에 `contract/conformance/cases/divergence.json`의 근거를 요구한다.
아래 셋은 지금 근거가 없다.

### 2.1 캐시 쓰기 단가가 두 축에서 다르다

- **무엇이.** 이 축은 모든 캐시 경계에 `ttl="1h"`를 걸고 캐시 쓰기를 입력의 2.0배로 센다
  (`shared/agents/envelope/catalog.py`, `runtime/llm/middleware_stack.py:47`). TS 축은 SDK 기본
  5분 캐시라 1.25배다. 값은 각자 자기 동작에 맞지만 같은 개념이 두 수를 낸다.
- **왜 문제인가.** 예산 상한이 같으므로 같은 작업에서 이 축이 더 일찍 끊기고, 추적 대시보드가 두
  축의 비용을 나란히 보이면 같은 일이 다른 값으로 보인다.
- **두 갈래.** (a) 캐시 수명과 배수표를 `contract/agent/shared/execution.budget.json`이 갖고 두 축이
  읽어 단가를 유도한다. (b) 지금 좁힐 수 없다면 `divergence.json`에 항목을 더해 근거를 적는다.

### 2.2 도구 재시도의 jitter

- **무엇이.** `runtime/llm/retry.py:24, 36`이 모델·도구 재시도 모두 `jitter=True`다.
  `divergence.json`의 `agent.model.retry`가 모델 재시도는 덮지만 도구 재시도를 덮는 항목은 없다.
- **결말.** 같은 입력의 실행 시간이 흔들린다. 사용자에게 가는 결과는 같다.

### 2.3 승인 결과를 읽는 이어 말할 턴

- **무엇이.** `chat/surface/resolution.py:132-138`의 `_follow_up`은 이미 `queued`인 턴이 있으면 새
  턴을 세우지 않는다. 그런데 재생은 그 턴의 `replay_anchor_message_id`까지로 창을 자르므로
  (`chat/surface/replay.py:51-56`) 앵커보다 뒤에 삽입된 도구 결과 줄은 그 턴의 문맥에도 들어가지
  않는다. 확인 두 개를 잇달아 승인하면 두 번째 결과를 모델이 읽지 못한다.
- **왜 여기 있나.** 옳은 규칙은 "승인이 남긴 결과 줄마다 그것을 앵커로 하는 턴을 세운다"인데,
  그러면 사용자에게 보이는 대화의 모양이 TS 축과 갈린다. TS는 아직 `queued`+`running`을 함께 본다.
- **정할 사람.** 계약 저장소를 갱신할 수 있는 쪽이 "두 축이 같은 모양을 갖는다"와 "차이를
  `divergence.json`에 적는다" 중 하나를 고른다.

---

## 3. TypeScript 축을 함께 고쳐야 하는 항목

### 3.1 장기기억 `key`가 검사 없이 신뢰 구역 안으로 렌더링된다

- **무엇이.** `content`는 `memory_rejection`이 지시문과 자격을 막지만
  (`chat/surface/memories.py:46`), 경로 인자 `key`는 길이 제한도 내용 검사도 없다. 그 값은 다음 턴에
  `- {key}: {content}`로 `<memory source="untrusted">` 안에 실린다
  (`worker/agents/chat/prompts.py:53-55`). `key`에 닫는 태그를 넣으면 구역이 데이터로 닫힌다.
- **왜 함께 고치나.** TS 축도 `key`를 검사하지 않고 같은 모양으로 렌더링한다. 한 축만 고치면 정본과
  갈리고 그 근거를 `divergence.json`에 적어야 한다.
- **두 갈래.** (a) `memory_rejection`을 `key`에도 걸고 계약의 `key` 스키마에 길이·패턴을 준다.
  (b) 렌더링이 `key`의 꺾쇠를 지우거나 값을 인용부호로 감싼다. (b)는 계약이 프롬프트 조각만
  소유하므로 조각 밖의 조립 규칙으로 정할 수 있다.
- **부수 차이.** TS는 경로 인자가 공백뿐이면 400으로 거절하고 이 축은 200으로 받는다.

### 3.2 `synthesisFloor.budgetShare`를 0이 아닌 값으로 올릴 때

- 이 축은 바닥 예약의 소모를 `floor_*` 채널에 적어 실행당 한 번으로 묶는다. 지금은 그 몫이 0이라
  달러에서는 드러나지 않는다. 계약이 0이 아닌 몫을 주면 TS 축도 같은 묶음을 세워야 한다.

### 3.3 아직 대조하지 못한 자리

- `recipe_scan/prompts.py`의 `build_user_prompt`가 적는 우선순위 문장이 TS 축의 같은 자리에
  반영됐는지 확인하지 못했다.
- TS 축의 갱신 SQL이 대화 실행의 축으로 거르는지, `ai_job_stage_outputs`에 어떤 `stage`·`slot`
  값을 쓰는지 확인하지 못했다. 두 항목은 `STRUCTURE-MIGRATION.md`의 P1·P4가 그 확인을 선행 조건으로
  적는다.
