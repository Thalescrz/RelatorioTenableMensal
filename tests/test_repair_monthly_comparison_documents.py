from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

from tenable_reports.domain.history import HistorySnapshot, SnapshotCompatibility
from tenable_reports.application.compact_snapshots import build_compact_snapshot
from tenable_reports.domain.fingerprints import fingerprint_finding_key
from tests.test_report_dataset import normalized_fixture
from tests.test_tag_report_dataset import _profile_with_tags
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


def test_client_selection_preserves_plan_order_and_rejects_unknown_client() -> None:
    plan = (
        SimpleNamespace(current=SimpleNamespace(client_id="client-a")),
        SimpleNamespace(current=SimpleNamespace(client_id="client-b")),
        SimpleNamespace(current=SimpleNamespace(client_id="client-c")),
    )

    assert repair._select_clients(plan, frozenset({"client-c", "client-a"})) == (
        plan[0],
        plan[2],
    )

    try:
        repair._select_clients(plan, frozenset({"client-missing"}))
    except ValueError as exc:
        assert "não pertence" in str(exc)
    else:
        raise AssertionError("Seleção desconhecida deveria ser rejeitada.")


def test_main_run_selection_happens_before_unselected_plan_validation() -> None:
    runs = (
        SimpleNamespace(client_id="client-a"),
        SimpleNamespace(client_id="client-b"),
        SimpleNamespace(client_id="client-c"),
    )

    assert repair._select_main_runs(runs, frozenset({"client-c", "client-a"})) == (
        runs[0],
        runs[2],
    )

    try:
        repair._select_main_runs(runs, frozenset({"client-missing"}))
    except ValueError as exc:
        assert "não pertence" in str(exc)
    else:
        raise AssertionError("Cliente fora dos MAIN deveria ser rejeitado.")


def test_missing_compact_is_restored_from_preserved_normalized_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    execution_root = tmp_path / "manual"
    dataset = (
        execution_root
        / "report-datasets"
        / "client-a"
        / "run-august"
        / "2026-08"
        / "report-dataset.json"
    )
    dataset.parent.mkdir(parents=True)
    dataset.write_text("{}", encoding="utf-8")
    source_snapshot = (
        execution_root
        / "snapshots"
        / "client-a"
        / "run-august"
        / "tenable_vm_vulnerabilities.snapshot.json"
    )
    source_snapshot.parent.mkdir(parents=True)
    source_snapshot.write_text(
        json.dumps({"completed_at": "2026-09-05T12:00:00Z"}),
        encoding="utf-8",
    )
    document = tmp_path / "custom.docx"
    document.write_bytes(b"docx")
    manifest = tmp_path / "publication-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "source_datasets": {
                    "vm": {
                        "path": str(dataset),
                        "sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    run = repair.MainRun(
        run_id="run-august",
        client_id="client-a",
        tenant_id="tenant-a",
        period_key="2026-08",
        timezone="America/Fortaleza",
        scope_hash="scope-a",
        metric_definition_version="report-definition-v1.2",
        execution_type="AUTOMATIC_MONTHLY",
        period_start_at="2026-08-01T03:00:00Z",
        period_end_at="2026-09-01T03:00:00Z",
        period_mode="PREVIOUS_CALENDAR_MONTH",
        origin="SCHEDULED",
        manifest_path=manifest,
    )
    marker = object()
    captured = {}

    def fake_prepare(**kwargs):
        captured.update(kwargs)
        return marker

    monkeypatch.setattr(repair, "prepare_compact_run_snapshot", fake_prepare)

    restored = repair._restore_predecessor_compact(
        predecessor=run,
        profile=SimpleNamespace(),
        documents=(repair.PublicationDocument(path=str(document), document_kind="custom"),),
    )

    assert restored is marker
    assert captured["output_root"] == execution_root.resolve()
    assert captured["created_at"] == "2026-09-05T12:00:00Z"


def test_rebuild_predecessor_snapshot_updates_summary_fingerprints_and_tag(
    tmp_path: Path,
) -> None:
    profile = _profile_with_tags()
    normalized = normalized_fixture()
    asset = replace(
        normalized.assets[0],
        client_id=profile.client_id,
        source_asset_id="asset-a",
        asset_key="client-fixture:tenable_vm:asset-a",
        first_scan_at="2026-07-01T10:00:00Z",
        last_scan_at="2026-09-05T10:00:00Z",
    )
    finding = replace(
        normalized.findings[0],
        finding_key="finding-open-before-close",
        client_id=profile.client_id,
        source_asset_id=asset.source_asset_id,
        asset_key=asset.asset_key,
        state="OPEN",
        severity="CRITICAL",
        first_found_at="2026-07-10T10:00:00Z",
        last_found_at="2026-09-05T09:00:00Z",
        resurfaced_at=None,
    )
    compact = build_compact_snapshot(
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        run_id="run-august",
        execution_type="AUTOMATIC_MONTHLY",
        period_mode="PREVIOUS_CALENDAR_MONTH",
        period_start_at="2026-08-01T03:00:00Z",
        period_end_at="2026-09-01T03:00:00Z",
        assets=(asset,),
        findings=(finding,),
        quality_issues=(),
        tag_asset_ids={"tag-a": (asset.source_asset_id,)},
        tag_scope={
            "selected_tags": [
                {
                    "uuid": "tag-a",
                    "category_uuid": "category-team",
                    "category_name": "Equipe",
                    "value": "Infra",
                    "asset_ids": [asset.source_asset_id],
                }
            ]
        },
        document_references={},
        created_at="2026-09-05T12:00:00Z",
    )
    predecessor_run = repair.MainRun(
        run_id="run-august",
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        period_key="2026-08",
        timezone="America/Fortaleza",
        scope_hash="scope-a",
        metric_definition_version="report-definition-v1.2",
        execution_type="AUTOMATIC_MONTHLY",
        period_start_at="2026-08-01T03:00:00Z",
        period_end_at="2026-09-01T03:00:00Z",
        period_mode="PREVIOUS_CALENDAR_MONTH",
        origin="SCHEDULED",
        manifest_path=tmp_path / "predecessor-manifest.json",
    )
    current_run = replace(
        predecessor_run,
        run_id="run-september",
        period_key="2026-09",
        period_start_at="2026-09-01T03:00:00Z",
        period_end_at="2026-10-01T03:00:00Z",
        manifest_path=tmp_path / "current-manifest.json",
    )
    stored = _snapshot("run-august", top_count=0)
    item = repair.RepairPlanItem(
        current=current_run,
        predecessor=predecessor_run,
        profile_path=tmp_path / "profile.json",
        profile=profile,
        compact_snapshot=compact,
        predecessor_compact_snapshot=compact,
        current_snapshot=replace(stored, run_id="run-september", period_id="2026-09"),
        predecessor_snapshot=stored,
        documents=(),
        controlled_scope_override=False,
    )

    rebuilt = repair._rebuild_predecessor_snapshot(
        item,
        output_root=tmp_path / "materialized",
    )

    assert rebuilt.summary["non_mitigated"] == 1
    assert rebuilt.open_finding_keys == (
        fingerprint_finding_key(finding.finding_key),
    )
    assert rebuilt.tag_snapshots[0]["summary"]["non_mitigated"] == 1
    assert rebuilt.tag_snapshots[0]["top_assets"][0]["total"] == 1
