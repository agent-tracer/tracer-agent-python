"""프로젝터 부트스트랩: `python -m tracer_agent.projector`."""

from __future__ import annotations

import uvicorn

from ..shared.config import get_settings
from .app import app


def main() -> None:
    """색인 세우기와 배출과 투영을 맡는 프로세스를 띄운다."""
    settings = get_settings()
    uvicorn.run(app, host=settings.agent_projector_host, port=settings.agent_projector_port)


if __name__ == "__main__":
    main()
