import unittest
from pathlib import Path


NOTEBOOK = Path(__file__).parents[1] / "notebooks" / "semantic_metadata_generator.py"


class NotebookContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = NOTEBOOK.read_text()

    def test_is_databricks_source_notebook(self) -> None:
        self.assertTrue(self.source.startswith("# Databricks notebook source\n"))

    def test_safe_defaults_are_present(self) -> None:
        self.assertIn('"dry_run": "true"', self.source)
        self.assertIn('"include": "[]"', self.source)
        self.assertIn('"model_endpoint": "databricks-qwen35-122b-a10b"', self.source)

    def test_samples_are_not_saved_in_context(self) -> None:
        self.assertIn("sample_rows.clear()", self.source)
        self.assertIn('metadata["sample_summary"] = sample_summary', self.source)
        self.assertNotIn('"sample_rows": sample_rows', self.source)


if __name__ == "__main__":
    unittest.main()
