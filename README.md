# dbx-metadata-generator

Generate confidence-scored Unity Catalog table and column comment proposals from schema metadata and ephemeral random samples.

The Databricks notebook:

- accepts include and exclude selectors for catalogs, schemas, tables, and glob patterns;
- discovers accessible Unity Catalog relations through `system.information_schema`;
- captures existing descriptions, column metadata, owners, and tags;
- samples a small number of rows at random without persisting or printing them;
- asks an open-weight Databricks model to summarize each sample without reproducing raw records;
- emits timestamped JSON progress logs for discovery, scanning, model calls, proposals, and apply operations;
- builds `TABLE_CONTEXTS` and `PROPOSED_CHANGES` dictionaries;
- applies high-confidence comments only when `dry_run=false`.

The default analysis endpoint is `databricks-qwen35-122b-a10b`, with configurable fallbacks. Change it in the notebook settings or job parameters when the endpoint is unavailable in a workspace.

## Manual Databricks upload

This is the recommended setup when Databricks CLI access is not available.

1. [Download the repository as a ZIP file](https://github.com/reggie-db/dbx-metadata-generator/archive/refs/heads/main.zip).
2. In Databricks, open **Workspace** and navigate to the folder where the notebook should be installed.
3. Select **Import**, choose the downloaded ZIP file, and import it as an archive.
4. Open `notebooks/semantic_metadata_generator` from the imported folder.
5. Connect the notebook to serverless compute.
6. In the first cell, set `include` to a narrow catalog, schema, table, or pattern. Keep `dry_run=true` for the first run.
7. Run all cells and follow the timestamped progress messages in the notebook output.
8. Review the final proposal table. Set `dry_run=false` only after the proposed comments have been reviewed.

Import the repository ZIP rather than only the `.ipynb` file. The notebook loads helper modules from the included `src` directory.

The user running the notebook needs access to the selected Unity Catalog tables and the configured model endpoint. Applying comments also requires permission to modify the selected tables' metadata.

## Selector syntax

Selectors accept `*`, `?`, character groups, and SQL-style `%` wildcards.

| Selector | Matches |
| --- | --- |
| `main` | Every table in catalog `main` |
| `main.sales` | Every table in `main.sales` |
| `main.sales.orders` | One exact table |
| `prod*.sales?.order%` | A three-part pattern |

An empty include list means all visible relations. Exclusions always win. The notebook excludes `system.*.*` and `__databricks_internal.*.*` by default.

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

The included test job runs a dry analysis of `samples.nyctaxi.trips`, scans one table, and applies no metadata changes.

## Run the notebook directly

Open `notebooks/semantic_metadata_generator.ipynb` in Databricks and edit the first cell or its widgets. Important defaults:

- `include=[]`: scan all visible, supported tables up to `max_tables`;
- `dry_run=true`: produce proposals without applying comments;
- `max_tables=25` and `sample_rows=8`: bound cost and runtime;
- `include_views=false`: scan managed, external, and foreign tables only;
- `min_apply_confidence=0.85`: minimum confidence when application is enabled.
- `log_level=INFO`: emit progress events; use `WARNING` or `ERROR` for quieter runs.

Set `dry_run=false` only after reviewing the displayed proposal table and narrowing the include selectors.

## Local checks

The selector library uses only the Python standard library:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m py_compile src/dbx_metadata_generator/*.py
```

The notebook contract test parses the Jupyter file and compiles every Python code cell.
