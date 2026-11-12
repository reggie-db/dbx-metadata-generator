"""Create and validate editable CSV review files for metadata proposals."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


TABLE_DESCRIPTION = "TABLE_DESCRIPTION"
COLUMN_DESCRIPTION = "COLUMN_DESCRIPTION"
TABLE_DESCRIPTION_PATH_SEGMENT = "[DESCRIPTION]"

CSV_FIELDS = (
    "metadata_path",
    "target_type",
    "catalog_name",
    "schema_name",
    "table_name",
    "column_name",
    "proposed_write",
    "confidence",
    "new_metadata",
    "current_metadata",
    "action",
    "rationale",
    "data_type",
    "nullable",
    "model_endpoint",
    "generated_at_utc",
)


def default_output_directory(current_user: str) -> Path:
    """Return the current user's Databricks Workspace home directory."""
    if not current_user.strip():
        raise ValueError("The current Databricks user cannot be empty")
    return Path("/Workspace/Users") / current_user.strip() / "dbx-metadata"


def metadata_path(
    catalog: str,
    schema: str,
    table: str,
    column: str | None = None,
) -> str:
    """Build the human-readable review target path."""
    target = column if column is not None else TABLE_DESCRIPTION_PATH_SEGMENT
    return ".".join((catalog, schema, table, target))


def proposed_write(
    change: Mapping[str, Any],
    *,
    minimum_confidence: float,
    evolve_existing_comments: bool,
) -> bool:
    """Return whether a generated proposal should initially be selected for writing."""
    action = str(change.get("action") or "keep").casefold()
    confidence = float(change.get("confidence") or 0.0)
    existing = change.get("existing_description")
    proposed = change.get("proposed_description")
    if action not in {"add", "evolve"} or confidence < minimum_confidence:
        return False
    if existing and action == "evolve" and not evolve_existing_comments:
        return False
    return proposed is not None and bool(str(proposed).strip())


def build_review_rows(
    table_contexts: Mapping[str, Mapping[str, Any]],
    proposed_changes: Mapping[str, Mapping[str, Any]],
    *,
    minimum_confidence: float,
    evolve_existing_comments: bool,
    generated_at: datetime | None = None,
) -> list[dict[str, Any]]:
    """Flatten table and column proposals into the editable CSV contract."""
    generated_at = generated_at or datetime.now(timezone.utc)
    generated_at_utc = generated_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    rows: list[dict[str, Any]] = []

    for fqn, proposed in proposed_changes.items():
        metadata = table_contexts[fqn]
        common = {
            "catalog_name": metadata["catalog"],
            "schema_name": metadata["schema"],
            "table_name": metadata["table"],
            "model_endpoint": proposed.get("model_endpoint") or "",
            "generated_at_utc": generated_at_utc,
        }

        table_change = proposed["table"]
        rows.append(
            _review_row(
                common,
                table_change,
                target_type=TABLE_DESCRIPTION,
                column_name="",
                data_type="",
                nullable="",
                minimum_confidence=minimum_confidence,
                evolve_existing_comments=evolve_existing_comments,
            )
        )

        column_changes = proposed["columns"]
        for column in metadata.get("columns", []):
            column_name = column["name"]
            change = column_changes.get(column_name)
            if change is None:
                continue
            rows.append(
                _review_row(
                    common,
                    change,
                    target_type=COLUMN_DESCRIPTION,
                    column_name=column_name,
                    data_type=column.get("data_type") or "",
                    nullable=column.get("nullable") or "",
                    minimum_confidence=minimum_confidence,
                    evolve_existing_comments=evolve_existing_comments,
                )
            )
    return rows


def _review_row(
    common: Mapping[str, Any],
    change: Mapping[str, Any],
    *,
    target_type: str,
    column_name: str,
    data_type: str,
    nullable: str,
    minimum_confidence: float,
    evolve_existing_comments: bool,
) -> dict[str, Any]:
    catalog = str(common["catalog_name"])
    schema = str(common["schema_name"])
    table = str(common["table_name"])
    return {
        "metadata_path": metadata_path(catalog, schema, table, column_name or None),
        "target_type": target_type,
        **common,
        "column_name": column_name,
        "proposed_write": str(
            proposed_write(
                change,
                minimum_confidence=minimum_confidence,
                evolve_existing_comments=evolve_existing_comments,
            )
        ).lower(),
        "confidence": float(change.get("confidence") or 0.0),
        "new_metadata": change.get("proposed_description") or "",
        "current_metadata": change.get("existing_description") or "",
        "action": change.get("action") or "",
        "rationale": change.get("rationale") or "",
        "data_type": data_type,
        "nullable": nullable,
    }


def write_review_csv(
    rows: Iterable[Mapping[str, Any]],
    output_directory: str | Path,
    *,
    generated_at: datetime | None = None,
) -> Path:
    """Write review rows to a timestamped CSV and return the resulting path."""
    generated_at = generated_at or datetime.now(timezone.utc)
    timestamp = generated_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = Path(output_directory).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    output_path = directory / f"metadata_proposals_{timestamp}.csv"
    suffix = 1
    while output_path.exists():
        output_path = directory / f"metadata_proposals_{timestamp}_{suffix:02d}.csv"
        suffix += 1
    with output_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return output_path


def read_review_csv(csv_path: str | Path) -> list[dict[str, str]]:
    """Read and validate an editable metadata review CSV."""
    path = Path(csv_path).expanduser()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = set(reader.fieldnames or [])
        missing = set(CSV_FIELDS) - fieldnames
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")
        rows = []
        for line_number, row in enumerate(reader, start=2):
            if None in row:
                raise ValueError(f"Row {line_number} has more values than the CSV header")
            if any((value or "").strip() for value in row.values()):
                rows.append(dict(row))
    validate_review_rows(rows)
    return rows


def parse_write_flag(value: str | None) -> bool:
    """Parse a review CSV write flag without treating arbitrary text as truthy."""
    normalized = (value or "").strip().casefold()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"", "false", "0", "no", "n"}:
        return False
    raise ValueError(f"Unsupported proposed_write value: {value!r}")


def validate_review_rows(rows: Sequence[Mapping[str, str]]) -> None:
    """Reject malformed or duplicate metadata targets before any DDL is issued."""
    seen: set[tuple[str, str, str, str, str]] = set()
    for number, row in enumerate(rows, start=2):
        target_type = (row.get("target_type") or "").strip().upper()
        catalog = row.get("catalog_name") or ""
        schema = row.get("schema_name") or ""
        table = row.get("table_name") or ""
        column = row.get("column_name") or ""
        if target_type not in {TABLE_DESCRIPTION, COLUMN_DESCRIPTION}:
            raise ValueError(f"Row {number} has unsupported target_type: {target_type!r}")
        if not all((catalog, schema, table)):
            raise ValueError(f"Row {number} must include catalog_name, schema_name, and table_name")
        if target_type == TABLE_DESCRIPTION and column:
            raise ValueError(f"Row {number} is a table description but column_name is not blank")
        if target_type == COLUMN_DESCRIPTION and not column:
            raise ValueError(f"Row {number} is a column description but column_name is blank")
        expected_path = metadata_path(catalog, schema, table, column or None)
        if row.get("metadata_path") != expected_path:
            raise ValueError(
                f"Row {number} metadata_path does not match its identifier fields: "
                f"expected {expected_path!r}"
            )
        parse_write_flag(row.get("proposed_write"))
        key = (target_type, catalog, schema, table, column)
        if key in seen:
            raise ValueError(f"Row {number} duplicates metadata target {row.get('metadata_path')!r}")
        seen.add(key)
