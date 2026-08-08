# 미룬 구조 변경과 마이그레이션 순서

이 문서는 지금 착수하지 않기로 한 네 가지 구조 변경의 설계와 마이그레이션 순서를 갖는다.
착수 결정이 나면 각 절이 그 변경의 계획서가 되고, 변경이 끝나면 그 절을 지운다.

## 이 문서가 여기 있는 이유

`CLAUDE.md`는 실행 구조 문서를 설명하는 코드와 같은 디렉터리의 `README.md`에 두고 `docs/`
디렉터리로 모으지 않는다고 정한다. 같은 규칙이 실행 구조 문서는 지금 코드가 하는 일만 적으라고
정한다. 이 문서는 그 규칙의 대상이 아니다. 세 가지 이유가 있다.

1. 이 문서는 지금 코드가 하는 일이 아니라 앞으로의 변경 계획이다. 규칙이 지키려는 것은 문서와
   코드가 어긋나지 않는 것인데, 계획 문서는 코드와 어긋나 있는 것이 정상이다.
2. 네 항목이 `worker/workflows/`, `shared/agents/chat/`, `worker/agents/runtime/`, 그리고
   저장소 밖의 배포에 걸쳐 있다. 이 문서를 소유할 코드 디렉터리가 하나가 아니다.
3. 저장소 뿌리는 이미 여러 층에 걸친 문서 `ARCHITECTURE.md`를 갖는 자리다.

그래서 새 디렉터리를 만들지 않고 `docs/`도 만들지 않고 저장소 뿌리에 둔다. 착수한 항목의
결과는 해당 코드 디렉터리의 `README.md`와 `ARCHITECTURE.md`가 받고 이 문서에서는 사라진다.

## 읽는 법

경로는 저장소 뿌리 기준이다. `contract/`로 시작하는 경로는 계약 submodule 아래이며 이 문서를
쓴 시점의 revision 은 `e9355526`이다. 저장소의 마지막 commit 은 `51a0183`이고 그 위에 아직
커밋되지 않은 변경이 있다. 줄번호는 그 작업 트리의 것이며, 움직이는 파일은 심벌 이름을 함께 적는다.

계약이 소유한 값은 여기 옮겨 적지 않고 어느 파일의 어느 줄인지만 가리킨다.

인용한 사실은 전부 실제 파일을 읽어 확인했다. 실행하거나 재현한 것은 없다. 확인하지 못한 것은
마지막 절이 따로 갖는다.

감사 D 가 좁힌 일곱 항목 중 둘은 이 문서가 다루지 않는다. P2(chat 바깥 StateGraph 제거)는 그 뒤
착수해 끝났고 지금 코드는 `chat/agent.py`가 `chat/steps/`의 단계를 차례로 부른다 — 그 결과는
`ARCHITECTURE.md`와 `chat/README.md`가 갖는다. P7a(캐시 쓰기 단가)는 두 축의 단가가 갈린 자리이고
계약 저장소를 함께 고쳐야 닫히므로 `CONTRACT-BACKLOG.md`가 갖는다.

---

## P1 — 내구 실행 엔진이 세 벌이고 계약이 소유한 재개 표가 죽어 있다

### 지금 코드가 하는 일

끝난 일을 다시 하지 않는 장치가 세 벌이고 그중 둘만 프로세스 경계를 넘는다.

| 장치 | 열쇠 | 위치 | 프로세스를 넘는가 |
| --- | --- | --- | --- |
| Temporal 액티비티 재시도 | `workflowId` | `contract/workflow/queues.yaml:141-147` | 예 |
| LangGraph 체크포인트 재개 | `executionId or jobId` | `src/tracer_agent/worker/agents/runtime/graph_session.py:53` | 예, 단 계약 밖 스키마 |
| 원장의 조건부 갱신 | 행 하나 | `src/tracer_agent/shared/agents/chat/execution_ledger.py` | 예 |

- 잡의 재개는 LangGraph 체크포인트가 갖는다. `graph_session.py:53-54`가 `executionId or jobId`를
  열쇠로 세이버를 열고, `runtime/durable_graph.py:92-94`의 `resume_input`이 이어받는 실행에
  `None`을 넣어 끝난 노드를 건너뛴다.
- 그 체크포인트의 쓰기 시점은 `durable_graph.py:17` `_JOB_DURABILITY = "async"`다.
- **프로세스 로컬 멱등 dict 는 이 문서를 쓴 뒤 지워졌다.** `runtime/execution/registry.py`가 통째로
  사라졌고 잡 단위 멱등은 `src/tracer_agent/shared/workflows/jobs_dispatch.py:36`의
  `REJECT_DUPLICATE`와 `shared/workflows/jobs_enqueue.py`의 `JobIntake.claim`이 갖는다. 아래
  마이그레이션 순서에서 그 dict 를 지우는 몫은 이미 끝난 것으로 읽는다.
- **계약이 소유한 재개 표를 쓰지 않는다.** `ai_job_stage_outputs`는
  `contract/db/migrations/0008-job-stage-output.sql:4-11`이 정의하고
  `contract/conformance/enforcement.json:15`가 `db/`를 `enforced`로 둔다. 그런데 `src`와 `tests`
  전체에서 이 표를 읽거나 쓰는 자리가 없다.
- **잡의 체크포인트를 아무도 지우지 않는다.** `runtime/checkpoint.py:55-63`의 `forget` 은
  존재하지만 호출자는 `worker/agents/chat/agent.py:86`(`run_chat`) 하나뿐이고 그것은 chat 경로다.
- 계약이 이 갈라짐을 이미 적었다. `contract/conformance/cases/divergence.json:24-30`의
  `job.workflow.shape` 가 "어느 쪽을 고르든 한 벌만 남긴다"라고 정한다.
- TypeScript 구현은 그 표를 실제로 쓴다. `tracer-agent-ts` 의
  `services/agent-worker/src/config/ledger/ai.job.stage.output.entity.ts:4-5`가 entity 이고
  `services/agent-worker/src/domain/recipe/application/finalize.recipe.scan.usecase.ts:65`와
  `fail.recipe.job.usecase.ts:32`가 종결·실패에서 `clear` 를 부른다.

### 그것이 만드는 실패 시나리오

**(1) 체크포인트가 무한히 쌓인다.** 잡 실행 하나가 성공으로 끝나든 실패로 끝나든
`agent_langgraph` 스키마의 그 열쇠 행은 남는다. 잡 경로에 `forget` 호출자가 없기 때문이다.
`recipe_scan` 은 노드마다 상태 전체를 직렬화하므로 행 하나가 작지 않다. 삭제 정책도 없고
표의 크기를 보는 지표도 없다. 언제 문제가 되는지는 재현하지 않았지만 방향은 단조 증가다.

**(2) 크래시가 예산 상한을 다시 연다.** `durability="async"`는 체크포인트 쓰기를 다음 스텝과
겹쳐 돌린다. 워커가 그 사이에 SIGKILL 로 죽으면 마지막 노드의 체크포인트가 없다. Temporal 이
재시도하면 `durable_graph.py:67-90`의 `prior_spend` 가 그 노드 이전까지의 `model_cost_usd` 만
읽으므로, 이미 쓴 달러가 상한에서 빠진다. 즉 크래시 한 번이 그 실행의 예산을 그만큼 다시 연다.
이 체크포인트의 존재 이유가 바로 크래시 복구인데 쓰기 시점이 그 이유와 어긋나 있다.

**(3) 두 축이 같은 재개 표를 보지 않는다.** TypeScript 는 `ai_job_stage_outputs` 에 적고 Python
은 `agent_langgraph` 에 적는다. 두 축이 같은 `agent-db` 를 공유하는 배치가 실재하므로(P4 절에서
확인) 같은 표를 두 축이 함께 읽는 상태가 성립하지 않는다.

### 목표 상태

- 잡의 재개 정본이 하나다. 끝난 단계의 산출이 계약이 소유한 `ai_job_stage_outputs` 에 있고
  LangGraph 체크포인터는 잡 경로에서 빠진다.
- `divergence.json` 의 `job.workflow.shape` 를 지운다.

### 마이그레이션 순서

**1단계 — 새는 것부터 막는다.** 잡의 종결·실패·취소 경로에서 `checkpoints.forget(resume_key)` 를
부른다. 구조 변경이 아니고 계약을 건드리지 않는다.

- 먼저 테스트로 고정하는 것: 잡 하나가 성공·실패·취소 어느 쪽으로 끝나도 `agent_langgraph` 에 그
  열쇠의 행이 남지 않는다. 아직 끝나지 않은 실행의 행은 남는다.
- 되돌리기: 호출 제거.

**2단계 — 단계 산출 어댑터를 더하되 체크포인터와 함께 쓴다.** `ai_job_stage_outputs` 를 읽고
쓰는 어댑터를 더하고, 두 벌이 같은 단계 집합을 갖는지 확인한다.

- 먼저 테스트로 고정하는 것: 같은 잡을 두 번 태웠을 때 체크포인터가 건너뛴 단계 집합과
  `ai_job_stage_outputs` 가 가진 단계 집합이 같다. 어긋나면 실패한다.
- 되돌리기: 어댑터를 끈다. 표는 남겨도 무해하다.

**3단계 — 팬아웃이 없는 `title-suggestion` 하나만 그래프 단계별 액티비티로 편다.**

- 먼저 테스트로 고정하는 것: 생성 액티비티를 단계마다 강제로 실패시켰을 때 재시도가 앞선 단계를
  다시 모델에 태우지 않는다. 모델 호출 횟수로 센다. 그리고 `tests/quality/golden` 의
  title-suggestion 골든 사례가 같은 출력과 궤적을 낸다.
- 되돌리기: 액티비티 목록을 되돌린다. 열린 잡은 `queues.yaml:144`의 `scheduleToCloseSeconds`
  안에 끝나므로 그 창만 넘긴다.

**4단계 — `task-cleanup`, `recipe-scan` 순으로 같은 분해를 적용한다.** `Send` 팬아웃은
워크플로의 `asyncio.gather` 가 대신한다. probe 보고는 `ai_job_stage_outputs` 에 넣고 워크플로
이력에는 열쇠만 싣는다.

- 먼저 테스트로 고정하는 것: 팬아웃 하나만 실패했을 때 그 하나만 다시 돈다. 그리고 워크플로
  액티비티가 주고받는 payload 크기가 상한 아래다.
- 이 단계는 2단계가 서 있어야 한다. 보고를 이력에 실을 수 없으므로 넣을 자리가 먼저 있어야 한다.

**5단계 — 잡 경로에서 체크포인터를 제거하고 옛 잡 체크포인트 행을 삭제한다.**

- 먼저 테스트로 고정하는 것: 체크포인터를 주지 않은 잡 실행이 단계 산출만으로 이어진다.

### 계약 변경과 TypeScript 영향

`CLAUDE.md` 의 계약 변경 순서를 따른다.

1. `contract/workflow/queues.yaml` 의 `singleKind.agentJob.activities` 목록(`:135-162`)이 늘어난다.
   그리고 **`stage` 와 `slot` 의 어휘를 계약이 갖게 한다.** `0008-job-stage-output.sql:5-7` 은
   표의 칸만 갖고 어떤 값이 오는지 정하지 않는다. 어휘가 계약에 없으면 두 축이 같은 표에 다른
   열쇠를 적어 서로의 단계를 못 읽는다. **이것이 이 항목에서 실제로 새로 필요한 계약 변경이다.**
2. 계약 자체 검사를 통과시킨다.
3. Python 을 그 계약에 맞춘다. TypeScript 는 표를 이미 쓰므로 어휘가 확정되면 그 값을 계약이
   적는 이름으로 맞추는 것만 남는다. TypeScript 의 워크플로는 `queues.yaml:75-126` 의 `perKind`
   이므로 Python 의 `singleKind` 액티비티 확장이 TypeScript 를 강제하지 않는다.
4. 조회 표면은 이 표를 아직 노출하지 않으므로 `agent-tracer` 와 `tracer-agent-web` 영향이 없다.
5. 두 저장소의 `contract/` submodule revision 을 맞춘다.
6. `agent-tracer-stack` 의 적합성 검사를 돌린다.

### 되돌릴 수 있는 지점과 없어지는 지점

- 1~4단계는 되돌릴 수 있다. 두 벌이 함께 도는 동안은 어느 쪽을 꺼도 재개가 성립한다.
- **5단계 이후는 되돌릴 수 없다.** 진행 중인 잡의 재개 상태가 체크포인트에 없으므로 구판으로
  돌아가면 그 잡들이 처음부터 돈다. 롤백은 "새 잡부터 구판" 으로만 가능하다.
- 계약이 `stage`/`slot` 어휘를 갖게 되면 그 뒤 이름 변경은 두 구현체와 이미 쌓인 행을 함께
  옮기는 일이 된다.

### 착수하지 않는 동안의 완화책

- **1단계만 먼저 한다.** 구조 변경이 아니고 계약을 건드리지 않으며 지금 새는 것을 멈춘다.
- `agent_langgraph` 표의 행 수와 가장 오래된 행의 나이를 지표로 낸다. 지금 아무도 보지 않는다.
- `PriorSpend.resumed`(`durable_graph.py:56`)를 지표로 낸다. 재개가 실제로 일어나는지 지금은 알
  수 없고, 재개율은 이 구조의 핵심 건강 지표다.
- `durable_graph.py:17` 의 `_JOB_DURABILITY` 를 `"sync"` 로 올리는 것을 검토한다. 공식 문서는
  `"sync"` 를 "다음 스텝이 시작하기 전에 동기로 저장" 으로, `"async"` 를 "다음 스텝이 도는 동안
  비동기로 저장" 으로 정의한다(참고 5). 체크포인트의 존재 이유가 크래시 복구이므로 지연을 조금
  내고 유실을 없애는 쪽이 그 이유에 맞다. 한 줄이고 되돌릴 수 있다. 다만 지연 비용은 재지 않았다.
- 멱등 dict 를 지우는 완화책은 **이미 실행됐다.** 이 문서가 "지금 지워도 보장이 줄지 않는다"고
  적은 자리이고, 잡 단위 멱등은 `jobs_dispatch.py:36` 이 그대로 갖는다.

---

## P3 — 접수와 워크플로 기동이 원자적이지 않고 jobs 에는 복구 장치가 없다

### 지금 코드가 하는 일

- chat 접수는 `src/tracer_agent/shared/agents/chat/intake/turn.py` 의 `ChatTurnIntake.enqueue`
  에서 `async with self._sql.transaction()`(`:49-50`)으로 사용자 메시지와 `queued` 실행을 한
  트랜잭션으로 커밋한다. 그리고 `self._dispatch.start`(`:54`)를 **트랜잭션 밖으로 나온 뒤**
  부른다.
- chat 의 기동은 `src/tracer_agent/shared/workflows/dispatch.py:48-59` 의 signal-with-start 다.
  `:53` 이 `USE_EXISTING`, `:55` 가 `ALLOW_DUPLICATE` 다.
- jobs 접수는 세 단계로 갈라져 있다. `src/tracer_agent/shared/workflows/jobs_enqueue.py` 의
  `JobIntake.admit`(`:81`)이 원장에 닿지 않고 심사하고, `JobIntake.claim`(`:94`)이 대기 행을
  커밋하고, 원장 연결을 닫은 뒤 `JobIntake.start`(`:127`)가 `dispatch.start` 를 부른다. 그 순서는
  `src/tracer_agent/shared/workflows/jobs_intake.py:87-90` 의 `enqueue_job` 이 갖는다.
- jobs 의 기동은 `src/tracer_agent/shared/workflows/jobs_dispatch.py:30-39` 이며 `:36` 이
  `REJECT_DUPLICATE`, `:38-39` 가 이미 시작된 워크플로 예외를 삼킨다.
- chat 의 유일한 보정은 `src/tracer_agent/worker/workflows/recovery.py:22-34` 의
  `resume_active_executions` 이고 호출은 `src/tracer_agent/worker/worker.py:204` 의
  `resume_chat_executions` 하나 — **chat 워커 부팅 시 1회**다.
- **jobs 에는 보정이 없다.** `worker.py:232-236` 의 `WORKER_PROFILES` 에서 chat 큐만 그 스윕을
  갖고 jobs·generate 는 `_nothing_to_resume`(`worker.py:211`)이며 `recovery.py:8` 이
  `ChatExecutionLedger` 만 가져온다.

### 그것이 만드는 실패 시나리오

**입력과 타이밍.** 사용자가 잡을 접수한다. `JobIntake.claim` 의 커밋이 성공한 직후,
`JobIntake.start` 가 돌기 전에 API 프로세스가 죽거나 Temporal 이 응답하지 않는다.

**무엇이 어떻게 깨지는가.** `ai_jobs` 행이 `pending` 으로 남고 그 행을 도는 워크플로가 없다.
이 저장소에는 그 행을 다시 기동하는 코드가 없다. 복구 통로는 하나뿐이다 — 사용자가 **같은
`idempotencyKey` 로 다시 접수하면** `jobs_enqueue.py:121` 의 `row["status"] != "pending"` 이
거짓이라 기동 payload 가 다시 실리고 `start` 가 다시 돈다. 키 없이 접수했으면 새 행이 서고 옛
행은 영구히 `pending` 이다. 사용자 화면에는 끝나지 않는 잡으로 보인다.

**chat 은 같은 자리에서 덜 나쁘다.** `chat_executions` 가 `queued` 로 남고 다음 chat 워커
부팅 때 `recovery.py:31-33` 이 쓸어 다시 신호한다. 부팅 간격이 며칠이면 며칠 뒤에 산다.

이것은 dual-write 문제이고 표준 해법은 transactional outbox 다.

### 목표 상태

접수가 커밋되면 워크플로가 반드시 돈다. 그 보장이 접수 프로세스의 생존에 걸리지 않는다.

### 마이그레이션 순서

**1단계 — outbox 표를 더하고 접수가 원장과 같은 트랜잭션에 한 행을 넣는다.** 릴레이는 아직
없다. 접수는 지금처럼 커밋 뒤 best-effort 로 기동하고 성공하면 `dispatched_at` 을 찍는다.

- 먼저 테스트로 고정하는 것: `dispatch.start` 가 예외를 던져도 outbox 행이 남는다. 그리고
  outbox 행과 원장 행이 같은 트랜잭션에서 커밋된다. jobs 와 chat 각각.
- 주의: chat 접수는 지금 `dispatch.start` 를 트랜잭션 밖에서 부른다(`turn.py:54`). outbox
  INSERT 는 `ChatTurnIntake._accept` 안, 즉 `:49-50` 의 트랜잭션 안으로 들어가야 뜻이 있다.
- 되돌리기: INSERT 를 뺀다. 표는 남겨도 무해하다.

**2단계 — 릴레이를 더한다.** `dispatched_at IS NULL AND created_at < now() - 10s` 를 주기로
쓸어 다시 기동한다. 중복 기동은 chat 이 `dispatch.py:53` 의 `USE_EXISTING`, jobs 가
`jobs_dispatch.py:36` 의 `REJECT_DUPLICATE` 로 이미 무해하다.

- 먼저 테스트로 고정하는 것: 찍히지 않은 행을 릴레이가 기동하고 찍는다. 이미 찍힌 행은
  건드리지 않는다. 기동이 실패하면 다음 회차가 다시 집는다.
- 릴레이를 어디에 두는가: jobs 워커의 백그라운드 태스크가 가장 작다. chat 워커는 replica 가
  여럿일 수 있고 그 문제가 `recovery.py` 에 이미 있으므로 릴레이는 한 자리에만 둔다.
- 되돌리기: 릴레이를 끈다. 지금 동작으로 돌아온다.

**3단계 — `recovery.py:31-33` 의 전역 재신호를 줄인다.** stale running 회수(`:30`)는 남긴다 —
그것은 dual-write 보정이 아니라 lease 회수이며 P4 가 다룬다.

- 먼저 테스트로 고정하는 것: 부팅 스윕 없이 outbox 릴레이만으로 `queued` 실행이 실행된다.

### 계약 변경과 TypeScript 영향

여기에는 갈림길이 하나 있다.

- outbox 표를 `contract/db/migrations/` 에 넣으면 `db/` 는 `enforcement.json:15` 가 `enforced`
  로 두는 경로이므로 **TypeScript 도 같은 표를 갖는다는 뜻이 된다.**
- 그러나 outbox 는 재개의 *보장*이지 wire 계약이 아니다. 계약이 "접수가 커밋되면 워크플로가
  반드시 돈다" 는 보장만 갖고 저장소는 구현체에 남기는 편이 낫다고 본다. 그러려면 표를 계약 밖
  스키마에 둔다.
- 계약 밖 스키마에 둘 때 `agent_langgraph` 를 재사용하지 않는다. `CLAUDE.md` 가 LangGraph
  체크포인트와 계약 원장을 직접 결합하지 말라고 정하므로 그 스키마에 원장 성격의 표를 넣으면
  그 이름의 뜻이 흐려진다. 별도 스키마 이름을 쓴다.

어느 쪽을 고르든 `CLAUDE.md` 의 순서를 따른다 — 계약 선언과 적합성 케이스 → 계약 자체 검사 →
두 구현체 → 소비 표면 → 각 저장소 submodule revision → stack 검증.

TypeScript 도 같은 dual-write 모양을 갖고 있을 것으로 보이지만, TypeScript 쪽 접수와 보정
구현은 읽지 않았다. 계약에 보장을 적을지 표를 적을지는 그것을 읽은 뒤에 정한다.

### 되돌릴 수 있는 지점과 없어지는 지점

- 1·2·3단계 전부 되돌릴 수 있다. 릴레이를 끄고 표를 남기면 지금 동작이다.
- 표가 계약에 들어간 뒤에는 표를 지우는 것이 두 구현체를 함께 건드리는 일이 된다.

### 착수하지 않는 동안의 완화책

- **jobs 워커 부팅 시 `pending` 잡을 쓸어 다시 기동한다.** chat 이 `worker.py:204` 에서 이미
  하는 것과 같은 모양이고 outbox 없이 할 수 있다. 부팅 간격만큼 늦지만 "영원히" 가 없어진다.
  가장 값싼 완화다. **다만 값싸지만은 않다** — 스윕이 `ai_jobs.input` 에서 도메인 입력을 다시
  세워 기동 payload 를 만들어야 하고, 그 값은 접수 시점의 모델 선택을 거친 값이다. 무엇을 언제
  되살릴지는 `contract/workflow/queues.yaml` 이 chat 과 같은 뜻을 갖는지 먼저 정한다.
- 지표 하나: `pending` 상태로 일정 시간 넘게 남은 잡의 수. 지금 그 수를 셀 자리가 없다.
- `jobs_enqueue.py` 의 `claim` 재기동 경로가 지금 유일한 복구 통로라는 사실을 그 자리 주석이
  적는다. 지금 그 사실이 코드 어디에도 적혀 있지 않다.

---

## P4 — 스레드 직렬화가 두 벌이고 축이 서로를 풀지 못한다

### 지금 코드가 하는 일

직렬화 장치가 두 벌이다.

- **Postgres 락.** `contract/db/migrations/0001-chat.sql:60-61` 의
  `chat_executions_running_thread` 가 `("thread_id") WHERE "status" = 'running'` 이다.
  `requested_backend` 가 색인에 없다.
- 축을 더한 migration 이 그 색인을 바꾸지 않았다. `0006-chat-axis.sql:18-20` 은 비유일 색인
  `chat_executions_active_backend` 를 **추가만** 한다.
- **Temporal 락.** `src/tracer_agent/worker/workflows/chat_workflows.py:145-165` 의 `_drain` 이
  자식 실행 워크플로를 하나씩 `await` 한다.

축 필터가 걸린 자리와 걸리지 않은 자리가 갈린다.

- 축으로 거르는 조회: `src/tracer_agent/shared/agents/chat/execution_ledger.py:18-23`(활성 목록),
  `:25-32`(다음 대기), `:34-39`·`:41-46`(stale 회수).
- 축으로 거르지 않는 갱신: `_CLAIM_QUEUED`(`:48-53`), `_BEGIN_ATTEMPT`(`:55-65`),
  `_CHECKPOINT_RUNNING`(`:67-72`), `_COMPLETE_RUNNING`(`:74-97`),
  `_RECORD_CANCELED_OUTCOME`(`:99-121`), `_FAIL_ACTIVE`(`:123-139`), `_CANCEL_ACTIVE`(`:141-157`).
- 축으로 거르지 않는 것이 맞는 자리: `src/tracer_agent/shared/agents/chat/intake/ledger.py:63-66`
  의 `has_active_turn`. `CLAUDE.md` 가 스레드가 바쁜지 보는 조회는 두 축을 함께 세라고 정한다.

워크플로 식별자에 축이 없다.

- `src/tracer_agent/shared/workflows/chat_spec.py:59-66` → `chat-thread:{threadId}`,
  `chat:{executionId}`.
- 계약이 같은 패턴을 갖는다: `contract/workflow/queues.yaml:30`, `:49`, `:132`.
- Temporal 의 워크플로 ID 유일성 범위는 **네임스페이스**다(참고 2). task queue 접두사를 달리
  주는 것으로는 갈리지 않는다.

스레드가 바쁠 때의 대기 상한은 `chat_spec.py:33-35` 가 60초 × 45회로 두고 계약도 같은 값을
`queues.yaml:165-168` 에 갖는다. stale 회수 유예는 `chat_spec.py:38` 이 20분으로 둔다.

### 감사 D 가 미확인으로 남긴 것 — 확인한 사실

`agent-tracer-stack` 을 읽어서(수정하지 않았다) 확인했다.

**확인한 것.**

- 두 축은 **서로 다른 Temporal 네임스페이스**를 본다. `agent-tracer-stack/compose/compare.yml:32`
  가 TypeScript 축에 `COMPARE_TS_NAMESPACE`(기본 `agent-ts`)를, `:38` 이 Python 축에
  `COMPARE_PYTHON_NAMESPACE`(기본 `agent-python`)를 준다.
- 그 이유가 워크플로 ID 충돌이라고 stack 이 명시한다. `compose/compare.yml:24-26` 의 주석과
  `agent-tracer-stack/README.md:186` 이 같은 말을 적는다.
- `compose/compare.yml:58-86` 의 `temporal-namespace` 서비스가 기동 전에 두 네임스페이스를
  만들고 API 와 워커가 그것을 기다린다.
- 큐 접두사도 축마다 다르다. `compose/compare.yml:31`, `:37`.
- **두 축은 같은 `agent-db` 를 공유한다.** `compose/compare.yml:5-15` 의 `x-agent-env` 가
  `AGENT_DB_HOST: agent-db` 와 `AGENT_DB_NAME: agent` 를 갖고 `:28`·`:36` 에서 두 축이 그것을
  상속한다. `:88-95` 의 `agent-schema` 가 두 축의 스키마를 TypeScript 이미지 하나로 세운다.
- 한 축만 올리는 배치는 `compose/agent-ts.yml` 과 `compose/agent-python.yml` 이고 둘 다 큐
  접두사 기본값 `agent` 를 쓴다(`agent-ts.yml:18`, `agent-python.yml:16`).

**따라서 감사 D 의 두 결과 중 하나만 남는다.**

- 결과 1(워크플로 ID 충돌)은 **지금 배포에서 일어나지 않는다.** 네임스페이스가 갈라져 있기
  때문이다. 다만 그것을 막고 있는 것은 이 저장소의 코드가 아니라 stack 의 환경변수 두 줄이다.
- 결과 2(같은 `agent-db`)는 **그대로 남는다.** 이것이 이 항목의 실제 위험이다.

**확인하지 못한 것은 마지막 절이 갖는다.**

### 그것이 만드는 실패 시나리오

**입력과 타이밍.** `compare` 프로파일로 두 축을 나란히 띄운다. 스레드 T 에서 TypeScript 축의
실행이 `running` 으로 서 있는 동안 TypeScript 워커가 SIGKILL 로 죽는다. 그 뒤 같은 스레드에
Python 축 실행이 접수된다.

**무엇이 어떻게 깨지는가.**

1. `has_active_turn`(`intake/ledger.py:63-66`)은 축으로 거르지 않으므로 접수 자체가
   `ACTIVE_TURN_CONFLICT`(409)로 거절된다(`turn.py:15`, `:73-74`). 상대 축의 죽은 실행이
   이 축의 접수를 막는다.
2. 접수가 통과한 경우, `prepare` 가 부르는 `_CLAIM_QUEUED`(`execution_ledger.py:48-53`)가
   `chat_executions_running_thread`(`0001-chat.sql:60-61`)에 걸려 `UniqueViolation` 을 받고
   `THREAD_BUSY` 로 접힌다.
3. Python 의 `recover_stale_running`(`execution_ledger.py:34-39`)은 `requested_backend = $3` 으로
   자기 축만 되돌리므로 상대 축의 stale `running` 을 풀 수 없다.
4. `chat_workflows.py:72-81` 의 `_prepare_until_thread_frees` 가 60초 × 45회, 즉 **45분** 기다린
   뒤 `:81` 의 `ApplicationError("chat thread never freed")` 로 실패한다. 그 45분 동안 그 스레드
   워크플로는 자식 하나에 묶여 있으므로 같은 스레드의 뒤 턴도 서지 않는다.
5. TypeScript 워커가 다시 뜨기 전에는 아무도 그 행을 풀지 못한다. 그 축을 내린 채로는 그
   스레드가 영구히 막힌다.

두 축을 나란히 띄워 견주는 것이 이 구조의 목적인데, 계약이 소유한 색인 하나가 그 목적을 막는다.

### 목표 상태

두 축이 같은 `agent-db` 위에서 서로의 스레드를 막지 않는다. 직렬화의 주인이 하나다.

### 마이그레이션 순서

**1단계 — 유일 색인이 축을 알게 한다.** `("requested_backend", "thread_id") WHERE status='running'`
으로 갈아끼운다.

- 주의: `requested_backend` 가 nullable 이다(`0001-chat.sql:37`). Postgres 의 유일 색인은 NULL 을
  서로 다른 값으로 보므로 축이 빈 행이 여럿 서게 된다. `0006-chat-axis.sql:3-9` 가 축이 빈 활성
  행을 이미 접었고 `:14-16` 의 CHECK 가 값의 범위를 갖지만 NOT NULL 은 아니다. 그러므로 이
  단계는 `requested_backend` 를 NOT NULL 로 올리는 것과 짝이거나, 그러지 않을 근거가 있어야 한다.
- 먼저 테스트로 고정하는 것: 축이 다른 두 실행이 같은 스레드에서 동시에 `running` 이 될 수 있다.
  같은 축의 두 실행은 여전히 될 수 없다.

**2단계 — 축 필터가 없는 갱신에 `requested_backend = $axis` 를 더한다.** 위에 열거한 일곱
문장이 대상이다. `has_active_turn` 은 그대로 둔다 — `CLAUDE.md` 가 그 하나만 예외로 정한다.

- 먼저 테스트로 고정하는 것: 다른 축의 행을 대상으로 부른 갱신이 0행을 낸다.
- 1·2단계는 하위호환이고 한 축만 뜬 배치에서 동작이 같다. 두 단계가 이 항목의 실질이다.

**3단계 — 워크플로 식별자에 축을 넣는다.** `chat-thread:{axis}:{threadId}`,
`chat:{axis}:{executionId}`, `job:{axis}:{kind}:{key}`.

- 먼저 테스트로 고정하는 것: 두 축이 같은 네임스페이스를 봐도 서로의 워크플로에 신호하지 않는다.
- **배포 창이 필요하다.** 옛 ID 의 스레드 워크플로가 새 ID 와 나란히 살아 같은 실행을 집을 수
  있다. 옛 것은 유휴 상한(`chat_spec.py:50`, 120초) 뒤 닫힌다. 계약의 `idleSeconds` 는 5로 낡아
  있으므로 그 값으로 창을 재지 않는다. 그러므로 P5 의 워커 판이 먼저 서 있어야 안전하다.
- **지금 급하지 않다.** stack 이 네임스페이스를 갈라 두었으므로 이 단계가 필요해지는 것은 두
  축을 한 네임스페이스에 두려 할 때다.

**4단계 — 스레드 busy 상한을 줄인다.** `chat_spec.py:35` 와 `queues.yaml:167` 이 갖는 45회를
내린다.

- 1·2단계가 서면 busy 는 같은 축의 stale 에서만 나오고 그것은 lease 회수가 푼다.
- **주의: 이 값은 회수 유예와 짝이라 따로 정할 수 없다.** `chat_spec.py:38` 의 회수 유예가
  20분이므로, 상한을 3~5회(3~5분)로 내리면 정상 stale 이 풀리기 전에 실패로 접힌다. 회수 유예를
  함께 줄이거나 상한을 20분보다 조금 크게 둔다.
- 먼저 테스트로 고정하는 것: busy 상한의 벽시계 합이 stale 회수 유예보다 크다.

### 계약 변경과 TypeScript 영향

`CLAUDE.md` 의 순서를 따른다.

1. 색인 교체는 `db/` 이므로 계약 변경이다. 새 migration 으로 갈아끼우고 두 구현체가 같은 색인을
   본다. 3단계를 하면 `queues.yaml:30`·`:49`·`:132` 의 ID 패턴 세 줄을, 4단계를 하면
   `queues.yaml:165-168` 의 `leases` 를 함께 바꾼다.
2. 계약 자체 검사를 통과시킨다.
3. 두 구현체를 맞춘다. TypeScript 에서 확인한 것: 워크플로 ID 를 같은 모양으로 만든다
   (`services/agent-api/src/domain/chat/adapter/chat.workflow.dispatcher.ts:85`), 그리고 조회는
   이미 축으로 거른다
   (`services/agent-worker/src/domain/chat/adapter/typeorm.chat.execution.repository.adapter.ts:27`).
   TypeScript 가 갱신에서도 축으로 거르는지는 확인하지 못했다.
4. 이 표를 읽는 소비 표면은 이 변경으로 칸이 바뀌지 않는다.
5. 두 저장소의 submodule revision 을 맞춘다.
6. `agent-tracer-stack` 을 검증한다. 이 항목은 stack 의 네임스페이스 분리를 되돌릴 수 있게 하는
   변경이므로 `agent-tracer-stack/README.md:186` 과 `compose/compare.yml:24-26` 의 주석도 함께
   갱신 대상이 된다.

이것은 두 구현체가 함께 이득을 보는 계약 변경이다. TypeScript 는 자기 축 이름만 넣으면 된다.

### 되돌릴 수 있는 지점과 없어지는 지점

- 1·2·4단계는 되돌릴 수 있다. 색인을 다시 갈아끼우고 `WHERE` 절을 지운다.
- 3단계는 되돌릴 수 있으나, 되돌리면 두 축을 한 네임스페이스에 나란히 띄우는 것이 다시 불가능해
  진다. 그리고 되돌리는 배포마다 열려 있던 스레드 워크플로가 한 번씩 갈린다.
- **`requested_backend` 를 NOT NULL 로 올리면 되돌릴 수 없다.** `0006-chat-axis.sql:3-9` 가 활성
  NULL 행을 이미 접었으므로 남은 NULL 은 종결된 행뿐인데, 그 행들에 어떤 축을 채워 넣을지 정할
  근거가 없다. 이 결정만 따로 판단한다.

### 착수하지 않는 동안의 완화책

- **stack 이 네임스페이스를 갈라 둔 것이 이 저장소의 전제라는 사실을 적는다.** 지금 그 전제는
  stack 의 compose 파일에만 있고 이 저장소는 모른다. 누군가 네임스페이스를 합치면 결과 1 이
  즉시 살아난다.
- `execution_ledger.py` 에 주석 한 줄: stale 회수가 자기 축만 풀고 유일 색인은 축을 모르므로 두
  사실이 짝이 되어 상대 축의 stale 이 이 축을 막는다. 지금 두 사실이 서로 다른 파일에 있다.
- 지표: `THREAD_BUSY` 로 재시도 중인 실행의 수와 그 대기 시간. 45분을 실제로 기다리는 실행이
  있는지 지금은 알 수 없다.
- **`THREAD_BUSY_MAX_ROUNDS` 만 먼저 줄이지 않는다.** 회수 유예보다 짧게 만들면 정상 stale 복구
  까지 실패로 접는다. 값싸 보이지만 값싸지 않다.

---

## P5 — 워크플로와 상태 스키마의 버전 관리가 없다

### 지금 코드가 하는 일

- `patched`, `deprecate_patch`, `get_version`, `VersioningBehavior`, `DeploymentVersion`,
  `build_id`, `WorkerDeploymentConfig`, `Replayer` — `src` 와 `tests` 전체에서 하나도 없다.
- `Worker(...)` 셋(`src/tracer_agent/worker/worker.py:139-152`, `:185-192`, `:196-202`)이
  `client`, `task_queue`, `workflows`, `activities`, `graceful_shutdown_timeout` 만 준다.
- 장수 워크플로가 실재한다. `chat_spec.py:50` 이 유휴 상한 120초, `:52` 가 자식 상한 100,
  `chat_workflows.py:162-163` 이 `continue_as_new` 다.
- 체크포인트 열쇠에 판이 없다. `graph_session.py:53` 의 `resume_key` 가 `executionId or jobId`다.
- **이미 한 번 데인 자리가 있다.** `worker/agents/chat/steps/converse.py:131` 의 주석이
  "판이 바뀌기 전에 선 체크포인트에는 이 칸이 없으므로 없으면 실린 이력으로 되돌린다" 라고
  적는다. 같은 파일 `:175` 도 이어받는 체크포인트의 모양을 다룬다. 버전 문제가 이미 일어났고
  그 한 자리에만 땜질이 있다. chat 바깥 StateGraph 가 사라지면서 이 파일은 `nodes/` 에서
  `steps/` 로 옮겨 왔고, 옮긴 뒤에도 그 두 자리는 그대로다.
- **TypeScript 도 같은 공백을 갖는다.** `tracer-agent-ts` 에서 `patched(`, `deprecatePatch`,
  `versioningBehavior`, `buildId` 를 찾았고 하나도 없다. 이것은 한 구현체의 문제가 아니다.

### 그것이 만드는 실패 시나리오

**입력과 타이밍.** `chat_workflows.py` 의 `_drain` 에 액티비티를 하나 더하거나 순서를 바꾼
이미지를 배포한다. 배포 순간 열려 있는 `chat-thread:{threadId}` 워크플로가 있다 — 유휴 상한이
120초이고 자식 100개까지 사는 구조이므로 대화가 도는 시간대에는 상시 있다.

**무엇이 어떻게 깨지는가.** 새 워커가 그 워크플로의 다음 워크플로 태스크를 받아 이력을 replay
하다가 이력에 없는 커맨드를 만나 non-determinism 으로 태스크가 실패한다. Temporal 은 워크플로
태스크 실패를 재시도하므로 그 스레드는 배포가 되돌아갈 때까지 진행하지 않는다. 사용자에게는 그
스레드의 답이 오지 않는 것으로 보인다. 잡 쪽은 `queues.yaml:144` 의 `scheduleToCloseSeconds`
안에 끝나므로 창이 좁지만, 그 순간 도는 잡은 같은 방식으로 죽는다.

**LangGraph 쪽도 같다.** `RecipeScanState` 의 채널 이름이나 노드 이름을 바꾸고 배포하면,
`resume_key` 가 판을 모르므로(`graph_session.py:53`) 옛 체크포인트를 이어받는다. 그러면
`durable_graph.py:67-90` 의 `prior_spend` 가 없는 칸을 읽거나 `:92-94` 의 `resume_input` 이 없는
노드부터 이어가려 한다. `steps/converse.py:131` 이 그 자리의 증거다. 지출 채널이 두 칸에서 여섯
칸으로 늘어난 것도 같은 종류의 판 변경이며, 늘어난 칸은 `SpendChannels` 가 기본값을 갖는 누적
채널이라 옛 체크포인트에서도 0으로 읽힌다.

### 목표 상태

배포가 열린 실행을 죽이지 않는다. 어느 실행이 어느 판에서 도는지 조회할 수 있다. 상태 스키마가
바뀌면 그것이 조용한 손상이 아니라 "이 실행은 처음부터 돈다" 는 관측 가능한 사건이 된다.

### 마이그레이션 순서

**1단계 — replay 테스트를 CI 에 넣는다.** `temporalio.worker.Replayer` 로 저장한 이력을 두
워크플로에 재생한다. 저장소 안에서 끝나고 계약을 건드리지 않는 순수 추가다.

- 먼저 테스트로 고정하는 것: 지금 코드가 지금 이력을 replay 한다. 이것이 기준선이다. 그 뒤
  워크플로를 고치는 변경마다 이 테스트가 먼저 깨져서 알려 준다.
- **P5 에서 가장 값싸고 가장 먼저다.** 그리고 P1·P3·P4 의 워크플로 변경 전부의 안전망이다.

**2단계 — 체크포인트 열쇠에 상태 스키마 판을 박는다.** `graph_session.py:53` 의 `resume_key` 를
판과 식별자를 이은 모양으로 바꾼다.

- 먼저 테스트로 고정하는 것: 판이 다르면 앞선 체크포인트를 이어받지 않고 `PriorSpend.resumed`
  가 거짓이다. 판이 같으면 이어받는다.
- 주의: 판을 올리는 배포에서 그때 진행 중이던 잡은 처음부터 돈다. 그것이 의도다. 다만 예산도
  처음부터 다시 열리므로 그 실행 하나의 지출이 두 배가 될 수 있다. 판을 올리는 배포는 그 사실을
  알고 한다.
- **P1 의 1단계가 먼저 서 있어야 한다.** 판을 올리면 옛 판의 체크포인트가 남는데 잡 경로에 지금
  정리 통로가 없다.

**3단계 — `Worker(...)` 셋에 배포 판 설정을 더한다.** 기본 동작을 유지하는 순수 추가다.

- `ChatThreadWorkflow` 는 `continue_as_new` 를 쓰는 장수 워크플로다. Temporal 공식 문서는 그
  분류에 **Pinned + continue-as-new 에서 판을 올리는 방식**을 권한다(참고 3).
- `AgentJobWorkflow` 는 다음 배포 전에 끝나는 짧은 워크플로다. 같은 문서가 그 분류에도
  **Pinned** 를 권한다. 감사 D 는 여기에 Auto-Upgrade 를 적었으나 공식 문서의 분류는 그렇지
  않으므로 이 문서는 Pinned 로 적는다.
- 먼저 테스트로 고정하는 것: 판을 선언한 워커가 선언하지 않은 워커와 같은 결과를 낸다.

**4단계 — `agent-tracer-stack` 의 배포 절차가 판을 램프한다.** 이 저장소 밖이다.

- 주의: Pinned 워크플로가 오래 남으면 옛 워커를 내릴 수 없다. `chat_spec.py:52` 의 자식 상한을
  줄이면 `continue_as_new` 가 자주 돌아 판이 자주 넘어간다. 그러나 그 값은 `queues.yaml:44` 의
  `maxChildren` 이 갖는 계약 값이므로 계약 변경이다.

### 계약 변경과 TypeScript 영향

- 1·2·3단계는 계약 영향이 없다. `queues.yaml` 은 큐와 상한과 식별자만 갖고 워커 판을 갖지 않는다.
- 4단계에서 자식 상한을 줄이면 `queues.yaml:44` 가 계약 변경이 되고, `CLAUDE.md` 의 순서대로
  계약 → 두 구현체 → stack 을 밟는다.
- TypeScript 도 같은 공백을 가지므로 이 항목은 계약보다 `agent-tracer-stack` 이 받는다. 두 축이
  같은 배포 절차를 쓰는 것이 그 저장소의 책임 범위에 맞는다.
- `compare` 프로파일에서 한 축만 판을 갖는 상태는 견주기를 흐린다. 두 축을 함께 올린다.

### 되돌릴 수 있는 지점과 없어지는 지점

- 1·3단계는 순수 추가이므로 되돌릴 수 있다.
- 2단계는 판을 올린 그 배포에서 진행 중이던 실행의 앞선 지출을 잃는다. 되돌려도 그 실행들은 이미
  처음부터 돌았다.
- 4단계에서 램프를 시작하면 두 판의 워커가 함께 뜨는 구간이 생긴다. **그 구간에서는 DB
  migration 이 반드시 하위호환이어야 한다.** P1·P3·P4 의 migration 단계가 전부 이 제약 아래로
  들어온다.

### 착수하지 않는 동안의 완화책

- **1단계를 지금 한다.** 계약을 건드리지 않는 순수 추가이고 P5 의 사고를 배포 전에 잡는다.
  이 문서의 네 항목을 통틀어 가장 값이 싸다.
- 워크플로 모듈을 고치는 변경에 "지금 열려 있는 워크플로가 있는가" 를 묻는 절차를 둔다. 지금 그
  질문을 하는 자리가 없다.
- 배포 직전에 열린 `chat-thread:*` 워크플로 수를 센다. 코드가 아니라 절차다.
- `chat_spec.py:50` 의 유휴 상한을 줄이면 열린 스레드 워크플로가 빨리 닫혀 배포 창이 넓어진다.
  그러나 그 자리의 주석이 적듯 매 턴 차가운 시작 비용을 낸다. **그리고 그 값을 만지기 전에
  계약이 낡아 있는 것을 먼저 정리한다** — `queues.yaml:43` 의 `idleSeconds` 는 5인데 두 구현체가
  모두 120초를 쓴다(`chat_spec.py:50` `THREAD_IDLE_S = 120.0`, TypeScript
  `chat.workflow.spec.ts:15` `CHAT_THREAD_IDLE_TIMEOUT = "2 minutes"`). 구현체 사이의 차이가 아니라
  **계약 값 하나가 낡은 것**이고, 이 문서의 배포 창 계산과 P4 3단계의 겹침 창은 5초가 아니라 120초를
  기준으로 읽어야 한다. 계약을 고치는 몫은 `CONTRACT-BACKLOG.md`가 갖는다.

---

## 착수 우선순위와 순서 의존

바꾸기 쉬운 순서가 아니라 **다른 항목의 안전을 만드는 순서**로 적는다.

| # | 무엇 | 왜 여기인가 | 계약 변경 | 되돌릴 수 있나 |
| --- | --- | --- | --- | --- |
| 1 | P5 1단계 — replay 테스트 | 나머지 셋이 전부 워크플로나 원장을 고치는데 그 변경이 열린 실행을 깨는지 볼 자리가 지금 없다 | 아니오 | 예 |
| 2 | P1 1단계 — 잡 체크포인트 정리 | 지금 새는 것을 멈춘다. 그리고 P5 2단계가 옛 판 행을 남기므로 정리 통로가 먼저 있어야 한다 | 아니오 | 예 |
| 3 | P3 전체 — dispatch outbox | 사용자에게 보이는 손해가 가장 크다. 잡이 영원히 `pending` 인 경로가 있고 복구 통로가 사용자의 재접수뿐이다 | 가볍게 | 예 |
| 4 | P5 2·3단계 — 스키마 판과 워커 판 | P4 3단계가 배포 창을 요구하므로 그 전에 서 있어야 한다 | 아니오 | 부분 |
| 5 | P4 1·2단계 — 색인과 갱신에 축 | 두 축이 같은 `agent-db` 를 공유하는 것이 확인됐으므로 지금 배포에서 바로 이득이다. 하위호환이다 | 예 | 예 |
| 6 | P4 3·4단계 — 워크플로 ID 와 busy 상한 | stack 이 네임스페이스를 갈라 둔 덕에 지금 급하지 않다. 두 축을 한 네임스페이스에 두려 할 때 필요해진다 | 예 | 부분 |
| 7 | P1 2~5단계 — 재개를 계약 표로 이관 | 가장 크고 되돌릴 수 없는 지점을 갖는다. 위가 전부 서고 replay 테스트가 워크플로 변경을 잡을 수 있게 된 뒤에 한다 | 예 | 부분 |

### 순서 의존

- **P5 1단계 → P1·P3·P4 의 모든 워크플로 변경.** replay 테스트 없이 워크플로를 고치면 배포가
  열린 실행을 깨는지 배포한 뒤에야 안다.
- **P1 1단계 → P5 2단계.** 체크포인트 열쇠에 판을 박으면 옛 판 행이 남는다. 잡 경로의 정리
  통로가 먼저 있어야 표가 자라지 않는다.
- **P5 3단계 → P4 3단계.** 워크플로 ID 를 바꾸는 배포는 옛 ID 와 새 ID 의 워크플로가 겹치는
  창을 만든다. 워커 판이 없으면 그 창에서 어느 워커가 어느 이력을 받을지 정할 수 없다.
- **P5 4단계 → P1·P3·P4 의 모든 DB migration.** 두 판의 워커가 함께 뜨는 구간이 생기면
  migration 이 하위호환이어야 한다.
- **P1 2단계 → P1 4단계.** probe 보고를 워크플로 이력에 실을 수 없으므로 넣을 표가 먼저 있어야
  팬아웃을 액티비티로 펼 수 있다.
- **P4 1단계 ↔ `requested_backend` NOT NULL.** 축을 포함한 유일 색인은 NULL 을 서로 다른 값으로
  보므로, NOT NULL 없이는 축이 빈 행에서 색인이 무력하다. 둘을 함께 판단한다.
- **P4 4단계 ↔ 회수 유예.** busy 상한의 벽시계 합은 stale 회수 유예보다 커야 한다. 둘 중 하나만
  바꾸지 않는다.

---

## 확인하지 못한 것

- **실행하지 않았다.** 이 문서 작업으로 `ruff`·`mypy`·`pytest`·적합성 러너를 돌리지 않았다.
  `scripts/check_comments.py` 만 확인했다. 인용한 경로·줄번호·값은 전부 실제로 읽은 것이다.
- TypeScript 구현의 **갱신 SQL 이 축으로 거르는지** 확인하지 않았다. 조회가 거르는 것만 확인했다.
- TypeScript 가 `ai_job_stage_outputs` 에 어떤 `stage`·`slot` 값을 쓰는지 확인하지 않았다.
  entity 와 `clear` 호출만 확인했다. P1 의 계약 어휘는 그 값을 읽은 뒤에 정할 수 있다.
- TypeScript 가 접수와 워크플로 기동 사이에 outbox 나 보정을 갖는지 확인하지 않았다. P3 의
  계약 갈림길은 그것을 읽은 뒤에 정한다.
- `agent-tracer-stack` 에서 `compare` 외의 프로파일이 두 축을 동시에 띄우는지 확인하지 않았다.
  `agent-ts.yml` 과 `agent-python.yml` 이 각각 한 축을 올리는 것만 확인했다.
- `queues.yaml:43` 의 `idleSeconds` 는 이제 확인했다. 적합성 러너가 이 칸을 대조하지 않는다 —
  `idleSeconds` 는 계약 전체에서 그 선언 한 줄에만 나오고 `contract/conformance` 어디에도 없다.
  그래서 두 구현체가 모두 120초를 써도 아무도 잡지 못했다.
- 이 저장소가 쓰는 Temporal Python SDK 판에 `WorkerDeploymentConfig` 가 있는지 확인하지 않았다.
  P5 3단계는 그것을 먼저 본 뒤에 착수한다.
- `durability` 를 `"sync"` 로 올렸을 때의 지연 비용을 재지 않았다. P1 의 완화책은 그 측정 뒤에
  결정한다.
- 감사 D 가 적은 §7 출처 6(transactional outbox)은 이 문서를 쓰며 다시 열지 않았다. 아래 참고
  목록의 5번이 그것이다.

---

## 참고

이 문서가 판단에 쓴 것만 적는다. 1~4는 이 문서를 쓰며 직접 열어 확인했다.

1. Temporal — Versioning (Python SDK):
   <https://docs.temporal.io/develop/python/versioning>
   Worker Versioning 이 권고이고 patching 은 그것을 쓰지 못할 때의 대안이라고 적는다.
2. Temporal — Workflow Id and Run Id:
   <https://docs.temporal.io/workflow-execution/workflowid-runid>
   워크플로 ID 의 유일성 범위가 네임스페이스라고 적는다. P4 결과 1 의 근거다.
3. Temporal — Worker Versioning:
   <https://docs.temporal.io/production-deployment/worker-deployments/worker-versioning>
   Pinned 와 Auto-Upgrade 를 워크플로 수명별로 나눠 권한다. 짧은 워크플로와 `continue_as_new` 를
   쓰는 장수 워크플로 둘 다 Pinned 쪽이다. P5 3단계의 근거다.
4. LangChain — Durability:
   <https://reference.langchain.com/python/langgraph/types/Durability>
   `"sync"` 는 다음 스텝이 시작하기 전에, `"async"` 는 다음 스텝이 도는 동안 저장한다고 적는다.
   이 문서가 참조한 판은 크래시 유실 위험을 문장으로 적지 않는다 — 그 위험은 두 정의에서
   따라 나오는 것이며 문서의 진술이 아니다.
5. Transactional outbox:
   <https://microservices.io/patterns/data/transactional-outbox.html>
   P3 의 표준 해법이다. 감사 D 에서 옮겨 왔고 이 문서에서 다시 열지 않았다.
