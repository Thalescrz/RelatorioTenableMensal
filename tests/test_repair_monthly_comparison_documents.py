from __future__ import annotations

from dataclasses import replace
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

from tenable_reports.domain.history import HistorySnapshot, SnapshotCompatibility
from tools import repair_monthly_comparison_documents as repair


def _snapshot(run_id: str, *, top_count: int) -> HistorySnapshot:
    return HistorySnapshot(
        snapshot_id=f"snapshot-{run_id}",
        run_id=run_id,
        period_id="2026-08",
        period_start_at="2026-08-01T03:00:00Z",
        period_end_at="2026-09-01T03:00:00Z",
        generated_at="2026-09-01T03:00:00Z",
        compatibility=SnapshotCompatibility(
            client_id="cliente-exemplo",
            tenant_id="tenant-exemplo",
            execution_type="AUTOMATIC_MONTHLY",
            period_mode="PREVIOUS_CALENDAR_MONTH",
            timezone="America/Fortaleza",
            metric_definition_version="report-definition-v1.2",
            scope_hash="scope-a",
        ),
        summary={"non_mitigated": 50},
        open_finding_keys=(),
        fixed_finding_keys=(),
        resurfaced_finding_keys=(),
        tag_snapshots=(
            {
                "tag_uuid": "tag-a",
                "category_name": "Equipe",
                "value": "Infra",
                "summary": {"non_mitigated": 50},
                "top_assets": [
                    {"asset_key": f"asset-{index:02d}", "total": 50 - index}
                    for index in range(top_count)
                ],
            },
        ),
    )


def test_overlay_tag_top_assets_recovers_twenty_without_changing_summary() -> None:
    stored = _snapshot("run-august", top_count=10)
    recovered_rows = (
        {
            "tag_uuid": "tag-a",
            "assets": [
                {"asset_key": f"asset-{index:02d}", "total": 50 - index}
                for index in range(20)
            ],
        },
    )

    assert hasattr(repair, "_overlay_tag_top_assets")
    rebuilt = repair._overlay_tag_top_assets(stored, recovered_rows)

    assert rebuilt.summary == stored.summary
    assert rebuilt.tag_snapshots[0]["summary"] == {"non_mitigated": 50}
    assert len(rebuilt.tag_snapshots[0]["top_assets"]) == 20


def test_snapshot_overlay_registry_substitutes_only_the_rebuilt_predecessor() -> None:
    stored = _snapshot("run-august", top_count=10)
    rebuilt = replace(stored, tag_snapshots=_snapshot("run-august", top_count=20).tag_snapshots)
    older = replace(_snapshot("run-july", top_count=5), period_id="2026-07")

    class BaseRegistry:
        def get_main_snapshot(self, _key):
            return stored

        def list_main_snapshots_before(self, _key):
            return (older, stored)

    assert hasattr(repair, "_SnapshotOverlayRegistry")
    registry = repair._SnapshotOverlayRegistry(BaseRegistry(), rebuilt)

    assert registry.get_main_snapshot(object()) is rebuilt
    assert registry.list_main_snapshots_before(object()) == (older, rebuilt)


def test_tag_only_selection_excludes_custom_document() -> None:
    documents = (
        repair.PublicationDocument(path="custom.docx", document_kind="custom"),
        repair.PublicationDocument(path="tag-a.docx", document_kind="tag"),
        repair.PublicationDocument(path="tag-b.docx", document_kind="tag"),
        repair.PublicationDocument(path="cloud.docx", document_kind="cloud"),
    )

    assert hasattr(repair, "_selected_documents")
    assert [
        item.document_kind
        for item in repair._selected_documents(documents, tag_only=True)
    ] == ["tag", "tag"]
    assert len(repair._selected_documents(documents, tag_only=False)) == 4


def test_manifest_cloud_dataset_path_uses_registered_source_dataset(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "cloud-dataset.json"
    dataset.write_text("{}", encoding="utf-8")
    digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
    manifest = tmp_path / "publication-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_datasets": {
                    "cloud": {
                        "path": str(dataset),
                        "sha256": digest,
                        "size_bytes": 2,
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    assert repair._manifest_cloud_dataset_path(manifest) == dataset.resolve()


def test_legacy_cloud_period_id_matches_canonical_month_from_start() -> None:
    assert repair._cloud_dataset_matches_period(
        {
            "period_id": "01SET26-30SET26",
            "start_at": "2026-09-01T03:00:00Z",
        },
        period_key="2026-09",
        timezone="America/Fortaleza",
    )


def test_plan_from_position_keeps_only_unprocessed_tail() -> None:
    assert repair._plan_from_position(("a", "b", "c"), 2) == ("b", "c")
    assert repair._plan_from_position((), 1) == ()


def test_tag_only_plan_omits_clients_without_tag_documents() -> None:
    custom_only = SimpleNamespace(
        documents=(
            repair.PublicationDocument(path="custom.docx", document_kind="custom"),
        )
    )
    with_tag = SimpleNamespace(
        documents=(
            repair.PublicationDocument(path="custom.docx", document_kind="custom"),
            repair.PublicationDocument(path="tag.docx", document_kind="tag"),
        )
    )

    assert hasattr(repair, "_selected_plan")
    assert repair._selected_plan((custom_only, with_tag), tag_only=True) == (with_tag,)
    assert repair._selected_plan((custom_only, with_tag), tag_only=False) == (
        custom_only,
        with_tag,
    )
