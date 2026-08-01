"""추적 창구로 나가는 payload 가 계약의 trace 자리에서 폐기되는지 검증한다."""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from tests.support.contract import shared_contract
from tracer_agent.shared.agents.shared.redaction import (
    RedactionStage,
    SuspectPayloadError,
    is_suspect_key,
    redact,
)
from tracer_agent.worker.agents.runtime.telemetry.disclosure import disclosable_run_payload

_RULES = shared_contract("redaction.json")
_KEY_WORDS = [str(word) for word in _RULES["keys"]["words"]]


@st.composite
def secret_keys(draw: st.DrawFn) -> str:
    prefix = draw(st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", max_size=5))
    suffix = draw(st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", max_size=5))
    word = draw(st.sampled_from(_KEY_WORDS)).replace(" ", "")
    return f"{prefix}{word}{suffix}"


@st.composite
def plain_keys(draw: st.DrawFn) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz_"
    return draw(st.text(alphabet=alphabet, min_size=1).filter(lambda name: not is_suspect_key(name)))


@st.composite
def plain_values(draw: st.DrawFn, max_depth: int = 2) -> Any:
    scalars = st.one_of(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz -", max_size=20),
        st.integers(),
        st.booleans(),
        st.none(),
    )
    if max_depth <= 0:
        return draw(scalars)
    nested = plain_values(max_depth=max_depth - 1)
    return draw(
        st.one_of(
            scalars,
            st.lists(nested, max_size=3),
            st.dictionaries(keys=plain_keys(), values=nested, max_size=3),
        )
    )


class Test추적_자리:
    def test_계약이_이_자리를_폐기로_정한다(self) -> None:
        assert _RULES["stages"]["trace"]["onSuspect"] == "discard"

    @given(plain_values())
    def test_걸릴_것이_없는_payload는_그대로_지난다(self, value: Any) -> None:
        assert redact(value, stage=RedactionStage.TRACE) == value

    @given(secret_keys(), st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=20))
    def test_자격의_이름을_가진_key_하나가_payload_전체를_막는다(self, key: str, value: str) -> None:
        with pytest.raises(SuspectPayloadError):
            redact({key: value, "prompt": "평범한 프롬프트 본문"}, stage=RedactionStage.TRACE)

    def test_다룰_수_없는_모양의_값도_막는다(self) -> None:
        with pytest.raises(SuspectPayloadError):
            redact({"note": object()}, stage=RedactionStage.TRACE)


class Test실행_payload:
    def test_걸릴_것이_없으면_그대로_내보낸다(self) -> None:
        payload = {"prompt": "평범한 프롬프트 본문"}

        assert disclosable_run_payload(payload) == payload

    def test_걸리는_자리가_하나라도_있으면_아무것도_내보내지_않는다(self) -> None:
        payload = {"apiKey": "sk-ant-live-should-not-leak", "prompt": "평범한 프롬프트 본문"}

        assert disclosable_run_payload(payload) == {}
