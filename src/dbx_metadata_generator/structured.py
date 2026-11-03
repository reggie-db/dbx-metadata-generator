"""Normalize Databricks AI structured responses across endpoint families."""

from __future__ import annotations

import json
from typing import Any


def structured_dict(value: Any) -> dict[str, Any]:
    """Return a dictionary when an AI response is a Row, dict, or JSON string."""
    if hasattr(value, "asDict"):
        value = value.asDict(recursive=True)

    for _ in range(3):
        if isinstance(value, dict):
            return value
        if not isinstance(value, str):
            break
        text = value.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        value = json.loads(text)

    raise TypeError(f"Expected a structured dictionary response, got {type(value).__name__}")
