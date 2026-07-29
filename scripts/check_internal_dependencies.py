"""Python 에이전트 내부 계층의 단방향 import 규칙을 검사한다."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path

AGENT_GRAPH = "agent_graph"
API_ROOT = f"{AGENT_GRAPH}.api"
WORKER_ROOT = f"{AGENT_GRAPH}.worker"
SHARED_ROOT = f"{AGENT_GRAPH}.shared"
AGENTS_PACKAGE = f"{SHARED_ROOT}.agents"
API_LAYER = "api"
WORKER_LAYER = "worker"
SHARED_APP_LAYER = "shared:app"
RUNTIME_LAYER = "shared:runtime"
SHARED_LAYER = "shared:shared"
SLICE_PREFIX = "shared:slice:"
SLICE_FOUNDATION_MODULES = {"models", "prompts"}
SLICE_EXECUTION_MODULES = {"agent", "graph", "nodes", "tools"}


@dataclass(frozen=True)
class DependencyViolation:
    """허용되지 않은 내부 import의 출발점과 도착점을 나타낸다."""

    source_module: str
    target_module: str
    source_layer: str
    target_layer: str
    lineno: int

    def message(self) -> str:
        """사람이 수정할 수 있는 import 방향 오류를 출력한다."""
        return (
            f"{self.source_module}:{self.lineno}: "
            f"{_display_layer(self.source_layer)} -> {_display_layer(self.target_layer)} "
            f"내부 의존은 허용되지 않는다 ({self.target_module})"
        )


@dataclass(frozen=True)
class _ImportTarget:
    module: str
    lineno: int


def find_violations(source_root: Path) -> list[DependencyViolation]:
    """소스 루트의 Python import를 해석해 계층 위반을 반환한다."""
    violations: list[DependencyViolation] = []
    for path in sorted(source_root.rglob("*.py")):
        source_module = _module_name(path, source_root)
        source_layer = _source_layer(source_module)
        if source_layer is None:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        is_package = path.name == "__init__.py"
        for target in _import_targets(tree, source_module, is_package):
            target_layer = _target_layer(target.module)
            if target_layer is None or _is_allowed(
                source_layer,
                target_layer,
                source_module,
                target.module,
            ):
                continue
            violations.append(
                DependencyViolation(
                    source_module=source_module,
                    target_module=target.module,
                    source_layer=source_layer,
                    target_layer=target_layer,
                    lineno=target.lineno,
                )
            )
    return violations


def _module_name(path: Path, source_root: Path) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _source_layer(module: str) -> str | None:
    if module == AGENTS_PACKAGE:
        return SHARED_APP_LAYER
    return _target_layer(module)


def _target_layer(module: str) -> str | None:
    if not (module == AGENT_GRAPH or module.startswith(f"{AGENT_GRAPH}.")):
        return None
    if module == API_ROOT or module.startswith(f"{API_ROOT}."):
        return API_LAYER
    if module == WORKER_ROOT or module.startswith(f"{WORKER_ROOT}."):
        return WORKER_LAYER
    if module == SHARED_ROOT or module.startswith(f"{SHARED_ROOT}."):
        if module == AGENTS_PACKAGE:
            return None
        if not module.startswith(f"{AGENTS_PACKAGE}."):
            return SHARED_APP_LAYER
        remainder = module.removeprefix(f"{AGENTS_PACKAGE}.")
        owner = remainder.split(".", maxsplit=1)[0]
        if owner == "runtime":
            return RUNTIME_LAYER
        if owner == "shared":
            return SHARED_LAYER
        return f"{SLICE_PREFIX}{owner}"
    return None


def _import_targets(
    tree: ast.AST,
    source_module: str,
    is_package: bool,
) -> list[_ImportTarget]:
    targets: list[_ImportTarget] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(_ImportTarget(alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            targets.extend(_resolve_from_import(node, source_module, is_package))
    return targets


def _resolve_from_import(
    node: ast.ImportFrom,
    source_module: str,
    is_package: bool,
) -> list[_ImportTarget]:
    if node.level == 0:
        base = node.module or ""
    else:
        package_parts = source_module.split(".") if is_package else source_module.split(".")[:-1]
        parent_count = node.level - 1
        if parent_count > len(package_parts):
            raise ValueError(f"{source_module}:{node.lineno}: 상대 import가 소스 루트를 벗어난다")
        base_parts = package_parts[: len(package_parts) - parent_count]
        if node.module:
            base_parts.extend(node.module.split("."))
        base = ".".join(base_parts)

    base_layer = _target_layer(base)
    if base_layer is not None and not (
        base_layer == _source_layer(source_module) and _is_layer_package_root(base, base_layer)
    ):
        return [_ImportTarget(base, node.lineno)]

    return [
        _ImportTarget(f"{base}.{alias.name}" if base else alias.name, node.lineno)
        for alias in node.names
        if alias.name != "*"
    ]


def _is_layer_package_root(module: str, layer: str) -> bool:
    if layer.startswith(SLICE_PREFIX):
        parts = _slice_module_parts(module)
        return parts is not None and parts[1] is None
    if layer == RUNTIME_LAYER:
        return module == f"{AGENTS_PACKAGE}.runtime"
    if layer == SHARED_LAYER:
        return module == f"{AGENTS_PACKAGE}.shared"
    if layer == API_LAYER:
        return module == API_ROOT
    if layer == WORKER_LAYER:
        return module == WORKER_ROOT
    if layer == SHARED_APP_LAYER:
        return module == SHARED_ROOT
    return False


def _is_allowed(
    source: str,
    target: str,
    source_module: str,
    target_module: str,
) -> bool:
    if source == API_LAYER:
        return target == API_LAYER or target.startswith("shared:")
    if source == WORKER_LAYER:
        return target == WORKER_LAYER or target.startswith("shared:")
    if source == SHARED_APP_LAYER:
        return target.startswith("shared:")
    if source.startswith(SLICE_PREFIX):
        if target not in {source, RUNTIME_LAYER, SHARED_LAYER}:
            return False
        return not _slice_foundation_depends_on_execution(source_module, target_module)
    if source == RUNTIME_LAYER:
        if target not in {RUNTIME_LAYER, SHARED_LAYER}:
            return False
        return not _telemetry_depends_on_runtime_engine(source_module, target_module)
    if source == SHARED_LAYER:
        return target == SHARED_LAYER
    return False


def _slice_foundation_depends_on_execution(source_module: str, target_module: str) -> bool:
    source_parts = _slice_module_parts(source_module)
    target_parts = _slice_module_parts(target_module)
    if source_parts is None or target_parts is None:
        return False
    source_owner, source_component = source_parts
    target_owner, target_component = target_parts
    return (
        source_owner == target_owner
        and source_component in SLICE_FOUNDATION_MODULES
        and target_component in SLICE_EXECUTION_MODULES
    )


def _slice_module_parts(module: str) -> tuple[str, str | None] | None:
    prefix = f"{AGENTS_PACKAGE}."
    if not module.startswith(prefix):
        return None
    parts = module.removeprefix(prefix).split(".")
    if not parts or parts[0] in {"runtime", "shared"}:
        return None
    return parts[0], parts[1] if len(parts) > 1 else None


def _telemetry_depends_on_runtime_engine(source_module: str, target_module: str) -> bool:
    source_component = _runtime_component(source_module)
    target_component = _runtime_component(target_module)
    return source_component == "telemetry" and target_component in {"execution", "llm"}


def _runtime_component(module: str) -> str | None:
    prefix = f"{AGENTS_PACKAGE}.runtime."
    if not module.startswith(prefix):
        return None
    return module.removeprefix(prefix).split(".", maxsplit=1)[0]


def _display_layer(layer: str) -> str:
    return layer.rsplit(":", maxsplit=1)[-1]


def main(argv: list[str]) -> int:
    """검사 경로를 받아 위반을 출력하고 종료 코드를 반환한다."""
    source_root = Path(argv[0]) if argv else Path("src")
    if not source_root.exists():
        sys.stdout.write(f"{source_root}: 경로가 없다\n")
        return 1
    violations = find_violations(source_root)
    if violations:
        sys.stdout.write("\n".join(item.message() for item in violations) + "\n")
        return 1
    sys.stdout.write("Python 내부 의존 규칙 검사 통과\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
