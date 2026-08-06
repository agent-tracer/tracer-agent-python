"""종류마다 고를 수 있는 모델을 소유하며 봉투와 설정이 함께 읽는다."""

from __future__ import annotations

CHAT_KIND = "chat"

# 종류마다 허용 모델을 좁게 두는 것이 절감의 첫 자리이며, 예산은 이 목록을 전제로 잡힌다.
ALLOWED_MODELS: dict[str, tuple[str, ...]] = {
    CHAT_KIND: ("claude-sonnet-4-6", "claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"),
    "title.suggestion": ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-sonnet-5"),
    "recipe.scan": ("claude-sonnet-4-6", "claude-sonnet-5", "claude-opus-5", "claude-haiku-4-5"),
    "task.cleanup": ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-sonnet-5"),
}


def allowed_models(kind: str) -> tuple[str, ...]:
    """그 종류가 허용한 모델이며 모르는 종류는 아무것도 허용하지 않는다."""
    return ALLOWED_MODELS.get(kind, ())


def offered_models(kinds: tuple[str, ...]) -> frozenset[str]:
    """그 종류들이 모두 함께 허용하는 모델이며 설정 하나가 고를 수 있는 값이다."""
    known = [kind for kind in kinds if kind in ALLOWED_MODELS]
    if not known:
        return frozenset()
    shared = set(ALLOWED_MODELS[known[0]])
    for kind in known[1:]:
        shared &= set(ALLOWED_MODELS[kind])
    return frozenset(shared)
