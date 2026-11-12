import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dbx_metadata_generator.review_csv import (
    COLUMN_DESCRIPTION,
    CSV_FIELDS,
    TABLE_DESCRIPTION,
    build_review_rows,
    default_output_directory,
    parse_write_flag,
    read_review_csv,
    write_review_csv,
)


class ReviewCsvTest(unittest.TestCase):
    def setUp(self) -> None:
        self.contexts = {
            "main.sales.orders": {
                "catalog": "main",
                "schema": "sales",
                "table": "orders",
                "table_type": "MANAGED",
                "owner": "owner@example.com",
                "columns": [
                    {
                        "name": "order_id",
                        "data_type": "BIGINT",
                        "nullable": "NO",
                    }
                ],
            }
        }
        self.changes = {
            "main.sales.orders": {
                "table": {
                    "existing_description": "",
                    "proposed_description": "One row per order.",
                    "action": "add",
                    "confidence": 0.95,
                    "rationale": "Clear grain.",
                },
                "columns": {
                    "order_id": {
                        "existing_description": "Existing",
                        "proposed_description": "Unique order identifier.",
                        "action": "evolve",
                        "confidence": 0.8,
                        "rationale": "More specific.",
                    }
                },
                "model_endpoint": "model",
            }
        }

    def test_builds_table_and_column_review_rows(self) -> None:
        rows = build_review_rows(
            self.contexts,
            self.changes,
            minimum_confidence=0.85,
            evolve_existing_comments=True,
            generated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )
        self.assertEqual([row["target_type"] for row in rows], [TABLE_DESCRIPTION, COLUMN_DESCRIPTION])
        self.assertEqual(rows[0]["metadata_path"], "main.sales.orders.[DESCRIPTION]")
        self.assertEqual(rows[1]["metadata_path"], "main.sales.orders.order_id")
        self.assertEqual(rows[0]["proposed_write"], "true")
        self.assertEqual(rows[1]["proposed_write"], "false")
        self.assertNotIn("table_type", rows[0])
        self.assertNotIn("table_owner", rows[0])

    def test_writes_timestamped_csv_and_reads_it_back(self) -> None:
        rows = build_review_rows(
            self.contexts,
            self.changes,
            minimum_confidence=0.85,
            evolve_existing_comments=True,
        )
        generated_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            path = write_review_csv(rows, directory, generated_at=generated_at)
            self.assertEqual(path.name, "metadata_proposals_20260102T030405Z.csv")
            self.assertEqual(read_review_csv(path)[0]["new_metadata"], "One row per order.")
            second_path = write_review_csv(rows, directory, generated_at=generated_at)
            self.assertEqual(second_path.name, "metadata_proposals_20260102T030405Z_01.csv")

    def test_blank_new_metadata_is_preserved_for_clearing(self) -> None:
        rows = build_review_rows(
            self.contexts,
            self.changes,
            minimum_confidence=0.85,
            evolve_existing_comments=True,
        )
        rows[0]["proposed_write"] = "true"
        rows[0]["new_metadata"] = ""
        with tempfile.TemporaryDirectory() as directory:
            path = write_review_csv(rows, directory)
            self.assertEqual(read_review_csv(path)[0]["new_metadata"], "")

    def test_rejects_missing_columns_and_duplicate_targets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing_path = Path(directory) / "missing.csv"
            with missing_path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["metadata_path"])
                writer.writeheader()
            with self.assertRaisesRegex(ValueError, "missing required columns"):
                read_review_csv(missing_path)

            duplicate_path = Path(directory) / "duplicate.csv"
            row = {field: "" for field in CSV_FIELDS}
            row.update(
                {
                    "metadata_path": "main.sales.orders.[DESCRIPTION]",
                    "target_type": TABLE_DESCRIPTION,
                    "catalog_name": "main",
                    "schema_name": "sales",
                    "table_name": "orders",
                    "proposed_write": "false",
                }
            )
            with duplicate_path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerows([row, row])
            with self.assertRaisesRegex(ValueError, "duplicates metadata target"):
                read_review_csv(duplicate_path)

            row["metadata_path"] = "main.sales.other.[DESCRIPTION]"
            mismatch_path = Path(directory) / "mismatch.csv"
            with mismatch_path.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
                writer.writeheader()
                writer.writerow(row)
            with self.assertRaisesRegex(ValueError, "metadata_path does not match"):
                read_review_csv(mismatch_path)

    def test_parses_only_explicit_write_flags(self) -> None:
        self.assertTrue(parse_write_flag("TRUE"))
        self.assertFalse(parse_write_flag(""))
        with self.assertRaises(ValueError):
            parse_write_flag("ture")

    def test_default_output_directory_is_workspace_home(self) -> None:
        self.assertEqual(
            default_output_directory("person@example.com"),
            Path("/Workspace/Users/person@example.com/dbx-metadata"),
        )


if __name__ == "__main__":
    unittest.main()
