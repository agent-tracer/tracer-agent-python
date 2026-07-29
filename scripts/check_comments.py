"""Python 주석과 docstring의 저장소 형식 규칙을 검사한다."""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from collections.abc import Iterable, Iterator
from pathlib import Path

KOREAN = re.compile(r"[가-힣]")
ENGLISH_WORD = re.compile(r"[A-Za-z]{2,}")
DECISION_REFERENCE = re.compile(r"(?:\bADR-\d+\b|(?<![A-Za-z0-9])D-?\d+(?!\d)|§\d+)", re.IGNORECASE)
DIRECTIVE = re.compile(
    r"^\s*(?:noqa\b|type:\s*ignore\b|pyright:|ruff:|fmt:|pragma:|coding[:=]|mypy:|noinspection\b)",
    re.IGNORECASE,
)
DIVIDER = re.compile(r"^[\s\-=*─━#/.|+]+$")
URL = re.compile(r"^\s*https?://")
LICENSE = re.compile(r"^\s*(?:Copyright|SPDX-|License|@license)", re.IGNORECASE)
SENTENCE_BREAK = re.compile(r"[.?!]\s")


def python_files(paths: Iterable[Path]) -> Iterator[Path]:
    for path in paths:
        if path.is_dir():
            yield from sorted(candidate for candidate in path.rglob("*.py") if ".venv" not in candidate.parts)
        elif path.suffix == ".py":
            yield path


def docstrings(tree: ast.AST) -> Iterator[tuple[int, str]]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, owners) or not node.body:
            continue
        first = node.body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            yield first.lineno, first.value.value


def standalone_strings(tree: ast.AST) -> Iterator[int]:
    owners = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    docstring_nodes = {
        id(node.body[0])
        for node in ast.walk(tree)
        if isinstance(node, owners)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            and id(node) not in docstring_nodes
        ):
            yield node.lineno


def comments(source: str) -> Iterator[tuple[int, bool, str]]:
    lines = source.splitlines()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        if token.start[0] == 1 and token.string.startswith("#!"):
            continue
        line_number, column = token.start
        full_line = not lines[line_number - 1][:column].strip()
        yield line_number, full_line, token.string.removeprefix("#").strip()


def violation(text: str) -> str | None:
    lines = [line.strip().lstrip("*").strip() for line in text.splitlines()]
    normalized = "\n".join(lines).strip()
    if not normalized:
        return None
    if DECISION_REFERENCE.search(normalized):
        return "고아 참조나 결정 번호 대신 코드가 강제하는 사실을 직접 적는다"
    if "—" in normalized:
        return "em-dash 부연을 제거하고 결과 중심 문장으로 적는다"
    for line in lines:
        if (
            not line
            or DIRECTIVE.match(line)
            or DIVIDER.fullmatch(line)
            or URL.match(line)
            or LICENSE.match(line)
            or KOREAN.search(line)
        ):
            continue
        if len(ENGLISH_WORD.findall(line)) >= 4:
            return "주석과 docstring은 한글로 적는다"
    return None


def single_sentence(text: str) -> str | None:
    stripped = text.strip()
    if "\n" in stripped or SENTENCE_BREAK.search(stripped):
        return "docstring은 한글 한 문장으로 적는다"
    return None


def _skippable(text: str) -> bool:
    return bool(
        not text or DIRECTIVE.match(text) or DIVIDER.fullmatch(text) or URL.match(text) or LICENSE.match(text)
    )


def comment_block_findings(entries: list[tuple[int, bool, str]], path: Path) -> list[str]:
    findings: list[str] = []
    previous_full_line = 0
    for line, full_line, text in sorted(entries):
        if not full_line or _skippable(text):
            continue
        if SENTENCE_BREAK.search(text) or line == previous_full_line + 1:
            findings.append(f"{path}:{line}: 줄 주석은 한 줄 한 문장으로 적는다")
        previous_full_line = line
    return findings


def check_file(path: Path) -> list[str]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        comment_entries = [*comments(source)]
        docstring_entries = [*docstrings(tree)]
        string_lines = [*standalone_strings(tree)]
    except (OSError, SyntaxError, tokenize.TokenError) as error:
        return [f"{path}: 검사할 수 없다: {error}"]

    findings = [f"{path}:{line}: 문자열 리터럴을 블록 주석으로 사용하지 않는다" for line in string_lines]
    for line, _full_line, text in sorted(comment_entries):
        message = violation(text)
        if message is not None:
            findings.append(f"{path}:{line}: {message}")
    findings.extend(comment_block_findings(comment_entries, path))
    for line, text in sorted(docstring_entries):
        message = violation(text) or single_sentence(text)
        if message is not None:
            findings.append(f"{path}:{line}: {message}")
    return findings


def main(argv: list[str]) -> int:
    targets = [Path(value) for value in argv] if argv else [Path("src"), Path("tests"), Path("scripts")]
    findings = [f"{path}: 경로가 없다" for path in targets if not path.exists()]
    findings.extend(finding for path in python_files(targets) for finding in check_file(path))
    if findings:
        sys.stdout.write("\n".join(findings) + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
