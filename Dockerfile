# syntax=docker/dockerfile:1

# ---- LangGraph 에이전트 서비스: uv로 의존성 설치 후 소스 실행 ----
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# uv 바이너리만 복사해 온다(빌드 캐시 친화적).
COPY --from=ghcr.io/astral-sh/uv:0.10.2 /uv /usr/local/bin/uv

# 의존성 레이어는 잠금 파일만으로 먼저 만들고 프로젝트는 소스와 함께 설치한다.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

EXPOSE 8800
CMD ["python", "-m", "tracer_agent.api"]
