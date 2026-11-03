import unittest

from dbx_metadata_generator.structured import structured_dict


class StructuredResponseTest(unittest.TestCase):
    def test_accepts_dictionary(self) -> None:
        self.assertEqual(structured_dict({"value": 1}), {"value": 1})

    def test_parses_json_string(self) -> None:
        self.assertEqual(structured_dict('{"columns": []}'), {"columns": []})

    def test_strips_markdown_fence(self) -> None:
        self.assertEqual(structured_dict('```json\n{"confidence": 0.9}\n```'), {"confidence": 0.9})

    def test_rejects_scalar(self) -> None:
        with self.assertRaises(TypeError):
            structured_dict(42)


if __name__ == "__main__":
    unittest.main()
