"""잡 입력이 자기 액티비티 payload 와 task_id 를 스스로 내는지 검증한다."""

from __future__ import annotations

from tracer_agent.shared.workflows.jobs_input import (
    INPUT_MODEL_BY_KIND,
    RecipeScanJobInput,
    TaskCleanupJobInput,
    TitleSuggestionJobInput,
    build_payload,
)

_BASE = {"userId": "u1", "executionId": "e1", "idempotencyKey": None}


def _payload(job_input: object) -> dict[str, object]:
    return build_payload(job_input, "u1", "e1", None)  # type: ignore[arg-type]


def test_스캔은_태스크와_요청_문구를_싣는다() -> None:
    payload = _payload(RecipeScanJobInput(taskId="t1", userPrompt="왜", language="ko"))

    assert payload == {**_BASE, "taskId": "t1", "language": "ko", "userPrompt": "왜"}


def test_없는_언어는_칸을_만들지_않는다() -> None:
    assert "language" not in _payload(RecipeScanJobInput(taskId="t1"))


def test_제목은_태스크만_싣는다() -> None:
    assert _payload(TitleSuggestionJobInput(taskId="t1")) == {**_BASE, "taskId": "t1"}


def test_정리는_요청한_개수만_싣는다() -> None:
    assert _payload(TaskCleanupJobInput()) == _BASE
    assert _payload(TaskCleanupJobInput(filters={"maxSuggestions": 3})) == {
        **_BASE,
        "maxSuggestions": 3,
    }


def test_태스크에_매인_잡만_원장의_태스크_칸을_갖는다() -> None:
    assert RecipeScanJobInput(taskId="t1").task_id() == "t1"
    assert TitleSuggestionJobInput(taskId="t1").task_id() == "t1"
    assert TaskCleanupJobInput().task_id() is None


def test_모든_잡_종류가_자기_payload_를_스스로_낸다() -> None:
    for model in INPUT_MODEL_BY_KIND.values():
        assert model.activity_payload is not type(None)
        assert "activity_payload" in dir(model)
