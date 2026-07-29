from tracer_agent.worker.agents.runtime.llm.structured_agent import recursion_config
from tracer_agent.worker.agents.runtime.telemetry.disclosure import TraceBackend, TraceSafeMetadata


def test_추적_뿌리_메타데이터가_langsmith_설정을_이룬다():
    metadata = TraceSafeMetadata(
        agent_name="test_agent",
        backend=TraceBackend.PYTHON,
        model_requested="claude-3-5-sonnet",
        prompt_version="v1.0",
        execution_id="e1",
        attempt_id="a1",
        job_id="j1",
        prompt_content_hash="abcd",
        tool_contract_version="v2",
        experiment_id="exp1",
        example_id="ex1",
        variant_id="v1",
    )

    ls_meta = metadata.langsmith_metadata()
    assert ls_meta["agent_tracer.agent.name"] == "test_agent"
    assert ls_meta["agent_tracer.backend"] == "python"
    assert ls_meta["agent_tracer.model.requested"] == "claude-3-5-sonnet"
    assert ls_meta["agent_tracer.execution.id"] == "e1"

    config = recursion_config(5, metadata)
    assert config["metadata"] == ls_meta
    assert config["run_name"] == "test_agent"
    assert "test_agent" in config["tags"]
    assert "python" in config["tags"]
    assert "v1.0" in config["tags"]
    assert config.get("run_id") is not None
