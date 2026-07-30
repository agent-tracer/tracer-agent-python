"""접수가 받는 잡 종류와 데드라인과 멱등 정규형이 계약과 같은지 검증한다."""

from __future__ import annotations

import hashlib
from typing import get_args

import pytest
from pydantic import ValidationError

from tests.support.contract import conformance_case, wire_contract
from tracer_agent.shared.agents.envelope.catalog import CATALOG, CHAT_KIND
from tracer_agent.shared.workflows.jobs_input import (
    IDEMPOTENCY_KEYS,
    INPUT_MODEL_BY_KIND,
    canonical_input,
    input_hash,
)
from tracer_agent.shared.workflows.jobs_intake import JobEnqueueBody
from tracer_agent.shared.workflows.jobs_kinds import JOB_EXECUTOR, JOB_KINDS, LOCAL_EXECUTOR

_INTAKE = conformance_case("job.intake")
_WIRE_KINDS: dict[str, dict[str, object]] = wire_contract("job.kinds.json")["kinds"]


def _accepted_kinds() -> tuple[str, ...]:
    """접수 본문이 실제로 통과시키는 잡 종류다."""
    return get_args(JobEnqueueBody.model_fields["kind"].annotation)


class Test접수가_받는_잡_종류:
    def test_계약이_적은_종류를_그대로_안다(self) -> None:
        assert sorted(JOB_KINDS) == sorted(_INTAKE["kinds"])

    def test_창구가_계약이_적은_종류를_빠짐없이_통과시킨다(self) -> None:
        assert sorted(_accepted_kinds()) == sorted(_INTAKE["kinds"])

    def test_본문_제약이_적은_종류와도_같다(self) -> None:
        assert sorted(_accepted_kinds()) == sorted(_INTAKE["body"]["constraints"]["kind"]["enum"])

    def test_접수_입력_모델을_종류마다_갖는다(self) -> None:
        assert sorted(INPUT_MODEL_BY_KIND) == sorted(_INTAKE["kinds"])

    def test_실행_주체를_계약과_같게_안다(self) -> None:
        assert {kind: _WIRE_KINDS[kind]["executor"] for kind in _INTAKE["kinds"]} == JOB_EXECUTOR


class Test잡_실행_데드라인:
    def test_워크플로가_태우는_종류마다_계약이_적은_값을_카탈로그가_갖는다(self) -> None:
        declared = {
            kind: _WIRE_KINDS[kind]["deadlineMs"]
            for kind in _INTAKE["kinds"]
            if JOB_EXECUTOR[kind] != LOCAL_EXECUTOR
        }

        assert {kind: CATALOG[kind].deadline_ms for kind in declared} == declared

    def test_로컬_실행기가_가져가는_종류는_계약도_데드라인을_적지_않는다(self) -> None:
        for kind in _INTAKE["kinds"]:
            if JOB_EXECUTOR[kind] != LOCAL_EXECUTOR:
                continue
            assert "deadlineMs" not in _WIRE_KINDS[kind]
            assert kind not in CATALOG

    def test_카탈로그는_대화와_워크플로_잡만_갖는다(self) -> None:
        temporal = [k for k in _INTAKE["kinds"] if JOB_EXECUTOR[k] != LOCAL_EXECUTOR]

        assert sorted(CATALOG) == sorted([CHAT_KIND, *temporal])


class Test멱등_입력의_정규형:
    def test_계약이_그_종류에_적은_도메인_입력_칸을_빠짐없이_본다(self) -> None:
        for kind, declared in _INTAKE["inputs"].items():
            assert sorted(IDEMPOTENCY_KEYS[kind]) == sorted([*declared["required"], *declared["optional"]])

    def test_계약이_적은_케이스마다_같은_정규형과_해시를_낸다(self) -> None:
        idempotency = _INTAKE["idempotency"]

        assert idempotency["digest"] == "sha256"
        for spec in idempotency["cases"]:
            kind = spec["kind"]
            job_input = INPUT_MODEL_BY_KIND[kind].model_validate(spec["input"])

            assert canonical_input(kind, job_input) == spec["canonical"]
            assert input_hash(kind, job_input) == spec["hash"]
            assert input_hash(kind, job_input) == hashlib.sha256(spec["canonical"].encode()).hexdigest()

    def test_계약이_종류마다_적은_칸을_그_순서대로_본다(self) -> None:
        assert {kind: list(keys) for kind, keys in IDEMPOTENCY_KEYS.items()} == _INTAKE["idempotency"]["keys"]

    def test_종류가_모르는_칸을_실은_입력을_접수가_받지_않는다(self) -> None:
        with pytest.raises(ValidationError):
            INPUT_MODEL_BY_KIND["title.suggestion"].model_validate({"taskId": "t1", "trigger": "dashboard"})
