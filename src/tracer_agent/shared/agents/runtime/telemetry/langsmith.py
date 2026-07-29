"""LangSmith SDK의 opt-in과 원문 비공개 기본값을 프로세스 경계에 적용한다."""

from __future__ import annotations

import os


def configure_langsmith(
    *,
    enabled: bool,
    endpoint: str,
    project: str,
    workspace_id: str | None,
    api_key: str | None,
    disclose_trace_payloads: bool,
) -> None:
    """설정 schema를 SDK 환경 계약에 연결하며 원문 공개 여부는 호출자가 정한다."""
    os.environ["LANGSMITH_TRACING"] = "true" if enabled else "false"
    os.environ["LANGSMITH_ENDPOINT"] = endpoint
    os.environ["LANGSMITH_PROJECT"] = project
    default_hide_payloads = "false" if disclose_trace_payloads else "true"
    os.environ["LANGSMITH_HIDE_INPUTS"] = default_hide_payloads
    os.environ["LANGSMITH_HIDE_OUTPUTS"] = default_hide_payloads
    if workspace_id is not None:
        os.environ["LANGSMITH_WORKSPACE_ID"] = workspace_id
    else:
        os.environ.pop("LANGSMITH_WORKSPACE_ID", None)
    if api_key is not None:
        os.environ["LANGSMITH_API_KEY"] = api_key
    else:
        os.environ.pop("LANGSMITH_API_KEY", None)
