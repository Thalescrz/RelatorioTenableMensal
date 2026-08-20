from __future__ import annotations

import unittest

from tenable_reports.application.normalize import filter_records_to_asset_scope
from tenable_reports.application.tag_scope import (
    VmTag,
    parse_number_selection,
    parse_tag_values,
    prompt_tag_selection,
    resolve_tag_selectors,
)


class TagScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tags = (
            VmTag("tag-a", "cat-rede", "Rede", "Matriz"),
            VmTag("tag-b", "cat-rede", "Rede", "Filial"),
            VmTag("tag-c", "cat-os", "Sistema", "Linux"),
        )

    def test_tag_payload_is_normalized_and_empty_categories_are_ignored(self) -> None:
        rows = parse_tag_values([
            {"uuid": "tag-b", "category_uuid": "cat-rede", "category_name": "Rede", "value": "Filial"},
            {"uuid": "cat-empty", "category_name": "Sem valores"},
            {"uuid": "tag-a", "category_uuid": "cat-rede", "category_name": "Rede", "value": "Matriz"},
        ])
        self.assertEqual([item.uuid for item in rows], ["tag-b", "tag-a"])

    def test_number_selection_accepts_ranges_and_all(self) -> None:
        self.assertEqual(parse_number_selection("1,3-5", 5), (1, 3, 4, 5))
        self.assertEqual(parse_number_selection("todos", 3), (1, 2, 3))

    def test_interactive_flow_selects_multiple_values_from_one_category(self) -> None:
        answers = iter(["1", "1-2"])
        output: list[str] = []
        selected = prompt_tag_selection(
            self.tags,
            input_fn=lambda _: next(answers),
            output_fn=output.append,
        )
        self.assertEqual({item.uuid for item in selected}, {"tag-a", "tag-b"})
        self.assertTrue(any("Categorias" in line for line in output))

    def test_selectors_reject_categories_that_would_be_combined_with_and(self) -> None:
        with self.assertRaisesRegex(ValueError, "AND"):
            resolve_tag_selectors(self.tags, ["tag-a", "tag-c"])

    def test_asset_and_finding_records_are_restricted_to_the_same_union(self) -> None:
        assets, findings = filter_records_to_asset_scope(
            asset_records=({"id": "asset-a"}, {"id": "asset-b"}),
            finding_records=(
                {"asset": {"uuid": "asset-a"}, "plugin": {"id": 1}},
                {"asset": {"uuid": "asset-b"}, "plugin": {"id": 2}},
            ),
            allowed_asset_ids=frozenset({"asset-b"}),
        )
        self.assertEqual([item["id"] for item in assets], ["asset-b"])
        self.assertEqual([item["plugin"]["id"] for item in findings], [2])


if __name__ == "__main__":
    unittest.main()
