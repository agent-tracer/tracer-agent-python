"""uvicorn 부트스트랩: `python -m tracer_agent.api`."""

from __future__ import annotations

import uvicorn

from ..shared.config import get_settings
from .app import app


def main() -> None:
    """접수 프로세스를 띄우며 프롬프트 조각 해석과 정합 검사는 그래프를 실제로 실행하는 워커가 맡는다."""
    settings = get_settings()
    uvicorn.run(app, host=settings.tracer_agent_host, port=settings.tracer_agent_port)


if __name__ == "__main__":
    main()
