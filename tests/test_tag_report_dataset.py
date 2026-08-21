from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from tenable_reports.application.tag_report_dataset import (
    build_tag_report_datasets_from_snapshot,
)
from tenable_reports.config.profile import ClientProfile
from tenable_reports.domain.report_dataset import build_report_dataset
from tenable_reports.domain.reporting import ReportingPeriod, previous_calendar_month
from tenable_reports.infrastructure.jsonl_io import write_jsonl_gzip_exclusive
from tests.test_report_dataset import normalized_fixture


RUN_ID = "run-tags-1"


def _july_2026() -> ReportingPeriod:
    return previous_calendar_month(
        reference_at="2026-08-01T00:00:00-03:00",
        timezone_name="America/Fortaleza",
    )


def _profile_with_tags() -> ClientProfile:
    return ClientProfile.from_dict(
        {
            "schema_version": 1,
            "client_id": "client-fixture",
            "display_name": "Cliente Fixture",
            "tenant_id": "tenant-fixture",
            "report": {
                "tag_reports": {
                    "enabled": True,
                    "tags": [
                        {
                            "tag_uuid": "tag-a",
                            "category_uuid": "category-team",
                            "category_name": "Equipe",
                            "value": "Infra",
                            "generate_report": True,
                            "include_temporal_comparison": True,
                        },
                        {
                            "tag_uuid": "tag-b",
                            "category_uuid": "category-place",
                            "category_name": "Local",
                            "value": "Fortaleza",
                            "generate_report": True,
                        },
                        {
                            "tag_uuid": "tag-empty",
                            "category_uuid": "category-team",
                            "category_name": "Equipe",
                            "value": "Sem findings",
                            "generate_report": True,
                        },
                        {
                            "tag_uuid": "tag-disabled",
                            "category_uuid": "category-team",
                            "category_name": "Equipe",
                            "value": "Desabilitada",
                            "generate_report": False,
                        },
                    ],
                }
            },
        }
    )


def _normalized_rows():
    normalized = normalized_fixture()
    base_asset = replace(
        normalized.assets[0],
        source_asset_id="asset-a",
        asset_key="client-fixture:tenable_vm:asset-a",
        display_name="Asset A",
        first_scan_at="2026-06-01T10:00:00Z",
        last_scan_at="2026-07-20T10:00:00Z",
    )
    asset_b = replace(
        base_asset,
        source_asset_id="asset-b",
        asset_key="client-fixture:tenable_vm:asset-b",
        display_name="Asset B",
    )
    shared = replace(
        base_asset,
        source_asset_id="asset-shared",
        asset_key="client-fixture:tenable_vm:asset-shared",
        display_name="Asset Shared",
    )
    finding_a = replace(
        normalized.findings[0],
        finding_key="finding-a",
        source_asset_id=base_asset.source_asset_id,
        asset_key=base_asset.asset_key,
        plugin_id=100101,
        state="OPEN",
        severity="CRITICAL",
        first_found_at="2026-06-10T10:00:00Z",
        last_found_at="2026-07-10T10:00:00Z",
        exploitable=True,
    )
    finding_b = replace(
        finding_a,
        finding_key="finding-b",
        source_asset_id=asset_b.source_asset_id,
        asset_key=asset_b.asset_key,
        plugin_id=100102,
        severity="HIGH",
    )
    finding_shared = replace(
        finding_a,
        finding_key="finding-shared",
        source_asset_id=shared.source_asset_id,
        asset_key=shared.asset_key,
        plugin_id=100103,
        severity="MEDIUM",
    )
    return (base_asset, asset_b, shared), (finding_a, finding_b, finding_shared)


def _tag_scope() -> dict:
    return {
        "schema_version": 2,
        "source": "tenable_vm_tags",
        "run_id": RUN_ID,
        "client_id": "client-fixture",
        "tenant_id": "tenant-fixture",
        "match_operator": "INDEPENDENT_TAG_SCOPES",
        "selected_asset_count": 3,
        "selected_tags": [
            {
                "uuid": "tag-a",
                "category_uuid": "category-team",
                "category_name": "Equipe",
                "value": "Infra",
                "asset_count": 2,
                "asset_ids": ["asset-a", "asset-shared"],
            },
            {
                "uuid": "tag-b",
                "category_uuid": "category-place",
                "category_name": "Local",
                "value": "Fortaleza",
                "asset_count": 2,
                "asset_ids": ["asset-b", "asset-shared"],
            },
            {
                "uuid": "tag-empty",
                "category_uuid": "category-team",
                "category_name": "Equipe",
                "value": "Sem findings",
                "asset_count": 0,
                "asset_ids": [],
            },
            {
                "uuid": "tag-disabled",
                "category_uuid": "category-team",
                "category_name": "Equipe",
                "value": "Desabilitada",
                "asset_count": 1,
                "asset_ids": ["asset-a"],
            },
        ],
        "warnings": [],
    }


def _write_normalized_run(root: Path, *, tag_scope: dict | None = None) -> None:
    assets, findings = _normalized_rows()
    normalized_dir = root / "normalized" / "client-fixture" / RUN_ID
    normalized_dir.mkdir(parents=True)
    write_jsonl_gzip_exclusive(
        normalized_dir / "assets.jsonl.gz",
        (item.to_dict() for item in assets),
    )
    write_jsonl_gzip_exclusive(
        normalized_dir / "findings.jsonl.gz",
        (item.to_dict() for item in findings),
    )
    (normalized_dir / "manifest.json").write_text(
        json.dumps({"client_id": "client-fixture", "run_id": RUN_ID}),
        encoding="utf-8",
    )

    snapshot_dir = root / "snapshots" / "client-fixture" / RUN_ID
    snapshot_dir.mkdir(parents=True)
    common = {
        "run_id": RUN_ID,
        "client_id": "client-fixture",
        "completed_at": "2026-08-01T03:00:00Z",
    }
    (snapshot_dir / "tenable_vm_assets_v2.snapshot.json").write_text(
        json.dumps({**common, "source": "tenable_vm_assets_v2", "snapshot_id": "assets-1"}),
        encoding="utf-8",
    )
    (snapshot_dir / "tenable_vm_vulnerabilities.snapshot.json").write_text(
        json.dumps(
            {
                **common,
                "source": "tenable_vm_vulnerabilities",
                "snapshot_id": "findings-1",
                "query": {
                    "include_plugin_output": False,
                    "filters": {"state": ["OPEN", "REOPENED", "FIXED"]},
                },
            }
        ),
        encoding="utf-8",
    )
    if tag_scope is not None:
        (snapshot_dir / "tenable_vm_tag_scope.snapshot.json").write_text(
            json.dumps(tag_scope),
            encoding="utf-8",
        )


def _build_general(tag_scope: dict | None):
    assets, findings = _normalized_rows()
    return build_report_dataset(
        client_id="client-fixture",
        run_id=RUN_ID,
        execution_type="AUTOMATIC_MONTHLY",
        period=_july_2026(),
        assets=assets,
        findings=findings,
        generated_at=datetime(2026, 8, 1, 3, tzinfo=UTC),
        collection_completed_at=datetime(2026, 8, 1, 3, tzinfo=UTC),
        finding_query={"filters": {"state": ["OPEN", "REOPENED", "FIXED"]}},
        tag_scope=tag_scope,
    )


def test_tag_datasets_are_isolated_and_general_dataset_is_unchanged(tmp_path: Path) -> None:
    without_tags = _build_general(None)
    with_tags = _build_general(_tag_scope())
    _write_normalized_run(tmp_path, tag_scope=_tag_scope())

    bundle = build_tag_report_datasets_from_snapshot(
        profile=_profile_with_tags(),
        run_id=RUN_ID,
        period=_july_2026(),
        output_root=tmp_path,
        include_output=False,
        execution_type="AUTOMATIC_MONTHLY",
    )

    by_uuid = {
        item.tag.uuid: json.loads(item.dataset_path.read_text(encoding="utf-8"))
        for item in bundle.artifacts
    }
    assert set(by_uuid) == {"tag-a", "tag-b", "tag-empty"}
    assert by_uuid["tag-a"]["metrics"]["non_mitigated"]["total"] == 2
    assert by_uuid["tag-b"]["metrics"]["non_mitigated"]["total"] == 2
    assert {row["source_asset_id"] for row in by_uuid["tag-a"]["top_assets"]} == {
        "asset-a",
        "asset-shared",
    }
    assert {row["source_asset_id"] for row in by_uuid["tag-b"]["top_assets"]} == {
        "asset-b",
        "asset-shared",
    }
    assert with_tags.dataset.metrics == without_tags.dataset.metrics
    assert with_tags.dataset.top_assets == without_tags.dataset.top_assets
    assert (
        with_tags.dataset.top_open_vulnerabilities
        == without_tags.dataset.top_open_vulnerabilities
    )


def test_tag_dataset_contains_operational_payload_even_without_findings(tmp_path: Path) -> None:
    _write_normalized_run(tmp_path, tag_scope=_tag_scope())

    bundle = build_tag_report_datasets_from_snapshot(
        profile=_profile_with_tags(),
        run_id=RUN_ID,
        period=_july_2026(),
        output_root=tmp_path,
        execution_type="AUTOMATIC_MONTHLY",
    )
    artifact = next(item for item in bundle.artifacts if item.tag.uuid == "tag-empty")
    data = json.loads(artifact.dataset_path.read_text(encoding="utf-8"))

    assert data["document_kind"] == "tag"
    assert data["tag"]["tag_uuid"] == "tag-empty"
    assert data["tag"]["include_temporal_comparison"] is False
    assert data["top_assets"] == []
    assert data["top_open_vulnerabilities"] == []
    assert data["metrics"]["non_mitigated"]["total"] == 0
    assert data["source_coverage"]["general_collection_filtered_by_tags"] is False


def test_missing_tag_scope_is_a_warning_and_does_not_create_partial_dataset(
    tmp_path: Path,
) -> None:
    scope = _tag_scope()
    scope["selected_tags"] = [
        row for row in scope["selected_tags"] if row["uuid"] != "tag-b"
    ]
    _write_normalized_run(tmp_path, tag_scope=scope)

    bundle = build_tag_report_datasets_from_snapshot(
        profile=_profile_with_tags(),
        run_id=RUN_ID,
        period=_july_2026(),
        output_root=tmp_path,
    )

    assert {item.tag.uuid for item in bundle.artifacts} == {"tag-a", "tag-empty"}
    assert any(
        warning["code"] == "TAG_SCOPE_UNAVAILABLE"
        and warning["tag_uuid"] == "tag-b"
        for warning in bundle.warnings
    )

