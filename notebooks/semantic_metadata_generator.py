# Databricks notebook source
import json
import os
import sys
from pathlib import Path

for candidate in ("../src", "./src"):
    resolved = os.path.abspath(candidate)
    if resolved not in sys.path:
        sys.path.insert(0, resolved)

from dbx_metadata_generator.selectors import RelationFilter, parse_selector_list
from dbx_metadata_generator.structured import structured_dict


DEFAULTS = {
    "include": "[]",
    "exclude": '["system.*.*", "__databricks_internal.*.*"]',
    "model_endpoint": "databricks-qwen35-122b-a10b",
    "model_fallbacks": '["databricks-gpt-oss-120b", "databricks-qwen3-next-80b-a3b-instruct"]',
    "dry_run": "true",
    "include_views": "false",
    "max_tables": "25",
    "sample_rows": "8",
    "max_sample_columns": "40",
    "max_cell_chars": "240",
    "column_batch_size": "40",
    "min_apply_confidence": "0.85",
    "evolve_existing_comments": "true",
}


def _widget(name: str, default: str, label: str) -> str:
    try:
        dbutils.widgets.get(name)
    except Exception:
        dbutils.widgets.text(name, default, label)
    return dbutils.widgets.get(name).strip()


def _as_bool(value: str) -> bool:
    return value.strip().casefold() in {"true", "1", "yes", "y"}


INCLUDE = parse_selector_list(_widget("include", DEFAULTS["include"], "Include selectors (JSON or comma-separated)"))
EXCLUDE = parse_selector_list(_widget("exclude", DEFAULTS["exclude"], "Exclude selectors (JSON or comma-separated)"))
MODEL_ENDPOINT = _widget("model_endpoint", DEFAULTS["model_endpoint"], "Analysis model endpoint")
MODEL_FALLBACKS = parse_selector_list(_widget("model_fallbacks", DEFAULTS["model_fallbacks"], "Fallback model endpoints"))
DRY_RUN = _as_bool(_widget("dry_run", DEFAULTS["dry_run"], "Dry run"))
INCLUDE_VIEWS = _as_bool(_widget("include_views", DEFAULTS["include_views"], "Include views"))
MAX_TABLES = int(_widget("max_tables", DEFAULTS["max_tables"], "Maximum relations to scan (0 = unlimited)"))
SAMPLE_ROWS = int(_widget("sample_rows", DEFAULTS["sample_rows"], "Random sample rows per table"))
MAX_SAMPLE_COLUMNS = int(_widget("max_sample_columns", DEFAULTS["max_sample_columns"], "Maximum columns sent in samples"))
MAX_CELL_CHARS = int(_widget("max_cell_chars", DEFAULTS["max_cell_chars"], "Maximum characters per sampled cell"))
COLUMN_BATCH_SIZE = int(_widget("column_batch_size", DEFAULTS["column_batch_size"], "Columns per proposal call"))
MIN_APPLY_CONFIDENCE = float(_widget("min_apply_confidence", DEFAULTS["min_apply_confidence"], "Minimum confidence to apply"))
EVOLVE_EXISTING_COMMENTS = _as_bool(_widget("evolve_existing_comments", DEFAULTS["evolve_existing_comments"], "Allow evolving existing comments"))

RELATION_FILTER = RelationFilter.compile(INCLUDE, EXCLUDE)
MODEL_CANDIDATES = list(dict.fromkeys([MODEL_ENDPOINT, *MODEL_FALLBACKS]))

print(json.dumps({
    "filter": RELATION_FILTER.explain(),
    "model_candidates": MODEL_CANDIDATES,
    "dry_run": DRY_RUN,
    "include_views": INCLUDE_VIEWS,
    "max_tables": MAX_TABLES,
    "sample_rows": SAMPLE_ROWS,
    "min_apply_confidence": MIN_APPLY_CONFIDENCE,
}, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC # Semantic Metadata Generator
# MAGIC
# MAGIC This notebook discovers accessible Unity Catalog tables, briefly samples each selected table,
# MAGIC asks a configurable open-weight model to summarize the data without reproducing raw records,
# MAGIC and builds confidence-scored table and column comment proposals.
# MAGIC
# MAGIC Raw samples are held only in local Python memory for the model call. They are not written to
# MAGIC tables, files, logs, outputs, `TABLE_CONTEXTS`, or `PROPOSED_CHANGES`.

# COMMAND ----------

from datetime import date, datetime
from decimal import Decimal
from typing import Any


TABLE_RESPONSE_FORMAT = (
    "STRUCT<result: STRUCT<description: STRING, action: STRING, confidence: DOUBLE, rationale: STRING>>"
)
COLUMN_RESPONSE_FORMAT = (
    "STRUCT<result: STRUCT<columns: ARRAY<STRUCT<name: STRING, description: STRING, "
    "action: STRING, confidence: DOUBLE, rationale: STRING>>>>"
)
SUMMARY_RESPONSE_FORMAT = (
    "STRUCT<result: STRUCT<summary: STRING, likely_grain: STRING, "
    "notable_patterns: ARRAY<STRING>, data_quality_notes: ARRAY<STRING>, sensitive_data_risk: STRING>>"
)


def quote_identifier(value: str) -> str:
    return f"`{value.replace('`', '``')}`"


def fully_qualified(catalog: str, schema: str, table: str) -> str:
    return ".".join(quote_identifier(part) for part in (catalog, schema, table))


def sql_string(value: str) -> str:
    return value.replace("'", "''")


def normalize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<binary:{len(value)} bytes>"
    if isinstance(value, dict):
        return {str(key): normalize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_value(item) for item in value]
    rendered = str(value)
    return rendered if len(rendered) <= MAX_CELL_CHARS else rendered[:MAX_CELL_CHARS] + "…"


ACTIVE_MODEL = None


def query_model(prompt: str, response_format: str) -> dict[str, Any]:
    global ACTIVE_MODEL
    candidates = [ACTIVE_MODEL] if ACTIVE_MODEL else []
    candidates.extend(model for model in MODEL_CANDIDATES if model not in candidates)
    failures = []
    for endpoint in candidates:
        try:
            row = spark.sql(
                "SELECT ai_query(:endpoint, :prompt, responseFormat => :response_format, "
                "failOnError => false) AS response",
                args={"endpoint": endpoint, "prompt": prompt, "response_format": response_format},
            ).first()
            response = row["response"] if row else None
            if response is None:
                raise RuntimeError("ai_query returned no response")
            response = structured_dict(response)
            result = response.get("result")
            if result is None:
                raise RuntimeError(f"ai_query returned an invalid structured response: {response!r}")
            ACTIVE_MODEL = endpoint
            return structured_dict(result)
        except Exception as exc:
            failures.append(f"{endpoint}: {type(exc).__name__}: {str(exc)[:300]}")
    raise RuntimeError("All model endpoints failed: " + " | ".join(failures))


def get_optional_tags(catalog: str, schema: str, table: str) -> dict[str, Any]:
    tags = {"table": {}, "columns": {}}
    try:
        table_rows = spark.sql(
            """
            SELECT tag_name, tag_value
            FROM system.information_schema.table_tags
            WHERE catalog_name = :catalog AND schema_name = :schema AND table_name = :table
            """,
            args={"catalog": catalog, "schema": schema, "table": table},
        ).collect()
        tags["table"] = {row["tag_name"]: row["tag_value"] for row in table_rows}
    except Exception:
        pass
    try:
        column_rows = spark.sql(
            """
            SELECT column_name, tag_name, tag_value
            FROM system.information_schema.column_tags
            WHERE catalog_name = :catalog AND schema_name = :schema AND table_name = :table
            """,
            args={"catalog": catalog, "schema": schema, "table": table},
        ).collect()
        for row in column_rows:
            tags["columns"].setdefault(row["column_name"], {})[row["tag_name"]] = row["tag_value"]
    except Exception:
        pass
    return tags


def inspect_relation(candidate: dict[str, Any]) -> dict[str, Any]:
    catalog = candidate["catalog"]
    schema = candidate["schema"]
    table = candidate["table"]
    column_rows = spark.sql(
        """
        SELECT column_name, ordinal_position, data_type, full_data_type, is_nullable, comment
        FROM system.information_schema.columns
        WHERE table_catalog = :catalog AND table_schema = :schema AND table_name = :table
        ORDER BY ordinal_position
        """,
        args={"catalog": catalog, "schema": schema, "table": table},
    ).collect()
    tags = get_optional_tags(catalog, schema, table)
    return {
        "catalog": catalog,
        "schema": schema,
        "table": table,
        "fqn": f"{catalog}.{schema}.{table}",
        "table_type": candidate["table_type"],
        "owner": candidate.get("owner"),
        "existing_description": candidate.get("description"),
        "tags": tags["table"],
        "columns": [
            {
                "name": row["column_name"],
                "position": row["ordinal_position"],
                "data_type": row["full_data_type"] or row["data_type"],
                "nullable": row["is_nullable"],
                "existing_description": row["comment"],
                "tags": tags["columns"].get(row["column_name"], {}),
            }
            for row in column_rows
        ],
    }


def random_sample(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    if SAMPLE_ROWS <= 0:
        return []
    identifier = fully_qualified(metadata["catalog"], metadata["schema"], metadata["table"])
    selected_columns = [column["name"] for column in metadata["columns"][:MAX_SAMPLE_COLUMNS]]
    if not selected_columns:
        return []
    projection = ", ".join(quote_identifier(column) for column in selected_columns)
    try:
        sample_df = spark.sql(f"SELECT {projection} FROM {identifier} TABLESAMPLE ({SAMPLE_ROWS} ROWS)")
    except Exception:
        sample_df = spark.table(identifier).select(*selected_columns).sample(
            withReplacement=False,
            fraction=0.01,
            seed=42,
        ).limit(SAMPLE_ROWS)
    return [
        {key: normalize_value(value) for key, value in row.asDict(recursive=True).items()}
        for row in sample_df.limit(SAMPLE_ROWS).collect()
    ]


def summarize_sample(metadata: dict[str, Any], sample_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not sample_rows:
        return {
            "summary": "No sample rows were available; proposals use schema and existing metadata only.",
            "likely_grain": "Unknown",
            "notable_patterns": [],
            "data_quality_notes": [],
            "sensitive_data_risk": "Unknown",
        }
    prompt = f"""
You are analyzing a small random sample from a Unity Catalog table to improve business metadata.
Summarize the apparent business meaning, likely row grain, notable patterns, and data-quality risks.
Identify possible sensitive-data risk at a high level. Do not reproduce complete rows, unique identifiers,
personal data, secrets, or long literal values. Generalize observations rather than quoting sample values.

Table metadata:
{json.dumps({key: metadata[key] for key in ("fqn", "table_type", "existing_description", "columns")}, default=str)}

Ephemeral random sample ({len(sample_rows)} rows; never persist or repeat verbatim):
{json.dumps(sample_rows, default=str)}
""".strip()
    return query_model(prompt, SUMMARY_RESPONSE_FORMAT)


def related_context(metadata: dict[str, Any], contexts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    related = []
    for context in contexts.values():
        if context["fqn"] == metadata["fqn"] or context["schema"] != metadata["schema"]:
            continue
        related.append({
            "fqn": context["fqn"],
            "existing_description": context.get("existing_description"),
            "sample_summary": context.get("sample_summary", {}).get("summary"),
        })
        if len(related) >= 12:
            break
    return related


def propose_table_description(metadata: dict[str, Any], contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    prompt = f"""
You write concise, factual Unity Catalog table descriptions for analysts and Databricks Genie.
Use the schema, existing metadata, and data summary below. Preserve accurate existing context and evolve it
only when the new description is materially clearer. Never invent business facts. Choose action from keep,
add, or evolve. Confidence must be between 0 and 1. A keep action should return the existing description.

Current table context:
{json.dumps(metadata, default=str)}

Related tables in the same scanned schema:
{json.dumps(related_context(metadata, contexts), default=str)}
""".strip()
    return query_model(prompt, TABLE_RESPONSE_FORMAT)


def propose_column_descriptions(metadata: dict[str, Any], table_proposal: dict[str, Any]) -> list[dict[str, Any]]:
    proposals = []
    columns = metadata["columns"]
    for offset in range(0, len(columns), COLUMN_BATCH_SIZE):
        batch = columns[offset : offset + COLUMN_BATCH_SIZE]
        prompt = f"""
You write concise, factual Unity Catalog column descriptions for analysts and Databricks Genie.
For every supplied column, return its exact name, a proposed description, action (keep, add, or evolve),
confidence from 0 to 1, and a brief rationale. Preserve accurate existing comments. Do not infer unsupported
codes or business rules from names alone. Use the summarized sample context but never recreate raw records.

Table: {metadata['fqn']}
Table proposal: {json.dumps(table_proposal, default=str)}
Sample summary: {json.dumps(metadata['sample_summary'], default=str)}
Columns:
{json.dumps(batch, default=str)}
""".strip()
        result = query_model(prompt, COLUMN_RESPONSE_FORMAT)
        proposals.extend(result.get("columns") or [])
    return proposals

# COMMAND ----------

relation_types = ["MANAGED", "EXTERNAL", "FOREIGN"]
if INCLUDE_VIEWS:
    relation_types.extend(["VIEW", "MATERIALIZED_VIEW", "STREAMING_TABLE"])

candidate_rows = spark.sql(
    """
    SELECT
      table_catalog AS catalog,
      table_schema AS schema,
      table_name AS table,
      table_type,
      table_owner AS owner,
      comment AS description
    FROM system.information_schema.tables
    WHERE table_schema <> 'information_schema'
    ORDER BY table_catalog, table_schema, table_name
    """
).collect()

selected_relations = []
for row in candidate_rows:
    candidate = row.asDict(recursive=True)
    if candidate["table_type"] not in relation_types:
        continue
    if RELATION_FILTER.matches(candidate["catalog"], candidate["schema"], candidate["table"]):
        selected_relations.append(candidate)
    if MAX_TABLES > 0 and len(selected_relations) >= MAX_TABLES:
        break

print(f"Selected {len(selected_relations)} relations from {len(candidate_rows)} visible relations.")

# COMMAND ----------

TABLE_CONTEXTS: dict[str, dict[str, Any]] = {}
SCAN_ERRORS: list[dict[str, str]] = []

for index, candidate in enumerate(selected_relations, start=1):
    fqn = f"{candidate['catalog']}.{candidate['schema']}.{candidate['table']}"
    print(f"[{index}/{len(selected_relations)}] Inspecting {fqn}")
    try:
        metadata = inspect_relation(candidate)
        sample_rows = random_sample(metadata)
        try:
            sample_summary = summarize_sample(metadata, sample_rows)
        finally:
            sample_rows.clear()
            del sample_rows
        metadata["sample_summary"] = sample_summary
        TABLE_CONTEXTS[fqn] = metadata
    except Exception as exc:
        SCAN_ERRORS.append({"fqn": fqn, "error": f"{type(exc).__name__}: {str(exc)[:1000]}"})

print(f"Built summarized context for {len(TABLE_CONTEXTS)} relations; errors={len(SCAN_ERRORS)}")

# COMMAND ----------

PROPOSED_CHANGES: dict[str, dict[str, Any]] = {}

for index, (fqn, metadata) in enumerate(TABLE_CONTEXTS.items(), start=1):
    print(f"[{index}/{len(TABLE_CONTEXTS)}] Proposing metadata for {fqn}")
    table_proposal = propose_table_description(metadata, TABLE_CONTEXTS)
    column_proposals = propose_column_descriptions(metadata, table_proposal)
    existing_columns = {column["name"]: column for column in metadata["columns"]}
    PROPOSED_CHANGES[fqn] = {
        "table": {
            "existing_description": metadata.get("existing_description"),
            "proposed_description": table_proposal.get("description"),
            "action": table_proposal.get("action"),
            "confidence": float(table_proposal.get("confidence") or 0.0),
            "rationale": table_proposal.get("rationale"),
        },
        "columns": {
            proposal["name"]: {
                "existing_description": existing_columns.get(proposal["name"], {}).get("existing_description"),
                "proposed_description": proposal.get("description"),
                "action": proposal.get("action"),
                "confidence": float(proposal.get("confidence") or 0.0),
                "rationale": proposal.get("rationale"),
            }
            for proposal in column_proposals
            if proposal.get("name") in existing_columns
        },
        "model_endpoint": ACTIVE_MODEL,
    }

# COMMAND ----------

APPLIED_CHANGES: list[dict[str, str]] = []
APPLY_ERRORS: list[dict[str, str]] = []


def should_apply(change: dict[str, Any]) -> bool:
    action = str(change.get("action") or "keep").casefold()
    confidence = float(change.get("confidence") or 0.0)
    existing = change.get("existing_description")
    if action not in {"add", "evolve"} or confidence < MIN_APPLY_CONFIDENCE:
        return False
    if existing and action == "evolve" and not EVOLVE_EXISTING_COMMENTS:
        return False
    return bool(change.get("proposed_description"))


if not DRY_RUN:
    for fqn, proposed in PROPOSED_CHANGES.items():
        metadata = TABLE_CONTEXTS[fqn]
        identifier = fully_qualified(metadata["catalog"], metadata["schema"], metadata["table"])
        table_change = proposed["table"]
        if should_apply(table_change):
            ddl = f"COMMENT ON TABLE {identifier} IS '{sql_string(table_change['proposed_description'])}'"
            try:
                spark.sql(ddl)
                APPLIED_CHANGES.append({"fqn": fqn, "target": "table"})
            except Exception as exc:
                APPLY_ERRORS.append({"fqn": fqn, "target": "table", "error": str(exc)[:1000]})

        for column_name, column_change in proposed["columns"].items():
            if not should_apply(column_change):
                continue
            ddl = (
                f"ALTER TABLE {identifier} ALTER COLUMN {quote_identifier(column_name)} "
                f"COMMENT '{sql_string(column_change['proposed_description'])}'"
            )
            try:
                spark.sql(ddl)
                APPLIED_CHANGES.append({"fqn": fqn, "target": column_name})
            except Exception as exc:
                APPLY_ERRORS.append({"fqn": fqn, "target": column_name, "error": str(exc)[:1000]})

# COMMAND ----------

proposal_rows = []
for fqn, proposed in PROPOSED_CHANGES.items():
    proposal_rows.append({"table": fqn, "target": "<table>", **proposed["table"]})
    for column_name, change in proposed["columns"].items():
        proposal_rows.append({"table": fqn, "target": column_name, **change})

if proposal_rows:
    display(spark.createDataFrame(proposal_rows).orderBy("table", "target"))

RUN_SUMMARY = {
    "dry_run": DRY_RUN,
    "active_model": ACTIVE_MODEL,
    "selected_relations": len(selected_relations),
    "summarized_contexts": len(TABLE_CONTEXTS),
    "proposed_relations": len(PROPOSED_CHANGES),
    "proposal_count": len(proposal_rows),
    "applied_count": len(APPLIED_CHANGES),
    "scan_error_count": len(SCAN_ERRORS),
    "apply_error_count": len(APPLY_ERRORS),
    "scanned_tables": list(TABLE_CONTEXTS),
}
print(json.dumps(RUN_SUMMARY, indent=2))
dbutils.notebook.exit(json.dumps(RUN_SUMMARY))
