from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from tenable_reports.application.deployment_transfer import (
    DeploymentDatabaseSnapshot,
    DeploymentTransferError,
    apply_deployment_import,
    create_deployment_export,
    plan_deployment_export,
    plan_deployment_import,
)


def _project(tmp_path: Path) -> tuple[Path, DeploymentDatabaseSnapshot]:
    root = tmp_path / "project"
    (root / "orchestration").mkdir(parents=True)
    (root / "credentials").mkdir()
    (root / "data" / "published").mkdir(parents=True)
    (root / "analysis_artifacts" / "staging").mkdir(parents=True)
    (root / "orchestration" / "clients.json").write_text(
        '{"clients": []}\n',
        encoding="utf-8",
    )
    (root / "orchestration" / "analysts.json").write_text(
        '{"analysts": []}\n',
        encoding="utf-8",
    )
    (root / "orchestration" / "document-control.json").write_text(
        '{"preparation": {}}\n',
        encoding="utf-8",
    )
    (root / "credentials" / "database.env").write_text(
        "TENABLE_REPORTS_DB_PASSWORD=must-not-leave\n",
        encoding="utf-8",
    )
    manifest = root / "data" / "published" / "manifest.json"
    document = root / "data" / "published" / "report.docx"
    manifest.write_text('{"status": "COMPLETE"}\n', encoding="utf-8")
    document.write_bytes(b"sanitized docx fixture")
    snapshot = DeploymentDatabaseSnapshot(
        schema_versions=("0001_initial.sql", "0002_report_main.sql"),
        main_references=("reference-a:run-a",),
        document_paths=(manifest, document),
    )
    return root, snapshot


class FakeDatabaseTransfer:
    def __init__(
        self,
        snapshot: DeploymentDatabaseSnapshot,
        *,
        restored_snapshot: DeploymentDatabaseSnapshot | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.restored_snapshot = restored_snapshot or snapshot
        self.calls: list[tuple[str, str]] = []

    def create_dump(self, destination: Path) -> None:
        self.calls.append(("dump", destination.name))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"sanitized postgresql dump")

    def restore_to_staging(self, dump_path: Path, staging_name: str) -> None:
        self.calls.append(("restore", staging_name))
        assert dump_path.is_file()

    def inspect_staging(self, staging_name: str) -> DeploymentDatabaseSnapshot:
        self.calls.append(("inspect", staging_name))
        return self.restored_snapshot

    def rebase_staging_paths(
        self,
        staging_name: str,
        replacements: dict[str, str],
    ) -> None:
        self.calls.append(("rebase", staging_name))
        assert replacements
        assert all(Path(path).is_absolute() for path in replacements.values())

    def promote_staging(self, staging_name: str) -> str:
        self.calls.append(("promote", staging_name))
        return "rollback-token"

    def rollback_promotion(self, token: str) -> None:
        self.calls.append(("rollback", token))

    def finalize_promotion(self, token: str) -> None:
        self.calls.append(("finalize", token))

    def discard_staging(self, staging_name: str) -> None:
        self.calls.append(("discard", staging_name))


def _export(tmp_path: Path) -> tuple[Path, Path, DeploymentDatabaseSnapshot]:
    project, snapshot = _project(tmp_path)
    archive = tmp_path / "transfer.zip"
    plan = plan_deployment_export(
        project_root=project,
        destination=archive,
        database_snapshot=snapshot,
        source_identity="installation-a",
        runtime_versions={"application": "0.1.0", "postgresql": "18"},
    )
    create_deployment_export(plan, database=FakeDatabaseTransfer(snapshot))
    return project, archive, snapshot


def test_export_inventory_uses_only_authorized_files_and_short_archive_names(
    tmp_path: Path,
) -> None:
    project, snapshot = _project(tmp_path)
    staging = project / "analysis_artifacts" / "staging" / "checkpoint.json"
    staging.write_text('{"status": "RECOVERABLE"}\n', encoding="utf-8")

    plan = plan_deployment_export(
        project_root=project,
        destination=tmp_path / "transfer.zip",
        database_snapshot=snapshot,
        source_identity="installation-a",
        runtime_versions={"application": "0.1.0"},
        include_staging=True,
    )

    logical_paths = {item.logical_path for item in plan.files}
    assert "orchestration/clients.json" in logical_paths
    assert "data/published/report.docx" in logical_paths
    assert "analysis_artifacts/staging/checkpoint.json" in logical_paths
    assert all(item.archive_path.startswith("f/") for item in plan.files)
    assert all(len(item.archive_path) <= 24 for item in plan.files)
    assert not any("credentials" in path for path in logical_paths)
    assert plan.reprovision_required == (
        "TENABLE_API_CREDENTIALS",
        "POSTGRESQL_CREDENTIALS",
    )


def test_export_refuses_missing_catalog_document_before_dump(tmp_path: Path) -> None:
    project, snapshot = _project(tmp_path)
    snapshot.document_paths[-1].unlink()

    with pytest.raises(DeploymentTransferError, match="DOCUMENT_MISSING"):
        plan_deployment_export(
            project_root=project,
            destination=tmp_path / "transfer.zip",
            database_snapshot=snapshot,
            source_identity="installation-a",
            runtime_versions={},
        )


def test_cataloged_document_outside_project_is_relocated_without_exposing_source_path(
    tmp_path: Path,
) -> None:
    project, snapshot = _project(tmp_path)
    external = tmp_path / "published reports" / "monthly-report.docx"
    external.parent.mkdir()
    external.write_bytes(b"external sanitized document")
    snapshot = DeploymentDatabaseSnapshot(
        schema_versions=snapshot.schema_versions,
        main_references=snapshot.main_references,
        document_paths=(*snapshot.document_paths, external),
    )

    plan = plan_deployment_export(
        project_root=project,
        destination=tmp_path / "transfer.zip",
        database_snapshot=snapshot,
        source_identity="installation-a",
        runtime_versions={},
    )

    relocated = [item for item in plan.files if item.source_path == external.resolve()]
    assert len(relocated) == 1
    assert relocated[0].logical_path.startswith("data/transferred/")
    assert str(external.parent) not in relocated[0].logical_path


def test_export_manifest_has_hashes_main_references_and_no_secret(
    tmp_path: Path,
) -> None:
    _, archive, snapshot = _export(tmp_path)

    with zipfile.ZipFile(archive) as package:
        names = package.namelist()
        manifest = json.loads(package.read("deployment-manifest.json"))
        unpacked = b"\n".join(package.read(name) for name in names)

    assert names.count("deployment-manifest.json") == 1
    assert manifest["schema_version"] == 1
    assert manifest["main_references"] == list(snapshot.main_references)
    assert manifest["database_dump"]["sha256"]
    assert all(item["sha256"] for item in manifest["files"])
    assert all(item["source_path_token"] for item in manifest["files"])
    assert str(tmp_path) not in json.dumps(manifest)
    assert b"must-not-leave" not in unpacked
    assert manifest["reprovision_required"] == [
        "TENABLE_API_CREDENTIALS",
        "POSTGRESQL_CREDENTIALS",
    ]


def test_import_dry_run_rejects_newer_schema_without_writing(tmp_path: Path) -> None:
    _, archive, _ = _export(tmp_path)
    rewritten = tmp_path / "newer.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(rewritten, "w") as target:
        for name in source.namelist():
            payload = source.read(name)
            if name == "deployment-manifest.json":
                manifest = json.loads(payload)
                manifest["schema_version"] = 999
                payload = json.dumps(manifest).encode("utf-8")
            target.writestr(name, payload)
    destination = tmp_path / "destination"

    with pytest.raises(DeploymentTransferError, match="TRANSFER_SCHEMA_UNSUPPORTED"):
        plan_deployment_import(
            archive=rewritten,
            project_root=destination,
            destination_identity=None,
        )

    assert not destination.exists()


def test_import_dry_run_rejects_database_schema_newer_than_destination_code(
    tmp_path: Path,
) -> None:
    _, archive, _ = _export(tmp_path)

    with pytest.raises(DeploymentTransferError, match="DATABASE_SCHEMA_UNSUPPORTED"):
        plan_deployment_import(
            archive=archive,
            project_root=tmp_path / "destination",
            destination_identity=None,
            supported_schema_versions=("0001_initial.sql",),
        )


def test_import_dry_run_rejects_tampering_path_traversal_space_and_identity(
    tmp_path: Path,
) -> None:
    _, archive, _ = _export(tmp_path)
    with zipfile.ZipFile(archive, "a") as package:
        package.writestr("../escape.txt", b"unsafe")
    with pytest.raises(DeploymentTransferError, match="TRANSFER_PATH_UNSAFE"):
        plan_deployment_import(
            archive=archive,
            project_root=tmp_path / "destination",
            destination_identity=None,
        )

    _, clean_archive, _ = _export(tmp_path / "second")
    with pytest.raises(DeploymentTransferError, match="TRANSFER_IDENTITY_CONFLICT"):
        plan_deployment_import(
            archive=clean_archive,
            project_root=tmp_path / "destination",
            destination_identity="installation-b",
        )
    with pytest.raises(DeploymentTransferError, match="TRANSFER_SPACE_INSUFFICIENT"):
        plan_deployment_import(
            archive=clean_archive,
            project_root=tmp_path / "destination",
            destination_identity=None,
            available_bytes=lambda _path: 1,
        )


def test_import_dry_run_detects_changed_payload_and_missing_main_document(
    tmp_path: Path,
) -> None:
    _, archive, _ = _export(tmp_path)
    changed = tmp_path / "changed.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(changed, "w") as target:
        for name in source.namelist():
            payload = source.read(name)
            if name.startswith("f/"):
                payload = bytes([payload[0] ^ 1]) + payload[1:]
            target.writestr(name, payload)
    with pytest.raises(DeploymentTransferError, match="TRANSFER_FILE_HASH_MISMATCH"):
        plan_deployment_import(
            archive=changed,
            project_root=tmp_path / "destination",
            destination_identity=None,
        )

    missing = tmp_path / "missing-main.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(missing, "w") as target:
        manifest = json.loads(source.read("deployment-manifest.json"))
        manifest["files"] = [
            item
            for item in manifest["files"]
            if item["logical_path"] != "data/published/report.docx"
        ]
        target.writestr("deployment-manifest.json", json.dumps(manifest))
        declared = {item["archive_path"] for item in manifest["files"]}
        declared.add(manifest["database_dump"]["archive_path"])
        for name in source.namelist():
            if name in declared:
                target.writestr(name, source.read(name))
    with pytest.raises(DeploymentTransferError, match="MAIN_DOCUMENT_MISSING"):
        plan_deployment_import(
            archive=missing,
            project_root=tmp_path / "destination",
            destination_identity=None,
        )


def test_apply_requires_exact_confirmation_and_preserves_main_snapshot(
    tmp_path: Path,
) -> None:
    _, archive, snapshot = _export(tmp_path)
    destination = tmp_path / "destination"
    existing = destination / "data" / "published" / "report.docx"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old")
    plan = plan_deployment_import(
        archive=archive,
        project_root=destination,
        destination_identity=None,
    )
    database = FakeDatabaseTransfer(snapshot)

    with pytest.raises(DeploymentTransferError, match="TRANSFER_CONFIRMATION_REQUIRED"):
        apply_deployment_import(
            plan,
            database=database,
            confirmation="wrong",
        )
    assert existing.read_bytes() == b"old"

    result = apply_deployment_import(
        plan,
        database=database,
        confirmation=plan.confirmation_phrase,
    )

    assert result.status == "APPLIED"
    assert existing.read_bytes() == b"sanitized docx fixture"
    assert (destination / "data" / ".deployment-identity").read_text(
        encoding="utf-8"
    ).strip() == "installation-a"
    assert [name for name, _ in database.calls][-4:] == [
        "rebase",
        "inspect",
        "promote",
        "finalize",
    ]


def test_failed_staging_validation_does_not_change_current_files(tmp_path: Path) -> None:
    _, archive, snapshot = _export(tmp_path)
    destination = tmp_path / "destination"
    existing = destination / "data" / "published" / "report.docx"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"old")
    plan = plan_deployment_import(
        archive=archive,
        project_root=destination,
        destination_identity=None,
    )
    invalid_snapshot = DeploymentDatabaseSnapshot(
        schema_versions=snapshot.schema_versions,
        main_references=("changed-reference:changed-run",),
        document_paths=snapshot.document_paths,
    )
    database = FakeDatabaseTransfer(snapshot, restored_snapshot=invalid_snapshot)

    with pytest.raises(DeploymentTransferError, match="MAIN_REFERENCE_MISMATCH"):
        apply_deployment_import(
            plan,
            database=database,
            confirmation=plan.confirmation_phrase,
        )

    assert existing.read_bytes() == b"old"
    assert not any(name == "promote" for name, _ in database.calls)
    assert database.calls[-1][0] == "discard"
