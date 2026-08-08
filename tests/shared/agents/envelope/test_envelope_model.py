"""봉투가 싣는 모델이 그 종류의 허용 목록 안에 있는지 검증한다."""

from __future__ import annotations

import pytest

from tracer_agent.shared.agents.envelope.catalog import CATALOG, JOB_KINDS
from tracer_agent.shared.agents.envelope.grants import DraftGrant
from tracer_agent.shared.agents.envelope.issue import chat_envelope, job_envelope
from tracer_agent.shared.agents.shared.model_tiering import CHAT_KIND, allowed_models

_OUTSIDE_MODELS = ("claude-3-5-sonnet-20241022", "gpt-4o", "")


def _chat_envelope(model: str | None) -> dict[str, object]:
    return chat_envelope(
        execution_id="execution-1",
        model=model,
        api_key="key-1",
        catalog=CATALOG[CHAT_KIND],
        read_api_base_url="http://read",
        agent_api_base_url="http://agent",
        grant=DraftGrant(token="t", token_hash="h"),
        user_id="user-1",
        now_ms=0,
    )


@pytest.mark.parametrize("model", _OUTSIDE_MODELS)
def test_허용_목록_밖_모델은_대화_봉투에_실리지_않는다(model: str) -> None:
    assert _chat_envelope(model)["model"] == CATALOG[CHAT_KIND].default_model


@pytest.mark.parametrize("model", allowed_models(CHAT_KIND))
def test_허용_목록_안_모델은_대화_봉투가_그대로_싣는다(model: str) -> None:
    assert _chat_envelope(model)["model"] == model


def test_모델을_고르지_않은_대화는_기본_모델로_연다() -> None:
    assert _chat_envelope(None)["model"] == CATALOG[CHAT_KIND].default_model


@pytest.mark.parametrize("kind", sorted(JOB_KINDS))
@pytest.mark.parametrize("model", _OUTSIDE_MODELS)
def test_허용_목록_밖_모델은_잡_봉투에도_실리지_않는다(kind: str, model: str) -> None:
    envelope = job_envelope(kind=kind, api_key="key-1", catalog=CATALOG[kind], chosen_model=model)

    assert envelope["model"] == CATALOG[kind].default_model


@pytest.mark.parametrize("kind", [*sorted(JOB_KINDS), CHAT_KIND])
def test_봉투가_싣는_기본_모델은_그_종류가_허용한_값이다(kind: str) -> None:
    assert CATALOG[kind].default_model in allowed_models(kind)
