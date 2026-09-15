from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import unquote


REQUIRED_GUIDANCE_FILES = (
    "CONTEXTO.md",
    "README.md",
    "DESIGN.md",
    "docs/README.md",
    "docs/19-visao-geral-e-objetivos.md",
    "docs/20-arquitetura-e-fluxo-de-dados.md",
    "docs/21-catalogo-de-dados-e-metricas.md",
    "docs/22-guia-operacional.md",
    "docs/23-guia-de-desenvolvimento.md",
    "AGENTS.md",
    "src/tenable_reports/AGENTS.md",
    "tests/AGENTS.md",
    "clients/AGENTS.md",
    ".agents/skills/operating-tenable-reports/SKILL.md",
    ".agents/skills/operating-tenable-reports/references/runbook.md",
    ".agents/skills/validating-tenable-report-data/SKILL.md",
    ".agents/skills/validating-tenable-report-data/references/data-contract.md",
)

SKILL_FILES = (
    ".agents/skills/operating-tenable-reports/SKILL.md",
    ".agents/skills/validating-tenable-report-data/SKILL.md",
)

MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
SCAFFOLD_MARKER = re.compile(r"\b(?:TODO|TBD|FILL\s+IN)\b")
IGNORED_LINK_PREFIXES = ("#", "http://", "https://", "mailto:", "app://")
IGNORED_MARKDOWN_PARTS = frozenset(
    {".git", ".venv", ".tmp", "data", "credentials", "node_modules"}
)
FENCED_CODE_START = re.compile(r"^[ \t]*(?P<fence>`{3,}|~{3,})")


@dataclass(frozen=True, slots=True)
class GuidanceIssue:
    code: str
    path: str
    message: str


def _parse_frontmatter(text: str) -> dict[str, str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    try:
        closing_index = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration:
        return None

    values: dict[str, str] = {}
    for line in lines[1:closing_index]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            return None
        key, value = line.split(":", maxsplit=1)
        values[key.strip()] = value.strip().strip('"\'')
    return values


def _link_target(raw_target: str) -> str:
    target = raw_target.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    elif " " in target:
        target = target.split(" ", maxsplit=1)[0]
    return unquote(target.split("#", maxsplit=1)[0])


def _without_fenced_code_blocks(text: str) -> str:
    visible_lines: list[str] = []
    fence_character: str | None = None
    fence_length = 0

    for line in text.splitlines(keepends=True):
        if fence_character is None:
            match = FENCED_CODE_START.match(line)
            if match is None:
                visible_lines.append(line)
                continue
            fence = match.group("fence")
            fence_character = fence[0]
            fence_length = len(fence)
            continue

        stripped = line.lstrip()
        closing_length = len(stripped) - len(stripped.lstrip(fence_character))
        if closing_length >= fence_length and not stripped[closing_length:].strip():
            fence_character = None
            fence_length = 0

    return "".join(visible_lines)


def _without_inline_code_spans(text: str) -> str:
    visible_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        visible: list[str] = []
        cursor = 0
        while cursor < len(line):
            opening = line.find("`", cursor)
            if opening < 0:
                visible.append(line[cursor:])
                break

            visible.append(line[cursor:opening])
            opening_end = opening
            while opening_end < len(line) and line[opening_end] == "`":
                opening_end += 1
            delimiter = line[opening:opening_end]

            search_from = opening_end
            while True:
                closing = line.find(delimiter, search_from)
                if closing < 0:
                    visible.append(line[opening:])
                    cursor = len(line)
                    break
                closing_end = closing + len(delimiter)
                has_adjacent_backtick = (
                    (closing > 0 and line[closing - 1] == "`")
                    or (closing_end < len(line) and line[closing_end] == "`")
                )
                if has_adjacent_backtick:
                    search_from = closing + 1
                    continue
                visible.append(" ")
                cursor = closing_end
                break

        visible_lines.append("".join(visible))
    return "".join(visible_lines)


def _validate_local_links(root: Path, path: Path, text: str) -> list[GuidanceIssue]:
    issues: list[GuidanceIssue] = []
    visible_text = _without_fenced_code_blocks(text)
    visible_text = _without_inline_code_spans(visible_text)
    for match in MARKDOWN_LINK.finditer(visible_text):
        raw_target = match.group(1).strip()
        if raw_target.lower().startswith(IGNORED_LINK_PREFIXES):
            continue
        target = _link_target(raw_target)
        if not target:
            continue
        resolved = (path.parent / target).resolve()
        if not resolved.exists():
            issues.append(
                GuidanceIssue(
                    code="BROKEN_LOCAL_LINK",
                    path=path.relative_to(root).as_posix(),
                    message=f"Link local não encontrado: {target}",
                )
            )
    return issues


def _iter_markdown_files(root: Path) -> tuple[Path, ...]:
    files = (
        path
        for path in root.rglob("*.md")
        if path.is_file()
        and not IGNORED_MARKDOWN_PARTS.intersection(path.relative_to(root).parts)
    )
    return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))


def _validate_skill(root: Path, relative: str) -> list[GuidanceIssue]:
    path = root / relative
    if not path.exists():
        return []

    frontmatter = _parse_frontmatter(path.read_text(encoding="utf-8"))
    expected_name = path.parent.name
    valid = (
        frontmatter is not None
        and frontmatter.get("name") == expected_name
        and bool(frontmatter.get("description"))
        and frontmatter["description"].startswith("Use when")
    )
    if valid:
        return []
    return [
        GuidanceIssue(
            code="INVALID_SKILL_FRONTMATTER",
            path=relative,
            message=(
                "O SKILL.md deve declarar name igual à pasta e description iniciada "
                "por 'Use when'."
            ),
        )
    ]


def validate_guidance(root: Path) -> tuple[GuidanceIssue, ...]:
    root = root.resolve()
    issues: list[GuidanceIssue] = []

    for relative in REQUIRED_GUIDANCE_FILES:
        path = root / relative
        if not path.is_file():
            issues.append(
                GuidanceIssue(
                    code="MISSING_REQUIRED_FILE",
                    path=relative,
                    message="Artefato obrigatório não encontrado.",
                )
            )
            continue

        text = path.read_text(encoding="utf-8")
        if SCAFFOLD_MARKER.search(text):
            issues.append(
                GuidanceIssue(
                    code="SCAFFOLD_MARKER",
                    path=relative,
                    message="Marcador de rascunho encontrado.",
                )
            )

    for path in _iter_markdown_files(root):
        text = path.read_text(encoding="utf-8")
        issues.extend(_validate_local_links(root, path, text))

    for relative in SKILL_FILES:
        issues.extend(_validate_skill(root, relative))

    return tuple(issues)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Valida documentação, AGENTS.md e skills do projeto."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)

    issues = validate_guidance(args.root)
    if issues:
        for issue in issues:
            print(json.dumps(asdict(issue), ensure_ascii=False))
        return 1

    print(
        json.dumps(
            {"status": "ok", "files": len(REQUIRED_GUIDANCE_FILES)},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
