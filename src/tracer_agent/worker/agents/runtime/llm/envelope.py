"""모델 이름으로 출력 한도와 추론 설정을 낸다."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_CONTRACT_ROOT = Path(__file__).resolve().parents[6] / "contract"
_MODEL_ENVELOPE_PATH = _CONTRACT_ROOT / "agent" / "shared" / "model.envelope.json"
_EXECUTION_LIMITS_PATH = _CONTRACT_ROOT / "agent" / "shared" / "execution.limits.json"


@dataclass(frozen=True)
class ModelEnvelope:
    """모델 하나가 그 실행에서 받는 출력 한도와 추론 노력과 사고 모드다."""

    max_output_tokens: int | None = None
    effort: str | None = None
    thinking: str | None = None


_EMPTY_ENVELOPE = ModelEnvelope()


@lru_cache(maxsize=1)
def _declared_envelope() -> dict[str, ModelEnvelope]:
    """모델 이름으로 기능의 기본 한도를 덮는 값을 계약에서 읽는다."""
    declared = json.loads(_EXECUTION_LIMITS_PATH.read_text(encoding="utf-8"))["modelEnvelope"]
    return {
        model: ModelEnvelope(
            max_output_tokens=values.get("maxOutputTokens"),
            effort=values.get("effort"),
            thinking=values.get("thinking"),
        )
        for model, values in declared.items()
        if isinstance(values, dict)
    }


@lru_cache(maxsize=1)
def _contract_vocabulary() -> tuple[frozenset[str], frozenset[str]]:
    document = json.loads(_MODEL_ENVELOPE_PATH.read_text(encoding="utf-8"))
    effort_values = frozenset(document["fields"]["effort"]["values"])
    thinking_values = frozenset(document["fields"]["thinking"]["values"])
    return effort_values, thinking_values


@lru_cache(maxsize=1)
def _validated_envelope() -> dict[str, ModelEnvelope]:
    effort_values, thinking_values = _contract_vocabulary()
    declared = _declared_envelope()
    for model, envelope in declared.items():
        if envelope.effort is not None and envelope.effort not in effort_values:
            raise ValueError(
                f"model.envelope.json: {model} effort {envelope.effort!r} not in {sorted(effort_values)}"
            )
        if envelope.thinking is not None and envelope.thinking not in thinking_values:
            raise ValueError(
                f"model.envelope.json: {model} thinking {envelope.thinking!r} "
                f"not in {sorted(thinking_values)}"
            )
    return declared


def model_envelope(model: str) -> ModelEnvelope:
    """이 구현이 그 모델에 정한 봉투를 내고 없으면 빈 봉투를 낸다."""
    return _validated_envelope().get(model, _EMPTY_ENVELOPE)
