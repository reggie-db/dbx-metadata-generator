import json
import unittest
from pathlib import Path


NOTEBOOK = Path(__file__).parents[1] / "notebooks" / "semantic_metadata_generator.ipynb"


class NotebookContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.notebook = json.loads(NOTEBOOK.read_text())
        cls.code_cells = [
            "".join(cell["source"])
            for cell in cls.notebook["cells"]
            if cell["cell_type"] == "code"
        ]
        cls.source = "\n".join(cls.code_cells)

    def test_is_jupyter_notebook(self) -> None:
        self.assertEqual(self.notebook["nbformat"], 4)
        self.assertEqual(self.notebook["metadata"]["language_info"]["name"], "python")

    def test_first_cell_contains_settings(self) -> None:
        self.assertEqual(self.notebook["cells"][0]["cell_type"], "code")
        self.assertIn("DEFAULTS = {", "".join(self.notebook["cells"][0]["source"]))

    def test_safe_defaults_are_present(self) -> None:
        self.assertIn('"dry_run": "true"', self.source)
        self.assertIn('"include": "[]"', self.source)
        self.assertIn('"model_endpoint": "databricks-qwen35-122b-a10b"', self.source)
        self.assertIn('"log_level": "INFO"', self.source)

    def test_samples_are_not_saved_in_context(self) -> None:
        self.assertIn("sample_rows.clear()", self.source)
        self.assertIn('metadata["sample_summary"] = sample_summary', self.source)
        self.assertNotIn('"sample_rows": sample_rows', self.source)

    def test_progress_logging_covers_execution_stages(self) -> None:
        self.assertIn("def progress(", self.source)
        self.assertIn('progress("discovery", "Discovering visible Unity Catalog relations"', self.source)
        self.assertIn('progress("model", "Calling model endpoint"', self.source)
        self.assertIn('"Dry run enabled; no comments will be applied"', self.source)
        self.assertIn('"Metadata generation finished"', self.source)

    def test_python_cells_compile(self) -> None:
        for index, source in enumerate(self.code_cells, start=1):
            with self.subTest(cell=index):
                compile(source, f"{NOTEBOOK.name}:cell-{index}", "exec")


if __name__ == "__main__":
    unittest.main()
