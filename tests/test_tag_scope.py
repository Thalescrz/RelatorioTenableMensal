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
from tenable_reports.domain.execution_control import ExecutionInterruptedError
from tenable_reports.infrastructure.tenable_vm.client import (
    ExportJob,
    TagScopeLimitExceeded,
)


class FakeVmClient:
    def __init__(self, values, failures=None) -> None:
        self.values = values
        self.failures = failures or {}
        self.calls = []

    def list_assets_for_tag(self, category_name, value):
        key = (category_name, value)
        self.calls.append(key)
        if key in self.failures:
            raise self.failures[key]
        return list(self.values.get(key, ()))


class LargeTagVmClient(FakeVmClient):
    def __init__(self, chunks) -> None:
        super().__init__({}, failures={
            ("Rede", "Matriz"): TagScopeLimitExceeded(
                "Escopo maior que o Workbench.",
                total_assets=5001,
            )
        })
        self.chunks = chunks
        self.export_requests = []

    def start_asset_export_v1_job(self, **kwargs):
        self.export_requests.append(dict(kwargs))
        return ExportJob("tag-export-fixture", "created")

    def wait_for_asset_completion(self, export_uuid, **kwargs):
        return {"status": "FINISHED"}, sorted(self.chunks)

    def download_asset_chunk(self, export_uuid, chunk_id):
        return list(self.chunks[chunk_id])


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

    def test_scope_snapshot_retry_reuses_same_run_without_api_recollection(self) -> None:
        client = FakeVmClient(
            {("Rede", "Matriz"): [{"id": "asset-a"}]},
        )
        with tempfile.TemporaryDirectory() as directory:
            first = collect_tag_scope_snapshot(
                client=client,
                profile=profile(),
                tags=(self.tags[0],),
                output_root=directory,
                run_id="same-run",
            )
            second = collect_tag_scope_snapshot(
                client=client,
                profile=profile(),
                tags=(self.tags[0],),
                output_root=directory,
                run_id="same-run",
            )

        self.assertEqual(second.path, first.path)
        self.assertEqual(second.scopes, first.scopes)
        self.assertEqual(client.calls, [("Rede", "Matriz")])

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

    def test_large_tag_falls_back_to_asset_export_v1_without_losing_assets(self) -> None:
        client = LargeTagVmClient({
            1: ({"id": "asset-a"}, {"id": "asset-shared"}),
            2: ({"id": "asset-b"}, {"id": "asset-shared"}),
        })

        with tempfile.TemporaryDirectory() as directory:
            result = collect_tag_scope_snapshot(
                client=client,
                profile=profile(),
                tags=(self.tags[0],),
                output_root=directory,
                run_id="large-tag-run",
            )
            payload = json.loads(result.path.read_text(encoding="utf-8"))

        self.assertEqual(result.warnings, ())
        self.assertEqual(
            result.scopes[0].asset_ids,
            frozenset({"asset-a", "asset-b", "asset-shared"}),
        )
        self.assertEqual(client.export_requests, [{
            "filters": {"tag.Rede": ["Matriz"]},
            "chunk_size": 5000,
        }])
        self.assertEqual(
            payload["selected_tags"][0]["scope_source"],
            "tenable_vm_asset_export_v1",
        )
        self.assertEqual(
            payload["selected_tags"][0]["export_uuid"],
            "tag-export-fixture",
        )

    def test_retry_unavailable_repairs_only_missing_tag_scope(self) -> None:
        failed = FakeVmClient(
            {},
            failures={("Rede", "Matriz"): RuntimeError("temporarily unavailable")},
        )
        recovered = LargeTagVmClient({1: ({"id": "asset-recovered"},)})

        with tempfile.TemporaryDirectory() as directory:
            first = collect_tag_scope_snapshot(
                client=failed,
                profile=profile(),
                tags=(self.tags[0],),
                output_root=directory,
                run_id="repair-run",
            )
            second = collect_tag_scope_snapshot(
                client=recovered,
                profile=profile(),
                tags=(self.tags[0],),
                output_root=directory,
                run_id="repair-run",
                retry_unavailable=True,
            )
            payload = json.loads(second.path.read_text(encoding="utf-8"))

        self.assertEqual(first.warnings[0]["tag_uuid"], "tag-a")
        self.assertEqual(second.warnings, ())
        self.assertEqual(second.scopes[0].asset_ids, frozenset({"asset-recovered"}))
        self.assertEqual(payload["warnings"], [])

    def test_large_tag_export_does_not_swallow_local_cancellation(self) -> None:
        client = LargeTagVmClient({1: ({"id": "asset-a"},)})

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ExecutionInterruptedError):
                collect_tag_scope_snapshot(
                    client=client,
                    profile=profile(),
                    tags=(self.tags[0],),
                    output_root=directory,
                    run_id="cancelled-large-tag",
                    cancellation_probe=lambda: True,
                )

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
