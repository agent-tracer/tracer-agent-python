"""GenAI 관측 속성과 추적 전파와 스팬 경계를 검증한다."""

from __future__ import annotations

from opentelemetry import trace as trace_api

from tracer_agent.shared.agents.shared.models import UsageDTO
from tracer_agent.worker.agents.runtime.telemetry import attributes, spans
from tracer_agent.worker.agents.runtime.telemetry.attributes import (
    BACKEND_ATTRIBUTE,
    BILLABLE_BASE_INPUT_TOKENS_ATTRIBUTE,
    CACHE_CREATION_INPUT_TOKENS_ATTRIBUTE,
    CACHE_READ_INPUT_TOKENS_ATTRIBUTE,
    GEN_AI_OPERATION,
    INPUT_TOKENS_ATTRIBUTE,
    JOB_ID_ATTRIBUTE,
    JOB_KIND_ATTRIBUTE,
    OUTPUT_TOKENS_ATTRIBUTE,
    build_client_attributes,
    build_invoke_agent_attributes,
    build_usage_attributes,
    token_measurements,
)
from tracer_agent.worker.agents.runtime.telemetry.propagation import (
    extract_trace_context,
    inject_trace_context,
)
from tracer_agent.worker.agents.runtime.telemetry.spans import invoke_agent_span

# 전역 TracerProvider는 conftest.py가 한 번만 등록한다(app/test_app.py가 같은 exporter를 공유).


class TestAttributes:
    def test_invoke_agent_스팬은_잡_id와_잡_종류를_싣는다(self) -> None:
        attrs = build_invoke_agent_attributes(
            job_id="job-1",
            agent_name="recipe-scan",
            model="claude-sonnet-4-6",
        )

        assert attrs[JOB_ID_ATTRIBUTE] == "job-1"
        assert attrs["gen_ai.operation.name"] == GEN_AI_OPERATION["invoke_agent"]
        assert attrs["gen_ai.agent.name"] == "recipe-scan"
        assert attrs["gen_ai.request.model"] == "claude-sonnet-4-6"
        assert attrs[JOB_KIND_ATTRIBUTE] == "recipe.scan"
        assert attrs[BACKEND_ATTRIBUTE] == "python"

    def test_client_메트릭_라벨은_토큰_수를_싣지_않는다(self) -> None:
        attrs = build_client_attributes("claude-haiku-4-5", "max_tokens")

        assert attrs["gen_ai.operation.name"] == GEN_AI_OPERATION["chat"]
        assert attrs["gen_ai.provider.name"] == "anthropic"
        assert attrs["gen_ai.request.model"] == "claude-haiku-4-5"
        assert attrs["error.type"] == "max_tokens"
        assert not [key for key in attrs if key.startswith("gen_ai.usage.")]

    def test_token_measurements는_input_output만_낸다(self) -> None:
        usage = UsageDTO(inputTokens=11, outputTokens=7, cacheReadTokens=3, cacheCreationTokens=2)

        assert token_measurements(usage) == [
            (11, {"gen_ai.token.type": "input"}),
            (7, {"gen_ai.token.type": "output"}),
        ]

    def test_tool_span_attrs는_파라미터_fingerprint만_싣는다(self) -> None:
        left = attributes.build_tool_span_attributes(
            "search_events",
            "recipe-scan",
            parameters={"q": "cookie", "limit": 20},
        )
        right = attributes.build_tool_span_attributes(
            "search_events",
            "recipe-scan",
            parameters={"limit": 20, "q": "cookie"},
        )

        assert (
            left["agent_tracer.tool.parameters.fingerprint"]
            == right["agent_tracer.tool.parameters.fingerprint"]
        )
        assert len(str(left["agent_tracer.tool.parameters.fingerprint"])) == 16
        assert "cookie" not in left.values()

    def test_usage_attributes는_cache_포함_총_input과_billable_base를_분리해서_낸다(self) -> None:
        usage = UsageDTO(inputTokens=11, outputTokens=7, cacheReadTokens=3, cacheCreationTokens=2)

        attrs = build_usage_attributes(usage)

        assert attrs[INPUT_TOKENS_ATTRIBUTE] == 16
        assert attrs[OUTPUT_TOKENS_ATTRIBUTE] == 7
        assert attrs[CACHE_READ_INPUT_TOKENS_ATTRIBUTE] == 3
        assert attrs[CACHE_CREATION_INPUT_TOKENS_ATTRIBUTE] == 2
        assert attrs[BILLABLE_BASE_INPUT_TOKENS_ATTRIBUTE] == 11

    def test_usage가_없으면_속성을_내지_않는다(self) -> None:
        assert build_usage_attributes(None) == {}


class TestTraceContextPropagation:
    async def test_inject와_extract가_같은_trace_id로_왕복한다(self) -> None:
        tracer = trace_api.get_tracer("test")
        with tracer.start_as_current_span("caller") as span:
            headers: dict[str, str] = {}
            inject_trace_context(headers)
            expected_trace_id = span.get_span_context().trace_id

        assert "traceparent" in headers
        parsed_context = extract_trace_context(headers)
        extracted_span = trace_api.get_current_span(parsed_context)
        assert extracted_span.get_span_context().trace_id == expected_trace_id

    async def test_invoke_agent_span이_전달받은_parent_context를_부모로_삼는다(self) -> None:
        tracer = trace_api.get_tracer("test")
        with tracer.start_as_current_span("caller") as caller_span:
            headers: dict[str, str] = {}
            inject_trace_context(headers)
            expected_trace_id = caller_span.get_span_context().trace_id

        parent_context = extract_trace_context(headers)
        async with invoke_agent_span(
            job_id="job-1", agent_name="recipe-scan", model="m", parent_context=parent_context
        ) as span:
            assert span.get_span_context().trace_id == expected_trace_id

    async def test_parent_context가_없으면_새_trace로_시작한다(self) -> None:
        async with invoke_agent_span(job_id=None, agent_name="recipe-scan", model="m") as span:
            assert span.get_span_context().trace_id != 0

    async def test_tool_span_안에서_inject하면_invoke_agent_span과_같은_trace_id를_담는다(
        self,
    ) -> None:
        async with invoke_agent_span(job_id=None, agent_name="recipe-scan", model="m") as agent_span:
            trace_id = agent_span.get_span_context().trace_id
            async with spans.tool_span("search_events", agent_name="recipe-scan") as tool_span_obj:
                assert tool_span_obj.get_span_context().trace_id == trace_id
                headers: dict[str, str] = {}
                inject_trace_context(headers)

        parsed_context = extract_trace_context(headers)
        assert trace_api.get_current_span(parsed_context).get_span_context().trace_id == trace_id
