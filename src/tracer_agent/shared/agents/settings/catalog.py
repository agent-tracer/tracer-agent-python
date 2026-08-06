"""설정이 고를 수 있는 모델을 소유하며 잡이 함께 허용하는 것만 담는다."""

from __future__ import annotations

from ..shared.model_tiering import offered_models
from .execution import model_kinds
from .models import ModelOption

# 단가를 모르는 모델은 예산을 집행할 수 없으므로 그 이름은 설정에 저장되지 않는다.
MODEL_OPTIONS: tuple[ModelOption, ...] = (
    ModelOption(id="claude-haiku-4-5", label="Claude Haiku 4.5"),
    ModelOption(id="claude-opus-5", label="Claude Opus 5"),
    ModelOption(id="claude-sonnet-4-6", label="Claude Sonnet 4.6"),
    ModelOption(id="claude-sonnet-5", label="Claude Sonnet 5"),
)


def model_options() -> tuple[ModelOption, ...]:
    """고를 수 있는 모델을 이름 오름차순으로 낸다."""
    offered = offered_models(model_kinds())
    chosen = [option for option in MODEL_OPTIONS if option.id in offered]
    return tuple(sorted(chosen, key=lambda option: option.id))


def knows_model(model: str) -> bool:
    """설정에 저장할 수 있는 모델인지 답한다."""
    return any(option.id == model for option in model_options())
