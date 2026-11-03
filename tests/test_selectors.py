import unittest

from dbx_metadata_generator.selectors import RelationFilter, parse_selector_list


class SelectorParsingTest(unittest.TestCase):
    def test_parses_json_and_delimited_values(self) -> None:
        self.assertEqual(parse_selector_list('["main", "samples.nyctaxi.*"]'), ["main", "samples.nyctaxi.*"])
        self.assertEqual(parse_selector_list("main, samples.nyctaxi.*\nother.schema"), ["main", "samples.nyctaxi.*", "other.schema"])
        self.assertEqual(parse_selector_list(""), [])


class RelationFilterTest(unittest.TestCase):
    def test_empty_include_matches_all(self) -> None:
        relation_filter = RelationFilter.compile()
        self.assertTrue(relation_filter.matches("main", "sales", "orders"))

    def test_catalog_schema_and_table_selectors(self) -> None:
        catalog = RelationFilter.compile(["main"])
        schema = RelationFilter.compile(["main.sales"])
        table = RelationFilter.compile(["main.sales.orders"])
        self.assertTrue(catalog.matches("main", "other", "table"))
        self.assertFalse(catalog.matches("samples", "other", "table"))
        self.assertTrue(schema.matches("main", "sales", "customers"))
        self.assertFalse(schema.matches("main", "finance", "customers"))
        self.assertTrue(table.matches("main", "sales", "orders"))
        self.assertFalse(table.matches("main", "sales", "customers"))

    def test_globs_and_sql_percent_wildcards(self) -> None:
        relation_filter = RelationFilter.compile(["prod*.sales?.order%"])
        self.assertTrue(relation_filter.matches("production", "sales1", "orders"))
        self.assertFalse(relation_filter.matches("production", "sales12", "orders"))

    def test_exclusion_wins(self) -> None:
        relation_filter = RelationFilter.compile(["main"], ["main.private.*"])
        self.assertTrue(relation_filter.matches("main", "public", "orders"))
        self.assertFalse(relation_filter.matches("main", "private", "orders"))

    def test_backtick_quoted_components(self) -> None:
        relation_filter = RelationFilter.compile(["`catalog.with.dot`.schema.table"])
        self.assertTrue(relation_filter.matches("catalog.with.dot", "schema", "table"))

    def test_invalid_selector_fails(self) -> None:
        with self.assertRaises(ValueError):
            RelationFilter.compile(["a.b.c.d"])


if __name__ == "__main__":
    unittest.main()
