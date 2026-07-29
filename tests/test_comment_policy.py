"""Python 주석과 docstring이 저장소 주석 규칙을 따르는지 검증한다."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1]
CHECKER = SERVICE_ROOT / "scripts" / "check_comments.py"


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
