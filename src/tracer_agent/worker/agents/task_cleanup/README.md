# Task Cleanup 에이전트

Task Cleanup 에이전트는 서버가 결정론적으로 선별한 정리 후보를 검토하고 archive 제안을 생성한다. `triage`가 후보를 노출하고, `inspect`가 후보별 event를 병렬 조사하며, `investigate`가 제안을 작성한다. 제안은 사용자의 검토와 승인 대상이며 task를 직접 변경하지 않는다.

## 토폴로지와 워크플로

```mermaid
flowchart TD
    START((START)) --> TRIAGE[triage: TriagePlan]
    TRIAGE -->|assignment 있음| FANOUT{{Send fan-out}}
    TRIAGE -->|assignment 없음 또는 예산 없음| EMPTY1[empty]
    FANOUT --> INSPECT[inspect: InspectReport]
    INSPECT --> INVESTIGATE[investigate: CleanupDraft]
    INVESTIGATE -->|redispatch| FANOUT
    INVESTIGATE -->|완료| VALIDATE[validate_decisions]
    VALIDATE -->|통과 + 제안 있음| FINALIZE[finalize]
    VALIDATE -->|통과 + 제안 없음| EMPTY2[empty]
    VALIDATE -->|실패 + repair 미실행| REPAIR[repair]
    REPAIR --> VALIDATE
    VALIDATE -->|수리 후 유효 제안 있음| FINALIZE
    VALIDATE -->|수리 후 유효 제안 없음| EMPTY3[empty]
    FINALIZE --> END1((END))
    EMPTY1 --> END2((END))
    EMPTY2 --> END3((END))
    EMPTY3 --> END4((END))
```

```mermaid
sequenceDiagram
    participant G as TASK_CLEANUP_GRAPH
    participant T as TriageNode
    participant I as InspectNode × N
    participant C as InvestigateNode
    participant V as ValidateDecisionsNode
    participant R as RepairNode
    participant API as tracer-api
    G->>T: candidate batch
    T-->>G: selected task IDs + depths
    G->>I: Send(task assignment, cost budget)
    par selected candidates
        I->>API: get task timeline
        API-->>I: event page
        I-->>C: InspectReport + cited event IDs
    end
    C-->>G: CleanupDraft + optional redispatch
    G->>V: candidate and event evidence validation
    V-->>G: finalize / repair / empty
```

`TriageNode`는 도구를 갖지 않고 요청이 실어 준 후보 배치만 본다. `InspectNode`는 할당된 task 하나의 event만 읽고, 조율자는 `COORDINATOR_TOOL_NAMES=()`로 도구 없이 보고를 종합한다. event가 있는 후보는 조사된 event를 인용해야 하며, event가 없는 후보는 event 인용 없이 archive 제안이 가능하다.

## 노드와 이동

| 노드 | 입력 | 처리 | 갱신 또는 결과 |
| --- | --- | --- | --- |
| `triage` | `TaskCleanupState` | 요청이 실어 준 후보 배치에서 review 대상을 depth로 선택한다 | `plan`, 노출 후보, 후보 근거 |
| `inspect` | `InspectDispatch` | 후보 하나의 timeline을 읽고 archive 여부·사유·event ID를 보고한다 | `reports`, `event_ids_by_task` |
| `investigate` | `TaskCleanupState` | review 보고를 종합해 `CleanupDraft`를 만들고 필요하면 redispatch한다 | `suggestions`, `redispatch` |
| `validate_decisions` | `TaskCleanupState` | 노출 후보와 조사 event만 제안이 인용하는지 검사한다 | 유효 제안, `validation_errors` |
| `repair` | `TaskCleanupState` | 검증 오류를 포함해 조율자 출력을 한 번 다시 생성한다 | 제안과 보고 갱신 |
| `finalize` | `TaskCleanupState` | 제안을 `max_suggestions`까지 외부 결과로 직렬화한다 | `suggestions` |
| `empty` | `TaskCleanupState` | 제안이 없거나 검증이 소진된 결과를 반환한다 | 빈 `suggestions` |

서버 후보 선별은 `qualify_candidates`가 담당한다. 최근 활동, running/waiting 상태의 stale 여부, event 유무, 중복 제목, placeholder 제목을 결정론적으로 평가하며 active child가 있는 task는 제외한다.

## 도구 타입

| 도구 | 노드 | 주요 인자 | 역할 |
| --- | --- | --- | --- |
| `get_task_events` | `inspect` | `taskId`, `limit`, `cursor`, `order` | 후보 task의 event 페이지를 조회하고 event ID를 장부에 기록한다 |

후보 페이지에 노출된 task만 조율자가 제안할 수 있다. `ToolRegistry`는 Pydantic 모델로 인자를 검증하고 후보 노출·event citation 장부를 갱신한다. 전송 오류만 재시도 대상으로 분류하고 HTTP 도메인 오류는 재시도하지 않는다.

## 프롬프트 구성

| 프롬프트 | 계약 template | 실행 단계 |
| --- | --- | --- |
| investigator system | `task-cleanup.investigator.system` | review report 종합·archive suggestion |
| triage system | `task-cleanup.triage.system` | 후보 선택·depth 배분 |
| inspect system | `task-cleanup.inspect.system` | 단일 후보 event 검토 |
| repair directive | `task-cleanup.investigator.repair` | 검증 오류 수리 |

```mermaid
flowchart TD
    CONTRACT[contract prompt.json] --> TRIAGE[triage system]
    CONTRACT --> INSPECT[inspect system]
    CONTRACT --> INVESTIGATOR[investigator system]
    CONTRACT --> REPAIR[repair directive]
    CANDIDATES[candidate fields] --> TUSER[triage user prompt]
    TASK[task ID] --> IUSER[inspect user prompt]
    REPORTS[reviewer reports] --> CUSER[investigator user prompt]
    ERRORS[validation errors] --> RUSER[repair prompt]
    TRIAGE --> Q1[LangChain agent]
    INSPECT --> Q2[LangChain agent]
    INVESTIGATOR --> Q3[LangChain agent]
    REPAIR --> Q4[LangChain agent]
```

아래 원문은 `task_cleanup/prompts.py`와 계약 fragment를 결합한 prompt이다.

### Triage·inspect·repair prompt 원문과 번역

실행별 user prompt는 `Scan time`, 최대 archive task 수, `Output language`, reviewer reports를 추가한다. 관련 구현은 [graph.py](graph.py), [nodes](nodes/), [tools/registry.py](tools/registry.py), [prompts.py](prompts.py), [policy.py](policy.py)에 있다.

프롬프트 전문은 문서가 옮겨 적지 않는다. 슬롯의 본문은 계약이, 슬롯을 감싸는 scaffold 는
`prompts.py` 가 소유한다. 두 축이 같은 template 을 읽으므로 그 본문을 문서 두 벌로 두면
계약이 바뀔 때 함께 낡는다.

```
계약   contract/agent/task-cleanup/prompt.json
task-cleanup.investigator.system
task-cleanup.investigator.repair
task-cleanup.triage.system
task-cleanup.inspect.system
```

## 미들웨어와 출력 타입

`AgentMiddlewareStack`이 네 에이전트와 같은 층을 같은 순서로 세우며, 공유 장부를 쓰는 도구는 이 호출의 락으로 직렬화한다. 구조화 출력은 `TriagePlan`, `InspectReport`, `CleanupDraft`이며, `validate_suggestions`가 도메인 제약을 결정적으로 본다. 겹친 제안과 상한을 넘은 꼬리는 다시 물어도 같은 답이 오므로 사유 없이 지우고, 근거가 어긋난 제안만 모델이 고칠 사유로 남긴다.

## Temporal 워크플로

세 잡 종류를 `agentJobWorkflow` 하나가 받고 준비 → 생성 → 종결 순서로 실행한다. 모델을 부르는
생성만 `generate` 큐로 보내고 나머지 단계는 `jobs` 큐에 남는다. 단계별 큐와 시간 상한과 재시도
상한은 계약의 `workflow/queues.yaml`이 소유한다.

| 액티비티 | 큐 | start-to-close | 재시도 | 비고 |
| --- | --- | --- | ---: | --- |
| `prepareAgentJob` | `jobs` | 60초 | 5 | 도메인 문맥을 모으고 원장을 실행 중으로 옮긴다 |
| `generateAgentJob` | `generate` | 900초 | 3 | 20분 schedule-to-close, 30초 heartbeat |
| `finalizeAgentJob` | `jobs` | 60초 | 5 | 원장 종결과 산출물과 완료 배달 |
| `failAgentJob` | `jobs` | 60초 | 5 | 어느 단계가 실패하든 원장을 실패로 닫는다 |
| `settleCanceledAgentJob` | `jobs` | 30초 | — | 액티비티가 돌기 전에 닿은 취소를 접는다 |

```mermaid
stateDiagram-v2
    [*] --> Prepare
    Prepare --> Generate
    Generate --> Finalize: 결과 또는 빈 결과
    Prepare --> Fail: 준비 실패
    Generate --> Fail: 취소가 아닌 오류
    Prepare --> SettleCanceled: 취소
    Generate --> SettleCanceled: 취소
    Finalize --> [*]
    Fail --> [*]
    SettleCanceled --> [*]
```

**자격은 생성 액티비티 밖으로 나가지 않는다.** 준비는 도메인 문맥만 모으고 그 산출이 워크플로
이력에 남는다. 이 시도가 쓸 봉투는 생성이 실행 직전에 받으며 종결은 자격을 뺀 정산 값만 받는다.

**생성이 다시 시도돼도 끝난 노드를 다시 태우지 않는다.** 그래프가 잡 하나를 열쇠로 삼는
LangGraph 체크포인트에서 이어가므로 실패 지점부터 재개한다. 그 상태는 계약이 소유하지 않는
`agent_langgraph` 스키마에 있으며 `divergence.json`의 `job.workflow.shape`가 그 자리를 갖는다.

## 관련 코드

| 확인 대상 | 파일 |
| --- | --- |
| 그래프 정의 | `graph.py` |
| 요청별 실행 조립 | `agent.py` |
| 선별·검사·결정 노드 | `nodes/inspect.py`, `nodes/decision.py`, `nodes/result.py` |
| LangChain agent와 미들웨어 | `runtime/llm/middleware_stack.py`, `runtime/llm/structured_agent.py` |
| 모델 호출자 조립 | `deps.py` |
| 도구 registry | `tools/registry.py` |
| 프롬프트 조립 | `prompts.py` |
| 예산·팬아웃 정책 | `policy.py` |
| 추적 API 읽기 | `reader.py` |
| 워크플로와 액티비티 | `worker/workflows/jobs_workflows.py`, `jobs_activities.py` |
