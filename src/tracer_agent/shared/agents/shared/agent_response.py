"""에이전트 공통 실행 응답의 외부 wire 모델을 소유한다."""

from pydantic import BaseModel, Field

from .models import AgentErrorDTO, AgentRunObservationDTO, AgentStepDTO, UsageDTO


class AgentResponse(BaseModel):
    """세 에이전트 공통 응답이며 data는 성공 시 구조화 출력이고 실패 시 None이다."""

    data: dict[str, object] | None = None
    modelUsed: str
    durationMs: int
    numTurns: int | None = None
    usage: UsageDTO | None = None
    error: AgentErrorDTO | None = None
    steps: list[AgentStepDTO] = Field(
        default_factory=list,
        description="에이전트 실행의 모델 메시지와 도구 호출과 그래프 이벤트 궤적이다.",
    )
    landed: bool = Field(
        default=False,
        description="예산이 다해 조사 도구를 거두고 결론만 받는 마지막 호출로 넘어갔는지다.",
    )
    actualModel: str | None = Field(
        default=None,
        description="프로바이더가 실제 응답에 사용한 모델이다.",
    )
    providerRequestId: str | None = Field(
        default=None,
        description="프로바이더가 실제 응답에 부여한 요청 식별자다.",
    )
    observation: AgentRunObservationDTO | None = Field(
        default=None,
        description="서버가 그대로 저장할 수 있는 canonical terminal observation이다.",
    )
