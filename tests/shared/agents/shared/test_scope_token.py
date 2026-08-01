"""실행에 매인 자격이 계약이 정한 모양과 수명을 그대로 따르는지 본다."""

from __future__ import annotations

import base64
import json

import pytest

from tracer_agent.shared.agents.shared import scope_token

_비밀 = "MONITOR_AUTH_TOKEN_SECRET"
_지금 = 1_000


@pytest.fixture(autouse=True)
def _서명_비밀(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_비밀, "secret")


def _자격() -> str:
    낸것 = scope_token.issue(user_id="local", execution_id="e1", ttl_ms=60_000, now_ms=_지금)
    assert 낸것 is not None
    return 낸것


class Test실행에_매인_자격:
    def test_계약이_정한_세_마디로_선다(self) -> None:
        마디 = _자격().split(".")

        assert len(마디) == 3
        assert 마디[0] == scope_token.prefix()

    def test_계약이_적은_네_칸을_싣는다(self) -> None:
        본문 = _자격().split(".")[1]
        실린것 = json.loads(base64.urlsafe_b64decode(본문 + "=" * (-len(본문) % 4)))

        assert set(실린것) == {"userId", "executionId", "issuedAt", "expiresAt"}
        assert 실린것["expiresAt"] == 실린것["issuedAt"] + 60_000

    def test_서명과_수명이_맞을_때만_범위를_낸다(self) -> None:
        자격 = _자격()

        assert scope_token.verify(자격, now_ms=_지금 + 1) == ("local", "e1")
        assert scope_token.verify(자격, now_ms=_지금 + 60_000) is None

    def test_서명이_다르면_범위를_내지_않는다(self) -> None:
        마디 = _자격().split(".")

        assert scope_token.verify(f"{마디[0]}.{마디[1]}.{'x' * len(마디[2])}", now_ms=_지금) is None

    def test_서명_비밀이_없으면_발급하지_않는다(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(_비밀, "   ")

        assert scope_token.issue(user_id="local", execution_id="e1", ttl_ms=1, now_ms=_지금) is None

    def test_모양이_맞으면_자기신고_헤더로_되돌리지_않는다(self) -> None:
        assert scope_token.looks_like(_자격()) is True
        assert scope_token.looks_like("Bearer something") is False
