"""서비스 설정."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from temporalio.client import Client


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    # TS 배포 단위와 같은 어휘를 쓰며 local만 LangSmith에 프롬프트·응답 원문을 보낸다.
    monitor_profile: Literal["local", "prd"] = "prd"

    tracer_agent_host: str = "0.0.0.0"
    tracer_agent_port: int = 8800

    tracer_db_host: str = "tracer-db"
    tracer_db_port: int = 5432
    tracer_db_name: str = "tracer"
    # 계약인 뷰에만 SELECT가 열린 역할이라 기반 테이블과 쓰기는 데이터베이스가 거부한다.
    agent_db_reader_user: str = "agent_reader"
    agent_db_reader_password: str = "agentreader"
    agent_db_checkpoint_user: str = "agent_checkpoint_writer"
    agent_db_checkpoint_password: str = "agentcheckpoint"
    # chat과 잡 셋 실행 행과 그 산출물에만 쓰기가 열린 역할이라 다른 도메인 테이블은 데이터베이스가 거부한다.
    agent_db_execution_user: str = "agent_execution_writer"
    agent_db_execution_password: str = "agentexecution"

    opensearch_node: str = "http://opensearch:9200"
    kafka_brokers: str = "redpanda:29092"
    temporal_address: str = "temporal:7233"
    temporal_namespace: str = "default"
    # 단가표도 한도도 자격도 이 서비스가 갖지 않으므로 실행 봉투를 이 주소에서 받아 온다.
    tracer_api_url: str = "http://tracer-api:3902"

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
            disclose_trace_payloads=self.monitor_profile == "local",
        )

    async def connect_temporal(self) -> Client:
        """설정한 Temporal에 붙는 연결 하나를 연다."""
        return await Client.connect(self.temporal_address, namespace=self.temporal_namespace)

    def tracer_dsn(self) -> str:
        """읽기 전용 역할로 원장 뷰에 붙는 접속 문자열을 만든다."""
        credentials = f"{self.agent_db_reader_user}:{self.agent_db_reader_password}"
        return f"postgresql://{credentials}@{self.tracer_db_host}:{self.tracer_db_port}/{self.tracer_db_name}"

    def checkpoint_dsn(self) -> str:
        """에이전트 실행 checkpoint 테이블에만 쓰는 접속 문자열을 만든다."""
        credentials = f"{self.agent_db_checkpoint_user}:{self.agent_db_checkpoint_password}"
        return f"postgresql://{credentials}@{self.tracer_db_host}:{self.tracer_db_port}/{self.tracer_db_name}"

    def execution_dsn(self) -> str:
        """chat과 잡 셋의 자기 실행 원장에만 쓰는 접속 문자열을 만든다."""
        credentials = f"{self.agent_db_execution_user}:{self.agent_db_execution_password}"
        return f"postgresql://{credentials}@{self.tracer_db_host}:{self.tracer_db_port}/{self.tracer_db_name}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
