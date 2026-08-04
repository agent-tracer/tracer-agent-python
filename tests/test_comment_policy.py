"""Python 주석과 docstring이 저장소 주석 규칙을 따르는지 검증한다."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
CHECKER = SERVICE_ROOT / "scripts" / "check_comments.py"

sys.path.insert(0, str(SERVICE_ROOT / "scripts"))
import check_comments  # noqa: E402


def run_checker(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), str(path)],
        cwd=SERVICE_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("# This comment explains an implementation detail.\n", "한글"),
        ("# 결과를 계산한다 — 호출부가 저장한다.\n", "em-dash"),
        ("# 비용 계산은 워커가 소유한다(D-3).\n", "고아 참조"),
        ('"""실행 경계를 검증한다(§15.2)."""\n', "고아 참조"),
        ('"""요청을 처리한다.\nThis paragraph remains English prose.\n"""\n', "한글"),
        ('"""요청을 검증한다. 그리고 응답을 만든다."""\n', "한 문장"),
        ('"""요청을 검증한다.\n응답을 만든다.\n"""\n', "한 문장"),
        ("# 요청을 검증한다. 그리고 응답을 만든다.\nVALUE = 1\n", "한 줄 한 문장"),
        ("# 요청을 검증한다는 사실이 있고\n# 응답을 만든다는 사실도 있다\nVALUE = 1\n", "한 줄 한 문장"),
        ("# 그래프가 도는 동안 노드를 센다.\n", "도는 → 실행한다"),
        ("# 실행기가 무너졌을 때 사유를 남긴다.\n", "무너졌 → 실패한다"),
        ("# 두 구현체가 같은 바이트를 먹도록 적는다.\n", "먹도록 → 쓴다"),
        ("# 조율자가 근거를 직접 캐지 않는다.\n", "캐지 → 수집한다"),
        ('"""이 스레드만 조회하는다."""\n', "받침에 맞지 않는 어미"),
        ('"""공급자의 판정만 받은다."""\n', "받침에 맞지 않는 어미"),
        ('"""맡아 둔 알림를 켠다."""\n', "목적격 조사는 을이다"),
    ],
)
def test_규칙을_어긴_주석과_docstring을_거부한다(
    tmp_path: Path,
    source: str,
    expected: str,
) -> None:
    target = tmp_path / "bad.py"
    target.write_text(source, encoding="utf-8")

    result = run_checker(target)

    assert result.returncode == 1
    assert expected in result.stdout


def test_한글_계약_docstring과_외부_사실_주석을_허용한다(tmp_path: Path) -> None:
    target = tmp_path / "good.py"
    target.write_text(
        '"""외부 요청 본문의 검증 경계를 제공한다."""\n\n# 공급자 제한값의 단위는 바이트다.\nVALUE = 1\n',
        encoding="utf-8",
    )

    result = run_checker(target)

    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "source",
    [
        '"""갱신이 끊긴 실행을 대기 자리로 되돌린다."""\n',
        '"""연결 풀을 열고 부른 쪽에 돌려준다."""\n',
        '"""남의 스레드는 없는 것으로 돌려보낸다."""\n',
        '"""노드 이름과 실행을 한 객체에 모은다."""\n',
        '"""이 판정이 뒤집히면 산출 강제가 내려간다."""\n',
        '"""접은 문자열의 자리가 원문의 자리와 맞물린다."""\n',
        '"""핵심은 계약이 값을 소유한다는 사실이다."""\n',
        '"""부를 수 있는 창구를 이름으로 고른다."""\n',
    ],
)
def test_어미와_조사가_바른_문장은_통과시킨다(tmp_path: Path, source: str) -> None:
    target = tmp_path / "good.py"
    target.write_text(source, encoding="utf-8")

    result = run_checker(target)

    assert result.returncode == 0, result.stdout + result.stderr


def test_삼중_따옴표를_블록_주석으로_사용하면_거부한다(tmp_path: Path) -> None:
    target = tmp_path / "bad.py"
    target.write_text(
        'VALUE = 1\n\n"""이 문자열은 어떤 계약의 docstring도 아니다."""\n',
        encoding="utf-8",
    )

    result = run_checker(target)

    assert result.returncode == 1
    assert "블록 주석" in result.stdout


def test_없는_검사_경로를_거부한다(tmp_path: Path) -> None:
    result = run_checker(tmp_path / "missing.py")

    assert result.returncode == 1
    assert "경로가 없다" in result.stdout


def test_커밋_메시지_검사기와_같은_표를_쓴다() -> None:
    commit_checker = (SERVICE_ROOT / "scripts" / "check-commit-msg.mjs").read_text(encoding="utf-8")
    table = commit_checker.split("REGISTER_WORDS = [", 1)[1].split("\n];", 1)[0]
    commit_surfaces = re.findall(r"\[/(.+?)/, \"([^\"]+)\"\]", table)

    comment_surfaces = [(surface.pattern, plain) for surface, plain in check_comments.FIGURATIVE]

    assert comment_surfaces == commit_surfaces
