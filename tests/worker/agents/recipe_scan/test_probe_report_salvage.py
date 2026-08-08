"""상한을 넘겨 거절된 전문가 보고를 잘라 세우는 경로를 확인한다."""

from __future__ import annotations

from tracer_agent.shared.agents.recipe_scan.models import (
    MAX_EXCERPT_CHARS,
    MAX_EXCERPTS_PER_PROBE,
    MAX_VERDICT_CHARS,
    salvage_probe_report,
)


def _excerpt(text: str) -> dict[str, str]:
    return {"taskId": "task-1", "eventId": "event-1", "text": text}


def test_판정을_상한까지_잘라_세운다() -> None:
    report = salvage_probe_report(
        "timeline",
        {"verdict": "가" * (MAX_VERDICT_CHARS + 22), "excerpts": [_excerpt("근거")]},
    )

    assert report is not None
    assert len(report.verdict) == MAX_VERDICT_CHARS
    assert len(report.excerpts) == 1


def test_인용_본문과_인용_수를_각각_상한까지_줄인다() -> None:
    listed = [_excerpt("가" * (MAX_EXCERPT_CHARS + 10))] * (MAX_EXCERPTS_PER_PROBE + 3)

    report = salvage_probe_report("timeline", {"verdict": "판정", "excerpts": listed})

    assert report is not None
    assert len(report.excerpts) == MAX_EXCERPTS_PER_PROBE
    assert all(len(one.text) == MAX_EXCERPT_CHARS for one in report.excerpts)


def test_잘라_세운_보고를_예산_소진과_가른다() -> None:
    # 글자 수를 넘긴 것과 예산이 끊겨 못 본 것은 다른 사실이며 조율자의 판단도 갈린다.
    report = salvage_probe_report("rules", {"verdict": "판정", "exhausted": False})

    assert report is not None
    assert report.truncated is True
    assert report.exhausted is False


def test_전문가가_적은_소진은_잘라_세워도_그대로_남는다() -> None:
    report = salvage_probe_report("rules", {"verdict": "판정", "exhausted": True})

    assert report is not None
    assert report.exhausted is True
    assert report.truncated is True


def test_맡은_축을_보고가_잘못_적었어도_파견이_정한_축으로_세운다() -> None:
    report = salvage_probe_report("rules", {"probe": "timeline", "verdict": "판정"})

    assert report is not None
    assert report.probe == "rules"


def test_상한_외의_이유로_어긋난_보고는_세우지_못한다() -> None:
    assert salvage_probe_report("timeline", {"verdict": ""}) is None
    assert salvage_probe_report("timeline", {"verdict": "판정", "excerpts": [{"text": "근거"}]}) is None
    assert salvage_probe_report("timeline", None) is None
    assert salvage_probe_report("timeline", "판정") is None
