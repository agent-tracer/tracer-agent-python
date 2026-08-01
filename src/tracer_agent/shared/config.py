"""서비스 설정."""

from __future__ import annotations

import os
from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from temporalio.client import Client

# 배포가 이 환경변수로 큐 접두사를 주며, 나란히 띄운 두 구현체는 다른 값을 받아 서로 다른 큐를 본다.
TASK_QUEUE_PREFIX_ENV = "AGENT_TASK_QUEUE_PREFIX"
DEFAULT_TASK_QUEUE_PREFIX = "agent"

# LangGraph checkpoint 표는 이 구현체의 실행 상태이므로 계약이 소유한 표와 다른 스키마에 둔다.
CHECKPOINT_SCHEMA = "agent_langgraph"


class MonitorProfile(StrEnum):
    """TS 배포 단위와 같은 어휘를 쓰는 배포 프로파일이다."""

    LOCAL = "local"
    PRD = "prd"


def task_queue(key: str) -> str:
    """계약이 정한 큐 키 앞에 배포가 준 접두사를 붙여 큐의 완전한 이름을 만든다."""
    prefix = os.environ.get(TASK_QUEUE_PREFIX_ENV, "").strip() or DEFAULT_TASK_QUEUE_PREFIX
    return f"{prefix}-{key}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # local만 LangSmith에 프롬프트·응답 원문을 보낸다.
    monitor_profile: MonitorProfile = MonitorProfile.PRD
    # 저장된 자격을 감추고 되돌리는 키를 이 값에서 유도하며 prd는 이것 없이 자격을 다루지 않는다.
    monitor_settings_encryption_key: SecretStr | None = None

    tracer_agent_host: str = "0.0.0.0"
    tracer_agent_port: int = 8800

    agent_db_host: str = "agent-db"
    agent_db_port: int = 5432
    agent_db_name: str = "agent"
    agent_db_user: str = "root"
    agent_db_password: str = "root"

    kafka_brokers: str = "redpanda:29092"
    temporal_address: str = "temporal:7233"
    temporal_namespace: str = "default"
    # 단가표도 한도도 자격도 이 서비스가 갖지 않으므로 실행 봉투를 이 주소에서 받아 온다.
    tracer_api_url: str = "http://tracer-api:3902"
    # 프롬프트는 에이전트의 데이터이므로 워커가 자기 배포 단위의 창구에서 이번 실행이 쓸 판을 받아 온다.
    agent_api_url: str = "http://agent-api:8800"

    # LangSmith SDK가 같은 환경변수를 직접 읽으며 명시적 opt-in 전에는 trace를 보내지 않는다.
    langsmith_tracing: bool = False
    langsmith_endpoint: str = "https://api.smith.langchain.com"
    langsmith_project: str = "agent-tracer-local"
    langsmith_workspace_id: str | None = None
    langsmith_api_key: SecretStr | None = None

    @model_validator(mode="after")
    def validate_langsmith(self) -> Settings:
        """활성 trace가 불완전한 목적지 설정으로 뜨지 못하게 한다."""
        if not self.langsmith_tracing:
            return self
        if not self.langsmith_endpoint.strip():
            raise ValueError("LANGSMITH_ENDPOINT is required when LANGSMITH_TRACING=true")
        if not self.langsmith_project.strip():
            raise ValueError("LANGSMITH_PROJECT is required when LANGSMITH_TRACING=true")
        if self.langsmith_api_key is None or not self.langsmith_api_key.get_secret_value().strip():
            raise ValueError("LANGSMITH_API_KEY is required when LANGSMITH_TRACING=true")
        return self

    def configure_langsmith(self) -> None:
        """LangSmith 설정을 실행 기계에 비밀 문자열 노출을 최소화해 전달한다."""
        from .agents.runtime.telemetry.langsmith import configure_langsmith

        api_key = None if self.langsmith_api_key is None else self.langsmith_api_key.get_secret_value()
        configure_langsmith(
            enabled=self.langsmith_tracing,
            endpoint=self.langsmith_endpoint,
            project=self.langsmith_project,
            workspace_id=self.langsmith_workspace_id,
            api_key=api_key,
            disclose_trace_payloads=self.monitor_profile is MonitorProfile.LOCAL,
        )

    async def connect_temporal(self) -> Client:
        """설정한 Temporal에 붙는 연결 하나를 연다."""
        return await Client.connect(self.temporal_address, namespace=self.temporal_namespace)

    def agent_dsn(self) -> str:
        """앱 계정으로 실행 원장에 붙는 접속 문자열을 만든다."""
        credentials = f"{self.agent_db_user}:{self.agent_db_password}"
        return f"postgresql://{credentials}@{self.agent_db_host}:{self.agent_db_port}/{self.agent_db_name}"

    def checkpoint_dsn(self) -> str:
        """checkpoint 표만 놓인 스키마로 search_path를 고정한 접속 문자열을 만든다."""
        return f"{self.agent_dsn()}?options=-csearch_path%3D{CHECKPOINT_SCHEMA}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
