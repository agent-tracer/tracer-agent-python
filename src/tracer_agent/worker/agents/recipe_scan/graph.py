"""recipe-scan의 정적 LangGraph 위상을 소유한다."""

from __future__ import annotations

from langgraph.errors import NodeError
from langgraph.graph import START
from langgraph.types import Command, RetryPolicy, Send, TimeoutPolicy

from tracer_agent.shared.agents.envelope.catalog import CATALOG
from tracer_agent.shared.agents.recipe_scan.models import ProbeAssignment, ProbeDispatch, RecipeScanState
from tracer_agent.shared.agents.shared.graph_state import remaining_cost_usd, remaining_turns

from ..runtime.durable_graph import DurableGraph
from ..runtime.errors import is_retryable_node_failure
from ..runtime.llm.budget import lease_shares
from ..runtime.routes import EMPTY
from ..runtime.timeouts import deadline_fraction_s
from ..runtime.validation_graph import add_validation_tail, new_graph, observed
from ..shared.empty_result import DEGRADED
from .nodes.candidate import InvestigateNode, ValidateCandidateNode
from .nodes.probe import ProbeNode
from .nodes.survey import SurveyNode
from .reservation import load_wall_clock_policy

_RECIPE_SCAN_DEADLINE_MS = CATALOG["recipe.scan"].deadline_ms
_WALL_CLOCK = load_wall_clock_policy()

# 전문가가 진전 없이 머무는 상한이며, 몫이 큰 전문가가 자연히 더 오래 실행되는 것을 막지 않는다.
PROBE_WALL_CLOCK_CEILING_S = deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, _WALL_CLOCK.probe)
_PROBE_SEND_TIMEOUT = TimeoutPolicy(
    idle_timeout=deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, _WALL_CLOCK.probe)
)
_SURVEY_TIMEOUT = TimeoutPolicy(
    run_timeout=deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, _WALL_CLOCK.survey),
    idle_timeout=deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, 0.08),
)
_INVESTIGATE_TIMEOUT = TimeoutPolicy(
    run_timeout=deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, _WALL_CLOCK.synthesis),
    idle_timeout=deadline_fraction_s(_RECIPE_SCAN_DEADLINE_MS, 0.2),
)
# 예산 초과와 출력 절단은 같은 예산으로 다시 돌아도 같은 자리에서 끝나므로 재시도하지 않는다.
_NODE_RETRY = RetryPolicy(max_attempts=3, retry_on=is_retryable_node_failure)


async def _survey_error_handler(
    _state: RecipeScanState,
    *,
    error: NodeError,  # noqa: ARG001 (langgraph는 이 이름으로만 주입한다)
) -> Command[str]:
    """조율자 계획이 재시도 끝에도 실패하면 빈 계획으로 하향해 결론 없는 성공으로 끝낸다."""
    # 낮춘 실행을 저장할 패턴이 없던 실행과 원장에서 구분하도록 실패한 사유를 함께 적는다.
    return Command(update={"plan": None, "empty_result_reason": DEGRADED}, goto=EMPTY)


def _remaining(state: RecipeScanState) -> tuple[int, float]:
    """예약을 뗀 뒤 팬아웃이 아직 쓰지 않은 턴과 달러다."""
    return remaining_turns(state), remaining_cost_usd(state)


def _fan_out(probes: list[ProbeAssignment], remaining_turns: int, remaining_usd: float) -> list[Send]:
    if not probes or remaining_turns <= 0 or remaining_usd <= 0.0:
        return []
    leases = lease_shares([assignment.share for assignment in probes], remaining_turns, remaining_usd)
    return [
        Send(
            ProbeNode.name,
            ProbeDispatch(
                assignment=assignment,
                siblings=[other for other in probes if other.probe != assignment.probe],
                max_turns=lease.max_turns,
                max_cost_usd=lease.max_cost_usd,
            ),
            timeout=_PROBE_SEND_TIMEOUT,
        )
        for assignment, lease in zip(probes, leases, strict=True)
    ]


def _dispatch(state: RecipeScanState) -> list[Send]:
    plan = state["plan"]
    # 조율자가 근거를 수집하는 도구를 갖지 않으므로 띄울 전문가가 없으면 바로 빈 결과로 끝낸다.
    if plan is None or not plan.probes:
        return [Send(EMPTY, state)]
    remaining_turns, remaining_usd = _remaining(state)
    return _fan_out(plan.probes, remaining_turns, remaining_usd) or [Send(EMPTY, state)]


def _after_investigate(state: RecipeScanState) -> list[Send]:
    plan = state["redispatch"]
    if plan is None:
        return [Send(ValidateCandidateNode.name, state)]
    remaining_turns, remaining_usd = _remaining(state)
    return _fan_out(plan.probes, remaining_turns, remaining_usd) or [Send(ValidateCandidateNode.name, state)]


_graph = new_graph(RecipeScanState)
observed(
    _graph,
    SurveyNode.name,
    retry_policy=_NODE_RETRY,
    error_handler=_survey_error_handler,
    timeout=_SURVEY_TIMEOUT,
)
observed(_graph, ProbeNode.name)
observed(_graph, InvestigateNode.name, retry_policy=_NODE_RETRY, timeout=_INVESTIGATE_TIMEOUT)
add_validation_tail(_graph, ValidateCandidateNode.name)
_graph.add_edge(START, SurveyNode.name)
_graph.add_conditional_edges(SurveyNode.name, _dispatch, [ProbeNode.name, EMPTY])
_graph.add_edge(ProbeNode.name, InvestigateNode.name)
_graph.add_conditional_edges(
    InvestigateNode.name, _after_investigate, [ProbeNode.name, ValidateCandidateNode.name]
)

# 오류 처리기를 붙인 노드마다 LangGraph 가 자기 몫의 노드를 더하므로 그 자리는 세지 않는다.
RECIPE_SCAN_NODE_NAMES: frozenset[str] = frozenset(name for name in _graph.nodes if not name.startswith("__"))

RECIPE_SCAN_GRAPH = DurableGraph(_graph)
