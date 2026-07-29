"""Python 내부 계층 가드의 import 해석과 위반 판정을 검증한다."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.check_internal_dependencies import find_violations

SERVICE_ROOT = Path(__file__).resolve().parents[1]
CHECKER = SERVICE_ROOT / "scripts" / "check_internal_dependencies.py"


def write_module(source_root: Path, module: str, source: str) -> None:
    """테스트용 모듈 경로와 본문을 만든다."""
    path = source_root.joinpath(*module.split(".")).with_suffix(".py")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_접수와_워커가_공유를_보고_같은_slice_import는_통과시킨다(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    write_module(
        source_root,
        "tracer_agent.api.app",
        "from ..shared.agents.recipe_scan import models\n"
        "from ..shared.agents.runtime.execution import registry\n",
    )
    write_module(
        source_root,
        "tracer_agent.worker.worker",
        "from ..shared.agents.recipe_scan import agent\n"
        "from ..shared.agents.runtime.execution import runner\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.workflows.dispatch",
        "from ..agents.shared import models\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.agents.recipe_scan.agent",
        "from . import models\n"
        "from ..runtime import callback\n"
        "from ..shared import models as shared_models\n"
        "import tracer_agent.shared.agents.runtime.errors\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.agents.runtime.ledger",
        "from ..shared import models\n",
    )
    write_module(source_root, "tracer_agent.shared.agents.shared.models", "import json\n")

    assert find_violations(source_root) == []


def test_접수가_워커를_워커가_접수를_공유가_접수와_워커를_참조하면_실패한다(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    write_module(
        source_root,
        "tracer_agent.api.leak",
        "from ..worker.worker import main\n",
    )
    write_module(
        source_root,
        "tracer_agent.worker.leak",
        "from ..api.app import app\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.leak",
        "from ..api.app import app\nfrom ..worker.worker import main\n",
    )

    violations = find_violations(source_root)

    assert {(item.source_module, item.target_module) for item in violations} == {
        ("tracer_agent.api.leak", "tracer_agent.worker.worker"),
        ("tracer_agent.worker.leak", "tracer_agent.api.app"),
        ("tracer_agent.shared.leak", "tracer_agent.api.app"),
        ("tracer_agent.shared.leak", "tracer_agent.worker.worker"),
    }


def test_relative_absolute_import와_from_alias의_계층_위반을_모두_찾는다(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    write_module(
        source_root,
        "tracer_agent.shared.agents.recipe_scan.relative_leak",
        "from .. import task_cleanup\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.agents.recipe_scan.absolute_leak",
        "import tracer_agent.shared.agents.title_suggestion.agent\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.agents.recipe_scan.from_leak",
        "from tracer_agent.shared.agents import recipe_scan, title_suggestion\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.agents.runtime.leak",
        "from ..recipe_scan import models\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.agents.shared.leak",
        "from ..runtime import callback\n",
    )

    violations = find_violations(source_root)

    assert {(item.source_module, item.target_module) for item in violations} == {
        (
            "tracer_agent.shared.agents.recipe_scan.absolute_leak",
            "tracer_agent.shared.agents.title_suggestion.agent",
        ),
        (
            "tracer_agent.shared.agents.recipe_scan.from_leak",
            "tracer_agent.shared.agents.title_suggestion",
        ),
        (
            "tracer_agent.shared.agents.recipe_scan.relative_leak",
            "tracer_agent.shared.agents.task_cleanup",
        ),
        ("tracer_agent.shared.agents.runtime.leak", "tracer_agent.shared.agents.recipe_scan"),
        ("tracer_agent.shared.agents.shared.leak", "tracer_agent.shared.agents.runtime"),
    }


def test_slice의_models와_prompts가_실행_구현을_참조하면_실패한다(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    write_module(
        source_root,
        "tracer_agent.shared.agents.recipe_scan.models",
        "from .agent import run_recipe_scan\n"
        "from .graph import build_recipe_scan_graph\n"
        "from .nodes import candidate\n"
        "from .tools import client\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.agents.recipe_scan.prompts",
        "from .nodes.evidence import create_evidence_nodes\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.agents.task_cleanup.models",
        "from . import tools\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.agents.title_suggestion.prompts",
        "from tracer_agent.shared.agents.title_suggestion import graph\n",
    )

    violations = find_violations(source_root)

    assert {(item.source_module, item.target_module) for item in violations} == {
        ("tracer_agent.shared.agents.recipe_scan.models", "tracer_agent.shared.agents.recipe_scan.agent"),
        ("tracer_agent.shared.agents.recipe_scan.models", "tracer_agent.shared.agents.recipe_scan.graph"),
        ("tracer_agent.shared.agents.recipe_scan.models", "tracer_agent.shared.agents.recipe_scan.nodes"),
        ("tracer_agent.shared.agents.recipe_scan.models", "tracer_agent.shared.agents.recipe_scan.tools"),
        (
            "tracer_agent.shared.agents.recipe_scan.prompts",
            "tracer_agent.shared.agents.recipe_scan.nodes.evidence",
        ),
        (
            "tracer_agent.shared.agents.task_cleanup.models",
            "tracer_agent.shared.agents.task_cleanup.tools",
        ),
        (
            "tracer_agent.shared.agents.title_suggestion.prompts",
            "tracer_agent.shared.agents.title_suggestion.graph",
        ),
    }


def test_runtime_telemetry가_execution과_llm을_참조하면_실패한다(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    write_module(
        source_root,
        "tracer_agent.shared.agents.runtime.telemetry.leak",
        "from ..execution import trace\nfrom ..llm import client\n",
    )
    write_module(
        source_root,
        "tracer_agent.shared.agents.runtime.telemetry.root_leak",
        "from .. import execution, llm\n",
    )

    violations = find_violations(source_root)

    assert {(item.source_module, item.target_module) for item in violations} == {
        (
            "tracer_agent.shared.agents.runtime.telemetry.leak",
            "tracer_agent.shared.agents.runtime.execution",
        ),
        (
            "tracer_agent.shared.agents.runtime.telemetry.leak",
            "tracer_agent.shared.agents.runtime.llm",
        ),
        (
            "tracer_agent.shared.agents.runtime.telemetry.root_leak",
            "tracer_agent.shared.agents.runtime.execution",
        ),
        (
            "tracer_agent.shared.agents.runtime.telemetry.root_leak",
            "tracer_agent.shared.agents.runtime.llm",
        ),
    }


def test_CLI가_위반_위치와_의존_방향을_출력하고_실패한다(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    write_module(
        source_root,
        "tracer_agent.shared.agents.recipe_scan.leak",
        "from ..task_cleanup import agent\n",
    )

    result = subprocess.run(
        [sys.executable, str(CHECKER), str(source_root)],
        cwd=SERVICE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "recipe_scan.leak:1" in result.stdout
    assert "recipe_scan -> task_cleanup" in result.stdout


def test_CLI가_접수와_워커_사이_위반도_같은_방식으로_출력한다(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    write_module(
        source_root,
        "tracer_agent.api.leak",
        "from ..worker.worker import main\n",
    )

    result = subprocess.run(
        [sys.executable, str(CHECKER), str(source_root)],
        cwd=SERVICE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "api.leak:1" in result.stdout
    assert "api -> worker" in result.stdout
