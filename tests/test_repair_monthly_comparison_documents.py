from __future__ import annotations

from dataclasses import replace
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
    )

    assert hasattr(repair, "_selected_documents")
    assert [
        item.document_kind
        for item in repair._selected_documents(documents, tag_only=True)
    ] == ["tag", "tag"]
    assert len(repair._selected_documents(documents, tag_only=False)) == 3


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
