"""Compile catalog, schema, table, and glob selectors into one filter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Iterable, Sequence


def parse_selector_list(value: str | Sequence[str] | None) -> list[str]:
    """Parse a JSON array or comma/newline-separated selector list."""
    if value is None:
        return []
    if not isinstance(value, str):
        return [str(item).strip() for item in value if str(item).strip()]

    text = value.strip()
    if not text:
        return []
    if text.startswith("["):
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("Selector JSON must be an array")
        return [str(item).strip() for item in parsed if str(item).strip()]

    normalized = text.replace("\n", ",")
    return [item.strip() for item in normalized.split(",") if item.strip()]


def _split_qualified_name(value: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    in_backticks = False
    index = 0

    while index < len(value):
        char = value[index]
        if char == "`":
            if in_backticks and index + 1 < len(value) and value[index + 1] == "`":
                current.append("`")
                index += 2
                continue
            in_backticks = not in_backticks
        elif char == "." and not in_backticks:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1

    if in_backticks:
        raise ValueError(f"Unclosed backtick in selector: {value!r}")
    parts.append("".join(current).strip())
    if any(not part for part in parts):
        raise ValueError(f"Invalid empty selector component: {value!r}")
    return parts


def _compile_selector(selector: str) -> tuple[str, str, str]:
    value = selector.strip().replace("%", "*")
    if not value:
        raise ValueError("Selectors cannot be empty")

    parts = _split_qualified_name(value)
    if len(parts) == 1:
        parts.extend(["*", "*"])
    elif len(parts) == 2:
        parts.append("*")
    elif len(parts) != 3:
        raise ValueError(
            "Selectors must be catalog, catalog.schema, or catalog.schema.table patterns: "
            f"{selector!r}"
        )
    return tuple(part.casefold() for part in parts)  # type: ignore[return-value]


def _matches(parts: tuple[str, str, str], pattern: tuple[str, str, str]) -> bool:
    return all(fnmatchcase(value.casefold(), expected) for value, expected in zip(parts, pattern))


@dataclass(frozen=True)
class RelationFilter:
    """Include/exclude filter where exclusions always take precedence."""

    include_patterns: tuple[tuple[str, str, str], ...]
    exclude_patterns: tuple[tuple[str, str, str], ...]

    @classmethod
    def compile(
        cls,
        include: Iterable[str] | None = None,
        exclude: Iterable[str] | None = None,
    ) -> "RelationFilter":
        include_patterns = tuple(_compile_selector(item) for item in (include or []))
        exclude_patterns = tuple(_compile_selector(item) for item in (exclude or []))
        return cls(include_patterns=include_patterns, exclude_patterns=exclude_patterns)

    def matches(self, catalog: str, schema: str, table: str) -> bool:
        relation = (catalog, schema, table)
        included = not self.include_patterns or any(
            _matches(relation, pattern) for pattern in self.include_patterns
        )
        excluded = any(_matches(relation, pattern) for pattern in self.exclude_patterns)
        return included and not excluded

    def explain(self) -> dict[str, list[str]]:
        def render(pattern: tuple[str, str, str]) -> str:
            return ".".join(pattern)

        return {
            "include": [render(pattern) for pattern in self.include_patterns] or ["*.*.*"],
            "exclude": [render(pattern) for pattern in self.exclude_patterns],
        }
