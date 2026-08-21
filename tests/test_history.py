from __future__ import annotations

import csv
import json
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest

from tenable_reports.application.history import (
    SQLiteSnapshotRepository,
    finalize_history_publication,
    import_history_csv,
    prepare_dataset_history,
    publish_dataset_history,
)
from tenable_reports.application.report_registry import InMemoryReportRegistry
from tenable_reports.config.profile import load_client_profile
from tenable_reports.infrastructure.jsonl_io import write_jsonl_gzip_exclusive
from tenable_reports.domain.fingerprints import fingerprint_finding_key
from tenable_reports.domain.history import (
    HistorySnapshot,
    SnapshotCompatibility,
    tag_year_history,
)
from tenable_reports.application.retention import (
    apply_cleanup_plan,
    plan_published_run_cleanup,
)


ROOT = Path(__file__).resolve().parents[1]


class _RecordingSnapshotRepository:
    location = "memory://history"

    def __init__(self) -> None:
        self.published = []

    def publish(self, snapshot) -> None:
        self.published.append(snapshot)

    def compatible_snapshots(self, compatibility, *, before_period_end_at):
        return ()


def _dataset(period_id: str, start: str, end: str, *, total: int) -> dict:
    value = json.loads(
        (ROOT / "tests/fixtures/report-dataset-phase5.json").read_text(
            encoding="utf-8"
        )
    )
    value["metric_definition_version"] = "report-definition-v1.2"
    value["run_id"] = f"run-{period_id}"
    value["execution_type"] = "AUTOMATIC_MONTHLY"
    value["period"].update({
        "period_id": period_id,
        "mode": "PREVIOUS_CALENDAR_MONTH",
        "timezone": "America/Fortaleza",
        "start_at": start,
        "end_at": end,
    })
    value["metrics"]["non_mitigated"].update({
        "total": total,
        "by_severity": {
            "critical": total // 10,
            "high": total // 5,
            "medium": total // 2,
            "low": total - (total // 10 + total // 5 + total // 2),
        },
        "new_in_period": 2,
        "new_by_severity": {"critical": 0, "high": 1, "medium": 1, "low": 0},
        "exploitable": 3,
        "exploitable_by_severity": {"critical": 1, "high": 1, "medium": 1, "low": 0},
        "patch_available_over_30_days": 4,
        "patch_available_over_30_days_by_severity": {
            "critical": 1, "high": 1, "medium": 1, "low": 1,
        },
    })
    value["metrics"]["mitigated"].update({
        "total": 5,
        "by_severity": {"critical": 1, "high": 1, "medium": 2, "low": 1},
    })
    value["metrics"]["resurfaced"].update({
        "total": 1,
        "by_severity": {"critical": 0, "high": 1, "medium": 0, "low": 0},
    })
    value["customizations"] = {
        "network_tag_snapshots": [{
            "tag_uuid": "tag-rede-a",
            "category": "Rede",
            "network": "Rede A",
            "period_id": period_id,
            "assets": [{
                "asset_key": "cliente-exemplo:tenable_vm:asset-a",
                "asset_name": "",
                "ip_address": "",
                "critical": 1,
                "high": 2,
                "medium": 3,
                "low": 1,
                "total": total,
                "exploitable": 2,
            }],
        }],
    }
    return value


def _write_period(directory: Path, value: dict, keys: list[dict]) -> tuple[Path, Path]:
    dataset = directory / f"{value['period']['period_id']}.json"
    normalized = directory / f"{value['period']['period_id']}.jsonl"
    dataset.write_text(json.dumps(value), encoding="utf-8")
    normalized.write_text(
        "".join(json.dumps(item) + "\n" for item in keys), encoding="utf-8"
    )
    return dataset, normalized


def _tag_history_snapshot(
    period_id: str,
    *,
    total: int,
    tag_uuid: str = "tag-a",
) -> HistorySnapshot:
    year, month = (int(value) for value in period_id.split("-"))
    next_year = year + (1 if month == 12 else 0)
    next_month = 1 if month == 12 else month + 1
    compatibility = SnapshotCompatibility(
        client_id="cliente-exemplo",
        tenant_id="tenable-cloud-global",
        execution_type="AUTOMATIC_MONTHLY",
        period_mode="PREVIOUS_CALENDAR_MONTH",
        timezone="America/Fortaleza",
        metric_definition_version="report-definition-v1.2",
        scope_hash="scope-a",
    )
    return HistorySnapshot(
        snapshot_id=f"snapshot-{period_id}-{tag_uuid}",
        run_id=f"run-{period_id}",
        period_id=period_id,
        period_start_at=f"{year:04d}-{month:02d}-01T03:00:00Z",
        period_end_at=f"{next_year:04d}-{next_month:02d}-01T03:00:00Z",
        generated_at=f"{next_year:04d}-{next_month:02d}-01T03:00:00Z",
        compatibility=compatibility,
        summary={"non_mitigated": total},
        open_finding_keys=(),
        fixed_finding_keys=(),
        resurfaced_finding_keys=(),
        tag_snapshots=(
            {
                "tag_uuid": tag_uuid,
                "category_name": "Equipe",
                "value": "Infra",
                "summary": {
                    "non_mitigated": total,
                    "non_mitigated_by_severity": {
                        "critical": total,
                        "high": 0,
                        "medium": 0,
                        "low": 0,
                    },
                    "mitigated": 1,
                    "mitigated_by_severity": {
                        "critical": 0,
                        "high": 1,
                        "medium": 0,
                        "low": 0,
                    },
                    "new": 2,
                    "new_by_severity": {
                        "critical": 0,
                        "high": 1,
                        "medium": 1,
                        "low": 0,
                    },
                },
                "top_assets": [],
            },
        ),
    )


def test_legacy_network_tag_snapshots_load_as_generic_tag_snapshots() -> None:
    original = _tag_history_snapshot("2026-01", total=10)
    payload = original.to_dict()
    payload["network_tag_snapshots"] = payload.pop("tag_snapshots")

    snapshot = HistorySnapshot.from_dict(payload)

    assert snapshot.tag_snapshots[0]["tag_uuid"] == "tag-a"
    stored = snapshot.to_dict()
    assert "tag_snapshots" in stored
    assert "network_tag_snapshots" not in stored


def test_tag_year_history_marks_missing_month_without_zero() -> None:
    rows = tag_year_history(
        (
            _tag_history_snapshot("2025-12", total=99),
            _tag_history_snapshot("2026-01", total=10),
            _tag_history_snapshot("2026-03", total=8),
            _tag_history_snapshot("2026-03", total=70, tag_uuid="tag-b"),
        ),
        current=_tag_history_snapshot("2026-04", total=7),
        tag_uuid="tag-a",
    )

    assert [row["period_id"] for row in rows] == [
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
    ]
    assert rows[1] == {
        "period_id": "2026-02",
        "label": "Fevereiro/2026",
        "availability": "UNAVAILABLE",
    }
    assert rows[2]["non_mitigated"] == 8
    assert rows[3]["non_mitigated"] == 7


def test_prepare_history_enriches_each_tag_dataset_and_compacts_current_snapshot(
    tmp_path: Path,
) -> None:
    profile = load_client_profile(ROOT / "clients/examples/client-profile.json")
    data = _dataset(
        "2026-07",
        "2026-07-01T03:00:00Z",
        "2026-08-01T03:00:00Z",
        total=12,
    )
    dataset_path, findings_path = _write_period(
        tmp_path,
        data,
        [{"finding_key": "finding-july", "state": "OPEN", "last_found_at": "2026-07-15T12:00:00Z"}],
    )
    tag_data = deepcopy(data)
    tag_data["document_kind"] = "tag"
    tag_data["tag"] = {
        "tag_uuid": "tag-a",
        "category_uuid": "category-team",
        "category_name": "Equipe",
        "value": "Infra",
        "include_temporal_comparison": True,
    }
    tag_data["metrics"]["non_mitigated"]["total"] = 4
    tag_data["top_assets"] = [{"source_asset_id": "asset-a", "total": 4}]
    tag_path = tmp_path / "tag-a" / "report-dataset.json"
    tag_path.parent.mkdir()
    tag_path.write_text(json.dumps(tag_data), encoding="utf-8")

    prepared = prepare_dataset_history(
        profile=profile,
        dataset_path=dataset_path,
        normalized_findings_path=findings_path,
        output_path=tmp_path / "report-dataset-with-history.json",
        tag_dataset_paths={"tag-a": tag_path},
        registry=InMemoryReportRegistry(),
        repository=_RecordingSnapshotRepository(),
    )

    assert prepared.current.tag_snapshots[0]["summary"]["non_mitigated"] == 4
    enriched_path = prepared.tag_enriched_dataset_paths["tag-a"]
    enriched = json.loads(enriched_path.read_text(encoding="utf-8"))
    assert enriched["tag_history_status"] == "AVAILABLE"
    assert enriched["tag_history"][-1]["period_id"] == "2026-07"
    assert enriched["tag_history"][-1]["non_mitigated"] == 4


def test_two_compatible_months_publish_trends_and_same_tag_comparison() -> None:
    profile = load_client_profile(
        ROOT / "clients/examples/client-profile-intelligence-expanded.json"
    )
    with tempfile.TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        database = directory / "history.sqlite"
        june, june_findings = _write_period(
            directory,
            _dataset(
                "2026-06", "2026-06-01T03:00:00Z", "2026-07-01T03:00:00Z",
                total=10,
            ),
            [
                {"finding_key": "persistent", "state": "OPEN", "last_found_at": "2026-06-15T12:00:00Z"},
                {"finding_key": "corrected", "state": "OPEN", "last_found_at": "2026-06-20T12:00:00Z"},
            ],
        )
        july, july_findings = _write_period(
            directory,
            _dataset(
                "2026-07", "2026-07-01T03:00:00Z", "2026-08-01T03:00:00Z",
                total=12,
            ),
            [
                {"finding_key": "persistent", "state": "OPEN", "last_found_at": "2026-07-15T12:00:00Z"},
                {"finding_key": "new", "state": "OPEN", "last_found_at": "2026-07-20T12:00:00Z"},
                {"finding_key": "corrected", "state": "FIXED", "last_fixed_at": "2026-07-12T12:00:00Z"},
                {"finding_key": "resurfaced", "state": "REOPENED", "last_found_at": "2026-07-22T12:00:00Z", "resurfaced_at": "2026-07-22T12:00:00Z"},
            ],
        )
        first = publish_dataset_history(
            profile=profile,
            dataset_path=june,
            normalized_findings_path=june_findings,
            database_path=database,
            output_path=directory / "june-enriched.json",
        )
        assert first.history_status == "NO_COMPATIBLE_PREDECESSOR"
        second = publish_dataset_history(
            profile=profile,
            dataset_path=july,
            normalized_findings_path=july_findings,
            database_path=database,
            output_path=directory / "july-enriched.json",
            csv_path=directory / "history.csv",
        )
        assert second.predecessor is not None
        assert second.predecessor.period_id == "2026-06"
        result = json.loads(second.enriched_dataset_path.read_text(encoding="utf-8"))
        customizations = result["customizations"]
        assert [item["period_id"] for item in customizations["monthly_history"]] == [
            "2026-06", "2026-07"
        ]
        assert customizations["previous_period_overview"]["total"]["non_mitigated"] == 10
        assert customizations["finding_transitions"]["new"] == [
            fingerprint_finding_key("new").hex()
        ]
        assert customizations["finding_transitions"]["corrected"] == [
            fingerprint_finding_key("corrected").hex()
        ]
        assert customizations["finding_transitions"]["persistent"] == [
            fingerprint_finding_key("persistent").hex()
        ]
        assert customizations["network_comparisons"][0]["tag_uuid"] == "tag-rede-a"
        assert len(customizations["network_comparisons"][0]["periods"]) == 2


def test_history_accepts_compressed_normalized_findings() -> None:
    profile = load_client_profile(
        ROOT / "clients/examples/client-profile-intelligence-expanded.json"
    )
    with tempfile.TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        dataset = _dataset(
            "2026-07", "2026-07-01T03:00:00Z", "2026-08-01T03:00:00Z", total=1
        )
        dataset_path = directory / "dataset.json"
        dataset_path.write_text(json.dumps(dataset), encoding="utf-8")
        findings_path = directory / "findings.jsonl.gz"
        write_jsonl_gzip_exclusive(
            findings_path,
            [{
                "finding_key": "compressed-open",
                "state": "OPEN",
                "last_found_at": "2026-07-15T12:00:00Z",
            }],
        )

        result = publish_dataset_history(
            profile=profile,
            dataset_path=dataset_path,
            normalized_findings_path=findings_path,
            database_path=directory / "history.sqlite",
            output_path=directory / "enriched.json",
        )

        assert result.snapshot.open_finding_keys == (
            fingerprint_finding_key("compressed-open"),
        )


def test_incompatible_metric_version_is_not_used_as_predecessor() -> None:
    profile = load_client_profile(
        ROOT / "clients/examples/client-profile-intelligence-expanded.json"
    )
    with tempfile.TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        june_value = _dataset(
            "2026-06", "2026-06-01T03:00:00Z", "2026-07-01T03:00:00Z", total=10
        )
        july_value = _dataset(
            "2026-07", "2026-07-01T03:00:00Z", "2026-08-01T03:00:00Z", total=12
        )
        july_value["metric_definition_version"] = "report-definition-v9.9"
        june, june_findings = _write_period(directory, june_value, [])
        july, july_findings = _write_period(directory, july_value, [])
        publish_dataset_history(
            profile=profile,
            dataset_path=june,
            normalized_findings_path=june_findings,
            database_path=directory / "history.sqlite",
            output_path=directory / "june-enriched.json",
        )
        result = publish_dataset_history(
            profile=profile,
            dataset_path=july,
            normalized_findings_path=july_findings,
            database_path=directory / "history.sqlite",
            output_path=directory / "july-enriched.json",
        )
        assert result.predecessor is None
        data = json.loads(result.enriched_dataset_path.read_text(encoding="utf-8"))
        assert data["customizations"]["history_status"]["status"] == (
            "NO_COMPATIBLE_PREDECESSOR"
        )
        assert "previous_period_overview" not in data["customizations"]


def test_csv_round_trip_preserves_aggregated_history() -> None:
    profile = load_client_profile(
        ROOT / "clients/examples/client-profile-intelligence-expanded.json"
    )
    with tempfile.TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        dataset, findings = _write_period(
            directory,
            _dataset(
                "2026-06", "2026-06-01T03:00:00Z", "2026-07-01T03:00:00Z",
                total=10,
            ),
            [],
        )
        export = directory / "history.csv"
        publication = publish_dataset_history(
            profile=profile,
            dataset_path=dataset,
            normalized_findings_path=findings,
            database_path=directory / "source.sqlite",
            output_path=directory / "enriched.json",
            csv_path=export,
        )
        imported = import_history_csv(
            csv_path=export,
            database_path=directory / "target.sqlite",
        )
        assert len(imported) == 1
        assert imported[0].snapshot_id == publication.snapshot.snapshot_id
        assert imported[0].tag_snapshots == publication.snapshot.tag_snapshots
        repository = SQLiteSnapshotRepository(directory / "target.sqlite")
        rows = repository.compatible_snapshots(
            publication.snapshot.compatibility,
            before_period_end_at="2026-08-01T03:00:00Z",
        )
        assert rows[0].summary["non_mitigated"] == 10


def test_import_rejects_invalid_summary_json() -> None:
    with tempfile.TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        path = directory / "invalid.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("snapshot_id", "summary_json"))
            writer.writerow(("example", "not-json"))
        with pytest.raises(ValueError, match="summary_json invalido"):
            import_history_csv(
                csv_path=path,
                database_path=directory / "history.sqlite",
            )


def test_august_does_not_fall_back_to_june_when_july_main_is_missing() -> None:
    profile = load_client_profile(
        ROOT / "clients/examples/client-profile-intelligence-expanded.json"
    )
    registry = InMemoryReportRegistry()
    snapshots = _RecordingSnapshotRepository()
    with tempfile.TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        june, june_findings = _write_period(
            directory,
            _dataset(
                "2026-06", "2026-06-01T03:00:00Z", "2026-07-01T03:00:00Z",
                total=10,
            ),
            [],
        )
        june_prepared = prepare_dataset_history(
            profile=profile,
            dataset_path=june,
            normalized_findings_path=june_findings,
            output_path=directory / "june-enriched.json",
            registry=registry,
            repository=snapshots,
        )
        finalize_history_publication(
            june_prepared,
            snapshot_repository=snapshots,
            registry=registry,
            publication_validated=True,
            auto_promote=True,
        )
        august, august_findings = _write_period(
            directory,
            _dataset(
                "2026-08", "2026-08-01T03:00:00Z", "2026-09-01T03:00:00Z",
                total=14,
            ),
            [],
        )

        prepared = prepare_dataset_history(
            profile=profile,
            dataset_path=august,
            normalized_findings_path=august_findings,
            output_path=directory / "august-enriched.json",
            registry=registry,
            repository=snapshots,
        )

        assert prepared.predecessor is None
        assert prepared.history_status == "NO_IMMEDIATE_MAIN"
        enriched = json.loads(prepared.enriched_dataset_path.read_text(encoding="utf-8"))
        assert [row["period_id"] for row in enriched["customizations"]["monthly_history"]] == [
            "2026-06", "2026-08"
        ]


def test_snapshot_is_only_published_and_promoted_after_document_validation() -> None:
    profile = load_client_profile(
        ROOT / "clients/examples/client-profile-intelligence-expanded.json"
    )
    registry = InMemoryReportRegistry()
    snapshots = _RecordingSnapshotRepository()
    with tempfile.TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        dataset, findings = _write_period(
            directory,
            _dataset(
                "2026-07", "2026-07-01T03:00:00Z", "2026-08-01T03:00:00Z",
                total=12,
            ),
            [],
        )
        prepared = prepare_dataset_history(
            profile=profile,
            dataset_path=dataset,
            normalized_findings_path=findings,
            output_path=directory / "july-enriched.json",
            registry=registry,
            repository=snapshots,
        )
        assert snapshots.published == []
        assert registry.get_main(prepared.reference_key) is None

        with pytest.raises(ValueError, match="Publicacao invalida"):
            finalize_history_publication(
                prepared,
                snapshot_repository=snapshots,
                registry=registry,
                publication_validated=False,
                auto_promote=True,
            )
        assert snapshots.published == []
        assert registry.get_main(prepared.reference_key) is None

        finalize_history_publication(
            prepared,
            snapshot_repository=snapshots,
            registry=registry,
            publication_validated=True,
            auto_promote=True,
        )
        assert [item.run_id for item in snapshots.published] == ["run-2026-07"]
        assert registry.get_main(prepared.reference_key).run_id == "run-2026-07"


def test_previous_main_derives_only_top_positive_vulnerability_changes() -> None:
    profile = load_client_profile(
        ROOT / "clients/examples/client-profile-intelligence-expanded.json"
    )
    registry = InMemoryReportRegistry()
    snapshots = _RecordingSnapshotRepository()
    with tempfile.TemporaryDirectory() as directory_name:
        directory = Path(directory_name)
        june, june_findings = _write_period(
            directory,
            _dataset(
                "2026-06", "2026-06-01T03:00:00Z", "2026-07-01T03:00:00Z",
                total=50,
            ),
            [
                *(
                    {"finding_key": f"a-{index}", "state": "OPEN", "last_found_at": "2026-06-15T12:00:00Z", "plugin_id": 1001, "plugin_name": "Plugin A"}
                    for index in range(20)
                ),
                *(
                    {"finding_key": f"b-{index}", "state": "OPEN", "last_found_at": "2026-06-15T12:00:00Z", "plugin_id": 1002, "plugin_name": "Plugin B"}
                    for index in range(30)
                ),
            ],
        )
        first = prepare_dataset_history(
            profile=profile,
            dataset_path=june,
            normalized_findings_path=june_findings,
            output_path=directory / "june-enriched.json",
            registry=registry,
            repository=snapshots,
        )
        finalize_history_publication(
            first,
            snapshot_repository=snapshots,
            registry=registry,
            publication_validated=True,
            auto_promote=True,
        )
        july, july_findings = _write_period(
            directory,
            _dataset(
                "2026-07", "2026-07-01T03:00:00Z", "2026-08-01T03:00:00Z",
                total=60,
            ),
            [
                *(
                    {"finding_key": f"a2-{index}", "state": "OPEN", "last_found_at": "2026-07-15T12:00:00Z", "plugin_id": 1001, "plugin_name": "Plugin A"}
                    for index in range(50)
                ),
                *(
                    {"finding_key": f"b2-{index}", "state": "OPEN", "last_found_at": "2026-07-15T12:00:00Z", "plugin_id": 1002, "plugin_name": "Plugin B"}
                    for index in range(10)
                ),
            ],
        )
        prepared = prepare_dataset_history(
            profile=profile,
            dataset_path=july,
            normalized_findings_path=july_findings,
            output_path=directory / "july-enriched.json",
            registry=registry,
            repository=snapshots,
        )

        enriched = json.loads(prepared.enriched_dataset_path.read_text(encoding="utf-8"))
        assert enriched["customizations"]["vulnerability_evolution"] == [
            {"plugin_id": 1001, "label": "Plugin A", "change": 30}
        ]
        assert enriched["customizations"]["vulnerability_evolution_status"] == "AVAILABLE"


def test_two_months_compare_after_first_month_transients_are_removed(
    tmp_path: Path,
) -> None:
    profile = load_client_profile(
        ROOT / "clients/examples/client-profile-intelligence-expanded.json"
    )
    registry = InMemoryReportRegistry()
    snapshots = _RecordingSnapshotRepository()

    def create_period(
        period_id: str,
        start_at: str,
        end_at: str,
        total: int,
    ) -> tuple[str, Path, Path, Path]:
        value = _dataset(period_id, start_at, end_at, total=total)
        run_id = str(value["run_id"])
        dataset_dir = (
            tmp_path / "report-datasets" / profile.client_id / run_id / period_id
        )
        dataset_dir.mkdir(parents=True)
        dataset_path = dataset_dir / "report-dataset.json"
        dataset_path.write_text(json.dumps(value), encoding="utf-8")
        normalized_dir = tmp_path / "normalized" / profile.client_id / run_id
        normalized_dir.mkdir(parents=True)
        findings_path = normalized_dir / "findings.jsonl.gz"
        write_jsonl_gzip_exclusive(
            findings_path,
            [{
                "finding_key": f"finding-{period_id}",
                "state": "OPEN",
                "last_found_at": start_at[:8] + "15T12:00:00Z",
            }],
        )
        for category in ("raw", "snapshots"):
            transient = tmp_path / category / profile.client_id / run_id
            transient.mkdir(parents=True)
            (transient / "fixture.bin").write_bytes(b"temporary")
        report_dir = tmp_path / "reports" / profile.client_id / run_id
        report_dir.mkdir(parents=True)
        document = report_dir / f"{period_id}.docx"
        document.write_bytes(b"published-document-fixture")
        return run_id, dataset_path, findings_path, document

    july_run, july_dataset, july_findings, july_docx = create_period(
        "2026-07", "2026-07-01T03:00:00Z", "2026-08-01T03:00:00Z", 12
    )
    july = prepare_dataset_history(
        profile=profile,
        dataset_path=july_dataset,
        normalized_findings_path=july_findings,
        output_path=july_dataset.parent / "report-dataset-with-history.json",
        registry=registry,
        repository=snapshots,
    )
    finalize_history_publication(
        july,
        snapshot_repository=snapshots,
        registry=registry,
        publication_validated=True,
        auto_promote=True,
    )
    cleanup = plan_published_run_cleanup(
        scoped_output_root=tmp_path,
        client_id=profile.client_id,
        run_id=july_run,
        publication_confirmed=True,
        history_confirmed=True,
    )
    apply_cleanup_plan(scoped_output_root=tmp_path, candidates=cleanup.candidates)

    assert july_docx.is_file()
    for category in ("raw", "snapshots", "normalized", "report-datasets"):
        assert not (tmp_path / category / profile.client_id / july_run).exists()

    _, august_dataset, august_findings, _ = create_period(
        "2026-08", "2026-08-01T03:00:00Z", "2026-09-01T03:00:00Z", 15
    )
    august = prepare_dataset_history(
        profile=profile,
        dataset_path=august_dataset,
        normalized_findings_path=august_findings,
        output_path=august_dataset.parent / "report-dataset-with-history.json",
        registry=registry,
        repository=snapshots,
    )
    enriched = json.loads(august.enriched_dataset_path.read_text(encoding="utf-8"))

    assert august.history_status == "COMPATIBLE_PREDECESSOR"
    assert august.predecessor is not None
    assert august.predecessor.period_id == "2026-07"
    assert enriched["customizations"]["monthly_history"][0]["period_id"] == "2026-07"
