# dbx-metadata-generator

Generate confidence-scored Unity Catalog table and column comment proposals from schema metadata and ephemeral random samples.

The Databricks notebook:

- accepts include and exclude selectors for catalogs, schemas, tables, and glob patterns;
- discovers accessible Unity Catalog relations through `system.information_schema`;
- captures existing descriptions, column metadata, owners, and tags;
- samples a small number of rows at random without persisting or printing them;
- asks an open-weight Databricks model to summarize each sample without reproducing raw records;
- emits timestamped JSON progress logs for discovery, scanning, model calls, and CSV export;
- builds `TABLE_CONTEXTS` and `PROPOSED_CHANGES` dictionaries;
- writes a timestamped, editable CSV of table and column comment proposals.

The separate `apply_metadata_csv` notebook validates an edited proposal CSV, checks for metadata
that changed after export, and applies only rows where `proposed_write` is true. This keeps generation
and mutation in separate runs so the CSV can be downloaded, edited, and uploaded before approval.

The default analysis endpoint is `databricks-qwen35-122b-a10b`, with configurable fallbacks. Change it in the notebook settings or job parameters when the endpoint is unavailable in a workspace.

## Manual Databricks upload

This is the recommended setup when Databricks CLI access is not available.

1. [Download the repository as a ZIP file](https://github.com/reggie-db/dbx-metadata-generator/archive/refs/heads/main.zip).
2. In Databricks, open **Workspace** and navigate to the folder where the notebook should be installed.
3. Select **Import**, choose the downloaded ZIP file, and import it as an archive.
4. Open `notebooks/semantic_metadata_generator` from the imported folder.
5. Connect the notebook to serverless compute.
6. In the first cell, set `include` to a narrow catalog, schema, table, or pattern. Optionally set `output_directory` to a Workspace or Unity Catalog Volume path.
7. Run all cells and follow the timestamped progress messages in the notebook output.
8. Download the generated CSV, edit `proposed_write` and `new_metadata`, then upload it to a Workspace or Volume path.
9. Open `notebooks/apply_metadata_csv`, set `csv_path` to the uploaded file, and run all cells.

Import the repository ZIP rather than only the `.ipynb` file. The notebook loads helper modules from the included `src` directory.

The user running the generator needs access to the selected Unity Catalog tables and the configured model endpoint. The user running the apply notebook also needs permission to modify the selected tables' metadata.

## CSV review format

Each row represents one description target. `metadata_path` is readable in spreadsheet tools:

- `catalog.schema.table.[DESCRIPTION]` represents the table description;
- `catalog.schema.table.column` represents a column description.

The separate `catalog_name`, `schema_name`, `table_name`, and `column_name` fields are authoritative,
so names containing dots do not depend on parsing `metadata_path`.

Edit these fields during review:

- `proposed_write`: set to `true` to apply the row or `false` to skip it;
- `new_metadata`: the description to write; leave it blank with `proposed_write=true` to clear the description.

The CSV also includes `confidence`, `current_metadata`, model action and rationale, column data type and
nullability, model endpoint, and generation timestamp. By default, the apply notebook
stops before making any changes if live metadata no longer matches `current_metadata`. Set
`allow_stale_metadata=true` only when that overwrite is intentional.

## Selector syntax

Selectors accept `*`, `?`, character groups, and SQL-style `%` wildcards.

| Selector | Matches |
| --- | --- |
| `main` | Every table in catalog `main` |
| `main.sales` | Every table in `main.sales` |
| `main.sales.orders` | One exact table |
| `prod*.sales?.order%` | A three-part pattern |

An empty include list means all visible relations. Exclusions always win. The notebook excludes `system.*.*` and `__databricks_internal.*.*` by default.
It also excludes relation names beginning with `__` or `event_log_`, which are commonly hidden pipeline implementation objects rather than user-facing tables.

## Data handling

Raw sample rows exist only in notebook-process memory while a summary request is made. They are not written to tables, volumes, files, logs, notebook output, `TABLE_CONTEXTS`, or `PROPOSED_CHANGES`. The model prompt explicitly instructs the model not to reproduce unique identifiers, personal data, secrets, or complete rows.

The model still receives sampled values. Review workspace governance requirements before scanning sensitive tables, and use include/exclude selectors to constrain scope.

## Automated deployment

Use an explicitly configured Databricks CLI profile:

```bash
databricks bundle validate --strict --target dev --profile MY-PROFILE
databricks bundle deploy --target dev --profile MY-PROFILE
databricks bundle run metadata_generator_test --target dev --profile MY-PROFILE
```

The included test job analyzes `samples.nyctaxi.trips` and writes a proposal CSV without applying metadata changes.

## Run the notebook directly

Open `notebooks/semantic_metadata_generator.ipynb` in Databricks and edit the first cell or its widgets. Important defaults:

- `include=[]`: scan all visible, supported tables up to `max_tables`;
- `output_directory=""`: write to `/Workspace/Users/<current-user>/dbx-metadata`; set a Workspace or `/Volumes/...` directory to override it;
- `max_tables=25` and `sample_rows=8`: bound cost and runtime;
- `include_views=false`: scan managed, external, and foreign tables only;
- `min_proposed_confidence=0.85`: minimum confidence for the initial `proposed_write=true` recommendation;
- `log_level=INFO`: emit progress events; use `WARNING` or `ERROR` for quieter runs.

The generated filename has the form `metadata_proposals_YYYYMMDDTHHMMSSZ.csv`. The generator never applies comments.

## Apply an edited CSV

Open `notebooks/apply_metadata_csv.ipynb` and set:

- `csv_path`: required path to the uploaded edited CSV;
- `allow_stale_metadata=false`: stop before applying anything when a selected target changed after export;
- `log_level=INFO`: progress verbosity.

The apply notebook validates the full CSV and preflights every selected target before issuing comment DDL.
It verifies each applied value afterward. Rows with `proposed_write=false` are ignored.

## Local checks

The selector library uses only the Python standard library:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m py_compile src/dbx_metadata_generator/*.py
```

The notebook contract test parses the Jupyter file and compiles every Python code cell.
