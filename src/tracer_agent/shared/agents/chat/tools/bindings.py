"""chat 도구가 어느 REST API의 뷰인지를 계약과 같은 값으로 소유한다."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

from .surface import chat_tool_bindings

_PLACEHOLDER = re.compile(r"\{([^}]+)\}")


@dataclass(frozen=True)
class ToolBinding:
    """도구 하나가 되읽거나 부를 API 한 자리이며, 인자가 경로·쿼리·본문 중 어디로 가는지를 담는다."""

    method: str
    path: str
    path_args: tuple[str, ...] = ()
    query: Mapping[str, str] = field(default_factory=dict)
    body: Mapping[str, str] = field(default_factory=dict)
    body_constants: Mapping[str, str] = field(default_factory=dict)

    def placeholders(self) -> tuple[str, ...]:
        """경로 틀에 적힌 자리 이름을 순서대로 낸다."""
        return tuple(_PLACEHOLDER.findall(self.path))


def _binding(declared: Mapping[str, Any]) -> ToolBinding:
    return ToolBinding(
        method=str(declared["method"]),
        path=str(declared["path"]),
        path_args=tuple(str(name) for name in declared["pathArgs"]),
        query=dict(declared["query"]),
        body=dict(declared["body"]),
        body_constants=dict(declared["bodyConstants"]),
    )


def _section() -> Mapping[str, Any]:
    section: Mapping[str, Any] = chat_tool_bindings()
    return section


TOOL_BINDINGS: dict[str, ToolBinding] = {
    str(name): _binding(declared) for name, declared in _section()["bindings"].items()
}

# action 을 받는 도구가 그 action 마다 갖는 자리이며 값은 계약이 소유한다.
TOOL_ACTION_BINDINGS: dict[str, dict[str, ToolBinding]] = {
    str(name): {str(action): _binding(one) for action, one in actions.items()}
    for name, actions in _section()["actionBindings"].items()
}


def takes_action(tool_name: str) -> bool:
    """이 도구가 action 으로 자리를 구분하는지 알린다."""
    return tool_name in TOOL_ACTION_BINDINGS


def binding_for(tool_name: str, args: Mapping[str, object]) -> ToolBinding:
    """도구 이름과 인자에 맞는 자리를 내며 계약에 없는 이름이나 action 이면 거절한다."""
    actions = TOOL_ACTION_BINDINGS.get(tool_name)
    if actions is None:
        binding = TOOL_BINDINGS.get(tool_name)
        if binding is None:
            raise KeyError(f"{tool_name} is not a contract tool")
        return binding
    action = args.get("action")
    if not isinstance(action, str):
        raise KeyError(f"{tool_name} needs an action")
    chosen = actions.get(action)
    if chosen is None:
        raise KeyError(f"{tool_name} has no {action} action")
    return chosen


# 대응하는 REST API가 아직 없어 실행 프로세스 안에서 처리되는 도구는 이제 없다.
LOCAL_TOOL_NAMES: tuple[str, ...] = ()


def fill_path(binding: ToolBinding, args: Mapping[str, object]) -> str:
    """경로 틀의 자리를 도구 인자 값으로 채운다."""
    path = binding.path
    for name in binding.path_args:
        path = path.replace("{" + name + "}", quote(str(args[name]), safe=""))
    return path
