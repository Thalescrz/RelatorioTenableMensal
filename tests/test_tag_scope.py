from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tenable_reports.application.normalize import filter_records_to_asset_scope
from tenable_reports.application.tag_scope import (
    VmTag,
    collect_tag_scope_snapshot,
    parse_number_selection,
    parse_tag_values,
    prompt_tag_selection,
    resolve_tag_selectors,
)
from tenable_reports.config.profile import ClientProfile


class FakeVmClient:
    def __init__(self, values, failures=None) -> None:
        self.values = values
        self.failures = failures or {}

    def list_assets_for_tag(self, category_name, value):
        key = (category_name, value)
        if key in self.failures:
            raise self.failures[key]
        return list(self.values.get(key, ()))


def profile() -> ClientProfile:
    return ClientProfile.from_dict(
        {
            "schema_version": 1,
            "client_id": "client-001",
            "display_name": "Cliente",
            "tenant_id": "tenant",
        }
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

    def test_selectors_accept_different_categories_as_independent_scopes(self) -> None:
        selected = resolve_tag_selectors(self.tags, ["tag-a", "tag-c"])

        self.assertEqual([item.uuid for item in selected], ["tag-a", "tag-c"])

    def test_scope_snapshot_keeps_each_tag_asset_set_separate(self) -> None:
        client = FakeVmClient(
            {
                ("Rede", "Matriz"): [
                    {"id": "asset-a"},
                    {"id": "asset-shared"},
                ],
                ("Sistema", "Linux"): [
                    {"id": "asset-b"},
                    {"id": "asset-shared"},
                ],
            }
        )
        with tempfile.TemporaryDirectory() as directory:
            result = collect_tag_scope_snapshot(
                client=client,
                profile=profile(),
                tags=(self.tags[0], self.tags[2]),
                output_root=directory,
                run_id="run-1",
            )
            payload = json.loads(Path(result.path).read_text(encoding="utf-8"))

        self.assertEqual(
            result.scopes[0].asset_ids,
            frozenset({"asset-a", "asset-shared"}),
        )
        self.assertEqual(
            result.scopes[1].asset_ids,
            frozenset({"asset-b", "asset-shared"}),
        )
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["match_operator"], "INDEPENDENT_TAG_SCOPES")

    def test_one_tag_failure_becomes_warning_without_erasing_other_scopes(self) -> None:
        client = FakeVmClient(
            {("Rede", "Matriz"): [{"id": "asset-a"}]},
            failures={("Sistema", "Linux"): RuntimeError("limit")},
        )
        with tempfile.TemporaryDirectory() as directory:
            result = collect_tag_scope_snapshot(
                client=client,
                profile=profile(),
                tags=(self.tags[0], self.tags[2]),
                output_root=directory,
                run_id="run-1",
            )

        self.assertEqual([scope.tag.uuid for scope in result.scopes], ["tag-a"])
        self.assertEqual(result.warnings[0]["tag_uuid"], "tag-c")
        self.assertEqual(result.warnings[0]["code"], "TAG_SCOPE_UNAVAILABLE")

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
