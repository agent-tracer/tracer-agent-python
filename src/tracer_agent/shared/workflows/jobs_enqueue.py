"""브라우저가 보낸 잡 하나를 자격과 멱등을 확인해 원장에 접수하고 워크플로에 넘긴다."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..agents.envelope.models import JOB_KEY_MISSING, ModelCredentialSource
from ..agents.runtime.ledger import LedgerSql, SqlRow
from ..agents.shared.json_view import JsonObject
from ..agents.shared.wire import INVALID_REQUEST, validation_details
from .jobs_anchor import ScanAnchorSource
from .jobs_dispatch import TemporalJobDispatch
from .jobs_input import INPUT_MODEL_BY_KIND, AdmissionContext, build_payload, input_hash
from .jobs_kinds import JOB_EXECUTOR, AgentJobKind
from .jobs_ledger import JobLedger

NOT_FOUND = (404, "not_found", "Job execution not found")
IDEMPOTENCY_CONFLICT = (
    409,
    "job.idempotency-conflict",
    "Idempotency key was already used with different job input",
)


class JobEnqueueBody(BaseModel):
    """계약이 정한 잡 접수 본문이며 브라우저는 백엔드마다 본문을 가르지 않는다."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["title.suggestion", "recipe.scan", "task.cleanup"]
    input: JsonObject = Field(default_factory=dict)
    idempotencyKey: str | None = Field(default=None, min_length=1, max_length=200)


class JobRejected(Exception):
    """접수가 잡을 받아들이지 않은 사유이며 창구가 그대로 봉투에 옮긴다."""

    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details


@dataclass(frozen=True)
class AdmittedJob:
    """원장에 닿기 전 심사를 통과한 잡의 도메인 입력과 멱등 열쇠다."""

    job_input: Any
    key: str | None
    request_hash: str | None


@dataclass(frozen=True)
class AcceptedJob:
    """접수가 세웠거나 되찾은 잡 원장 행과 아직 기동하지 않은 워크플로 입력이다."""

    row: SqlRow
    pending_payload: JsonObject | None


class JobIntake:
    """잡 하나를 원장 밖 심사와 원장 안 멱등 판정으로 나눠 접수하고 처음 세운 접수만 워크플로를 기동한다."""

    def __init__(
        self,
        anchors: ScanAnchorSource,
        credentials: ModelCredentialSource,
        dispatch: TemporalJobDispatch,
    ) -> None:
        self._anchors = anchors
        self._credentials = credentials
        self._dispatch = dispatch

    async def admit(self, user_id: str, body: JobEnqueueBody) -> AdmittedJob:
        """원장 연결에 앞서 도메인 입력과 앵커와 모델 자격을 심사한다."""
        job_input = self._admitted_input(body)
        rejection = await job_input.admit(AdmissionContext(user_id, self._anchors))
        if rejection is not None:
            raise JobRejected(rejection.status, rejection.code, rejection.message)
        # 자격을 접수가 보지 않으면 대기 행이 선 뒤 워커에서 실패해 사용자가 사유를 늦게 받는다.
        if not await self._credentials.api_key(user_id):
            raise JobRejected(*JOB_KEY_MISSING)
        key = _idempotency_key(body.idempotencyKey)
        request_hash = None if key is None else input_hash(body.kind, job_input)
        return AdmittedJob(job_input=job_input, key=key, request_hash=request_hash)

    async def claim(
        self,
        sql: LedgerSql,
        user_id: str,
        body: JobEnqueueBody,
        admitted: AdmittedJob,
        now: datetime,
    ) -> AcceptedJob:
        """심사를 통과한 잡의 대기 행을 세우거나 같은 멱등키가 먼저 세워 둔 행을 되찾는다."""
        ledger = JobLedger(sql)
        execution_id = str(uuid.uuid4())
        created = await ledger.claim(
            execution_id,
            user_id,
            body.kind,
            JOB_EXECUTOR[body.kind],
            admitted.job_input.task_id(),
            admitted.key,
            admitted.request_hash,
            body.input,
            now,
        )
        row = await self._settled_row(ledger, user_id, body, execution_id, admitted.key, created)
        if not created and row["idempotency_input_hash"] != admitted.request_hash:
            raise JobRejected(*IDEMPOTENCY_CONFLICT)

        job_id = str(row["id"])
        if not created and row["status"] != "pending":
            return AcceptedJob(row=row, pending_payload=None)
        return AcceptedJob(
            row=row, pending_payload=build_payload(admitted.job_input, user_id, job_id, admitted.key)
        )

    async def start(self, kind: str, accepted: AcceptedJob) -> None:
        """대기 행이 선 접수만 워크플로를 기동하며 그동안 원장 연결을 쥐지 않는다."""
        if accepted.pending_payload is None:
            return
        await self._dispatch.start(
            AgentJobKind.of_wire(kind), str(accepted.row["id"]), accepted.pending_payload
        )

    def _admitted_input(self, body: JobEnqueueBody) -> Any:
        """이 종류가 받는 도메인 입력이며 스키마를 어기면 계약이 정한 거절을 낸다."""
        try:
            return INPUT_MODEL_BY_KIND[body.kind].model_validate(body.input)
        except ValidationError as invalid:
            raise JobRejected(*INVALID_REQUEST, details=validation_details(invalid)) from invalid

    async def _settled_row(
        self,
        ledger: JobLedger,
        user_id: str,
        body: JobEnqueueBody,
        execution_id: str,
        key: str | None,
        created: bool,
    ) -> SqlRow:
        """이번 접수가 세운 행이거나 같은 멱등키가 먼저 세워 둔 행이다."""
        row = None
        if created:
            row = await ledger.find(execution_id)
        elif key is not None:
            row = await ledger.find_by_idempotency(user_id, body.kind, key)
        if row is None:
            raise JobRejected(*NOT_FOUND)
        return row


def _idempotency_key(value: str | None) -> str | None:
    """공백뿐인 멱등키는 키를 싣지 않은 것과 같게 본다."""
    trimmed = (value or "").strip()
    return trimmed or None
