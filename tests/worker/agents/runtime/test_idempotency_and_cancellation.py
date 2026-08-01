"""idempotencyKey 중복 실행 방지와 runId 취소 검증."""

from __future__ import annotations

import asyncio

import pytest

from tests.support.prompts import CONTRACT_VERSION
from tracer_agent.worker.agents.runtime.execution.registry import cancel_run
from tracer_agent.worker.agents.runtime.execution.runner import execute


async def test_같은_idempotency_key는_본체를_한_번만_실행한다() -> None:
    calls = 0

    async def body(_usage: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    first = await execute(
        "title-suggestion",
        "model",
        1_000,
        body,
        idempotency_key="key-1",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )
    second = await execute(
        "title-suggestion",
        "model",
        1_000,
        body,
        idempotency_key="key-1",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )

    assert calls == 1
    assert first.data == {"ok": True}
    assert second.data == {"ok": True}


async def test_동시에_들어온_같은_key는_진행중인_실행을_공유한다() -> None:
    calls = 0
    release = asyncio.Event()

    async def body(_usage: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        await release.wait()
        return {"ok": True}

    first = asyncio.ensure_future(
        execute(
            "title-suggestion",
            "model",
            1_000,
            body,
            idempotency_key="key-2",
            prompt_version=CONTRACT_VERSION,
            tool_contract_version=CONTRACT_VERSION,
        )
    )
    await asyncio.sleep(0)  # 첫 실행이 캐시에 등록될 시간을 준다.
    second = asyncio.ensure_future(
        execute(
            "title-suggestion",
            "model",
            1_000,
            body,
            idempotency_key="key-2",
            prompt_version=CONTRACT_VERSION,
            tool_contract_version=CONTRACT_VERSION,
        )
    )
    await asyncio.sleep(0)
    release.set()

    result_first, result_second = await asyncio.gather(first, second)

    assert calls == 1
    assert result_first.data == result_second.data == {"ok": True}


async def test_idempotency_key가_다르면_각각_실행한다() -> None:
    calls = 0

    async def body(_usage: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    await execute(
        "title-suggestion",
        "model",
        1_000,
        body,
        idempotency_key="key-a",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )
    await execute(
        "title-suggestion",
        "model",
        1_000,
        body,
        idempotency_key="key-b",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )

    assert calls == 2


async def test_idempotency_key가_없으면_매번_실행한다() -> None:
    calls = 0

    async def body(_usage: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    await execute(
        "title-suggestion",
        "model",
        1_000,
        body,
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )
    await execute(
        "title-suggestion",
        "model",
        1_000,
        body,
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )

    assert calls == 2


async def test_실패한_idempotency_key는_다음_Temporal_시도에서_다시_실행한다() -> None:
    calls = 0

    async def body(_usage: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("retryable provider failure")
        return {"ok": True}

    first = await execute(
        "recipe-scan",
        "model",
        1_000,
        body,
        idempotency_key="retry-key",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )
    second = await execute(
        "recipe-scan",
        "model",
        1_000,
        body,
        idempotency_key="retry-key",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )

    assert first.error is not None
    assert second.data == {"ok": True}
    assert calls == 2


async def test_만료시각이_지나도_진행중인_실행은_다시_시작하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tracer_agent.worker.agents.runtime.execution.registry as registry

    calls = 0
    release = asyncio.Event()
    clock = 0.0
    monkeypatch.setattr(registry, "_IDEMPOTENCY_TTL_S", 1.0)
    monkeypatch.setattr(registry.time, "monotonic", lambda: clock)

    async def body(_usage: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        await release.wait()
        return {"ok": True}

    first = asyncio.ensure_future(
        execute(
            "recipe-scan",
            "model",
            1_000,
            body,
            idempotency_key="long-running-key",
            prompt_version=CONTRACT_VERSION,
            tool_contract_version=CONTRACT_VERSION,
        )
    )
    await asyncio.sleep(0)
    clock = 2.0
    second = asyncio.ensure_future(
        execute(
            "recipe-scan",
            "model",
            1_000,
            body,
            idempotency_key="long-running-key",
            prompt_version=CONTRACT_VERSION,
            tool_contract_version=CONTRACT_VERSION,
        )
    )
    await asyncio.sleep(0)
    release.set()

    await asyncio.gather(first, second)

    assert calls == 1


async def test_성공_캐시_TTL은_실행_완료부터_계산한다(monkeypatch: pytest.MonkeyPatch) -> None:
    import tracer_agent.worker.agents.runtime.execution.registry as registry

    calls = 0
    release = asyncio.Event()
    clock = 0.0
    monkeypatch.setattr(registry, "_IDEMPOTENCY_TTL_S", 1.0)
    monkeypatch.setattr(registry.time, "monotonic", lambda: clock)

    async def body(_usage: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        await release.wait()
        return {"ok": True}

    first = asyncio.ensure_future(
        execute(
            "recipe-scan",
            "model",
            1_000,
            body,
            idempotency_key="completed-cache-key",
            prompt_version=CONTRACT_VERSION,
            tool_contract_version=CONTRACT_VERSION,
        )
    )
    await asyncio.sleep(0)
    clock = 2.0
    release.set()
    await first
    clock = 2.5

    second = await execute(
        "recipe-scan",
        "model",
        1_000,
        body,
        idempotency_key="completed-cache-key",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )

    assert second.data == {"ok": True}
    assert calls == 1


@pytest.mark.parametrize(
    ("label", "model", "input_hash"),
    [
        ("title-suggestion", "other-model", "input-a"),
        ("title-suggestion", "model", "input-b"),
    ],
)
async def test_같은_key의_다른_실행_범위는_거부한다(
    label: str,
    model: str,
    input_hash: str,
) -> None:
    calls = 0
    key = f"conflicting-key-{model}-{input_hash}"

    async def body(_usage: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    await execute(
        "title-suggestion",
        "model",
        1_000,
        body,
        idempotency_key=key,
        input_hash="input-a",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )
    conflict = await execute(
        label,
        model,
        1_000,
        body,
        idempotency_key=key,
        input_hash=input_hash,
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )

    assert calls == 1
    assert conflict.error is not None
    assert conflict.error.subtype == "invalid_request_error"


async def test_같은_key라도_다른_agent는_독립적으로_실행한다() -> None:
    calls = 0

    async def body(_usage: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    first = await execute(
        "title-suggestion",
        "model",
        1_000,
        body,
        idempotency_key="agent-scoped-key",
        input_hash="input-a",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )
    second = await execute(
        "recipe-scan",
        "model",
        1_000,
        body,
        idempotency_key="agent-scoped-key",
        input_hash="input-a",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )

    assert first.error is None
    assert second.error is None
    assert calls == 2


async def test_같은_key의_다른_job은_거부한다() -> None:
    calls = 0

    async def body(_usage: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"ok": True}

    await execute(
        "title-suggestion",
        "model",
        1_000,
        body,
        job_id="job-a",
        idempotency_key="job-scoped-key",
        input_hash="input-a",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )
    conflict = await execute(
        "title-suggestion",
        "model",
        1_000,
        body,
        job_id="job-b",
        idempotency_key="job-scoped-key",
        input_hash="input-a",
        prompt_version=CONTRACT_VERSION,
        tool_contract_version=CONTRACT_VERSION,
    )

    assert calls == 1
    assert conflict.error is not None
    assert conflict.error.subtype == "invalid_request_error"


class TestCancelRun:
    async def test_취소된_실행은_CancelledError를_그대로_재전파한다(self) -> None:
        started = asyncio.Event()

        async def body(_usage: object) -> dict[str, object]:
            started.set()
            await asyncio.Event().wait()  # 영원히 대기(취소로만 끝난다)

        task = asyncio.ensure_future(
            execute(
                "recipe-scan",
                "model",
                5_000,
                body,
                job_id="job-cancel-1",
                prompt_version=CONTRACT_VERSION,
                tool_contract_version=CONTRACT_VERSION,
            )
        )
        await started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_runId로_등록된_실행을_취소하면_대기중인_호출자도_취소된다(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def body(_usage: object) -> dict[str, object]:
            started.set()
            await release.wait()
            return {"ok": True}

        task = asyncio.ensure_future(
            execute(
                "recipe-scan",
                "model",
                5_000,
                body,
                run_id="run-1",
                prompt_version=CONTRACT_VERSION,
                tool_contract_version=CONTRACT_VERSION,
            )
        )
        await started.wait()

        assert cancel_run("run-1") is True

        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_runId가_없으면_jobId로_등록된다(self) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def body(_usage: object) -> dict[str, object]:
            started.set()
            await release.wait()
            return {"ok": True}

        task = asyncio.ensure_future(
            execute(
                "recipe-scan",
                "model",
                5_000,
                body,
                job_id="job-cancel-2",
                prompt_version=CONTRACT_VERSION,
                tool_contract_version=CONTRACT_VERSION,
            )
        )
        await started.wait()

        assert cancel_run("job-cancel-2") is True

        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_없는_run_id_취소는_False를_돌려준다(self) -> None:
        assert cancel_run("no-such-run") is False

    async def test_끝난_실행은_레지스트리에서_빠져_취소해도_False다(self) -> None:
        async def body(_usage: object) -> dict[str, object]:
            return {"ok": True}

        res = await execute(
            "title-suggestion",
            "model",
            1_000,
            body,
            run_id="run-done",
            prompt_version=CONTRACT_VERSION,
            tool_contract_version=CONTRACT_VERSION,
        )

        assert res.data == {"ok": True}
        assert cancel_run("run-done") is False
