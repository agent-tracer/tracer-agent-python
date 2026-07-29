"""계약이 소유한 프롬프트 조각의 자리표시자와 렌더러다."""

from __future__ import annotations

from string import Template

# 배포 이미지에 계약 저장소가 없어 런타임 로더를 둘 수 없으므로, 값을 손으로 들고 계약 테스트가 대조한다.
PLACEHOLDERS: dict[str, str] = {
    "recipeCandidateLimit": "4",
    "maxRedispatchProbes": "3",
    "maxRedispatchRounds": "1",
    "maxProbeWeight": "10",
    "maxInspectWeight": "4",
    "maxEvidenceEventIds": "100",
    "checkCitationsTool": "check_citations",
}


def render(fragment: str) -> str:
    """조각 하나의 자리표시자를 채우며 표에 없는 이름은 던진다."""
    return Template(fragment).substitute(PLACEHOLDERS)
