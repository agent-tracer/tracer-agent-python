from __future__ import annotations

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from tracer_agent.api.evaluation import EvaluationRunRequest, run_evaluation


@pytest.mark.asyncio
async def test_에이전트를_모르는_평가_요청은_모델을_부르지_않고_거절한다() -> None:
    request = EvaluationRunRequest.model_validate(
        {
            "executionId": "execution-1",
            "attemptId": "execution-1:1",
            "backend": "python",
            "model": "model",
            "prompt": {"versionId": "prompt-1", "contentHash": "0" * 64},
            "toolContractVersion": "tools-1",
            "input": {"question": "hello"},
            "referenceOutput": {"answer": "ok"},
            "maxCostUsd": 1,
            "apiKey": "fixture-key",
            "modelRates": {"model": {"input": 1, "output": 2, "cacheWrite": 1, "cacheRead": 1}},
        }
    )
    with pytest.raises(HTTPException) as raised:
        await run_evaluation(request)
    assert raised.value.status_code == 422


def test_콘텐츠_해시_형식이_틀리면_봉투_검증에서_거부한다() -> None:
    with pytest.raises(ValidationError):
        EvaluationRunRequest.model_validate(
            {
                "executionId": "execution-1",
                "attemptId": "execution-1:1",
                "backend": "python",
                "model": "model",
                "prompt": {
                    "versionId": "prompt-1",
                    "contentHash": "wrong",
                },
                "toolContractVersion": "tools-1",
                "input": {},
                "referenceOutput": {"secretAnswer": True},
                "maxCostUsd": 1,
                "apiKey": "fixture-key",
                "modelRates": {
                    "model": {
                        "input": 1,
                        "output": 1,
                        "cacheWrite": 1,
                        "cacheRead": 1,
                    }
                },
            }
        )
