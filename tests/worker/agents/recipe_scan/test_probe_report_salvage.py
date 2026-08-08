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


def test_스스로_닫지_못한_조사이므로_소진으로_적는다() -> None:
    report = salvage_probe_report("rules", {"verdict": "판정", "exhausted": False})

    assert report is not None
    assert report.exhausted is True


def test_맡은_축을_보고가_잘못_적었어도_파견이_정한_축으로_세운다() -> None:
    report = salvage_probe_report("rules", {"probe": "timeline", "verdict": "판정"})

    assert report is not None
    assert report.probe == "rules"


def test_상한_외의_이유로_어긋난_보고는_세우지_못한다() -> None:
    assert salvage_probe_report("timeline", {"verdict": ""}) is None
    assert salvage_probe_report("timeline", {"verdict": "판정", "excerpts": [{"text": "근거"}]}) is None
    assert salvage_probe_report("timeline", None) is None
    assert salvage_probe_report("timeline", "판정") is None
