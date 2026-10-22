"""Controlled, secret-free transfer of one durable application installation."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Protocol, Sequence
from uuid import uuid4


TRANSFER_SCHEMA_VERSION = 1
_CONFIGURATION_FILES = (
    "orchestration/clients.json",
    "orchestration/analysts.json",
    "orchestration/document-control.json",
)
_ALLOWED_LOGICAL_ROOTS = {
    "analysis_artifacts",
    "data",
    "orchestration",
    "outputs",
}
_REPROVISION_REQUIRED = (
    "TENABLE_API_CREDENTIALS",
    "POSTGRESQL_CREDENTIALS",
)


class DeploymentTransferError(RuntimeError):
    """Stable, sanitized transfer failure."""

    def __init__(self, code: str, *, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}{f' ({detail})' if detail else ''}")


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_zip_entry(package: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with package.open(name) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(raw: str, *, code: str = "TRANSFER_PATH_UNSAFE") -> Path:
    pure = PurePosixPath(str(raw))
    if pure.is_absolute() or not pure.parts or ".." in pure.parts:
        raise DeploymentTransferError(code)
    if any(part in {"", "."} for part in pure.parts):
        raise DeploymentTransferError(code)
    return Path(*pure.parts)


def _relative_to_project(path: Path, project_root: Path) -> Path:
    try:
        relative = path.resolve().relative_to(project_root.resolve())
    except ValueError as exc:
        raise DeploymentTransferError("DOCUMENT_OUTSIDE_PROJECT") from exc
    if not relative.parts or relative.parts[0] not in _ALLOWED_LOGICAL_ROOTS:
        raise DeploymentTransferError("DOCUMENT_OUTSIDE_AUTHORIZED_ROOT")
    return relative


def _logical_document_path(path: Path, project_root: Path) -> Path:
    try:
        return _relative_to_project(path, project_root)
    except DeploymentTransferError as exc:
        if exc.code != "DOCUMENT_OUTSIDE_PROJECT":
            raise
    source = path.resolve()
    token = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:16]
    safe_name = "".join(
        character if character.isalnum() or character in {" ", ".", "-", "_"}
        else "_"
        for character in source.name
    ).strip(" .")
    if not safe_name:
        safe_name = "document.bin"
    return Path("data") / "transferred" / token / safe_name[:120]


def _nearest_existing(path: Path) -> Path:
    current = path.resolve()
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


@dataclass(frozen=True, slots=True)
class DeploymentDatabaseSnapshot:
    schema_versions: tuple[str, ...]
    main_references: tuple[str, ...]
    document_paths: tuple[Path, ...]


class DeploymentDatabaseTransfer(Protocol):
    def create_dump(self, destination: Path) -> None: ...

    def restore_to_staging(self, dump_path: Path, staging_name: str) -> None: ...

    def rebase_staging_paths(
        self,
        staging_name: str,
        replacements: Mapping[str, str],
    ) -> None: ...

    def inspect_staging(self, staging_name: str) -> DeploymentDatabaseSnapshot: ...

    def promote_staging(self, staging_name: str) -> str: ...

    def rollback_promotion(self, token: str) -> None: ...

    def finalize_promotion(self, token: str) -> None: ...

    def discard_staging(self, staging_name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class DeploymentTransferFile:
    source_path: Path
    logical_path: str
    archive_path: str
    size: int
    sha256: str
    source_path_token: str

    def manifest_dict(self) -> dict[str, object]:
        return {
            "logical_path": self.logical_path,
            "archive_path": self.archive_path,
            "size": self.size,
            "sha256": self.sha256,
            "source_path_token": self.source_path_token,
        }


@dataclass(frozen=True, slots=True)
class DeploymentTransferManifest:
    schema_version: int
    transfer_id: str
    created_at: str
    source_identity: str
    runtime_versions: Mapping[str, str]
    schema_versions: tuple[str, ...]
    main_references: tuple[str, ...]
    main_documents: tuple[str, ...]
    database_dump: Mapping[str, object]
    files: tuple[Mapping[str, object], ...]
    reprovision_required: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "transfer_id": self.transfer_id,
            "created_at": self.created_at,
            "source_identity": self.source_identity,
            "runtime_versions": dict(self.runtime_versions),
            "schema_versions": list(self.schema_versions),
            "main_references": list(self.main_references),
            "main_documents": list(self.main_documents),
            "database_dump": dict(self.database_dump),
            "files": [dict(item) for item in self.files],
            "reprovision_required": list(self.reprovision_required),
        }


@dataclass(frozen=True, slots=True)
class DeploymentExportPlan:
    project_root: Path
    destination: Path
    database_snapshot: DeploymentDatabaseSnapshot
    source_identity: str
    runtime_versions: Mapping[str, str]
    files: tuple[DeploymentTransferFile, ...]
    main_documents: tuple[str, ...]
    reprovision_required: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeploymentExportResult:
    status: str
    destination: Path
    transfer_id: str
    file_count: int


@dataclass(frozen=True, slots=True)
class DeploymentImportPlan:
    archive: Path
    project_root: Path
    manifest: DeploymentTransferManifest
    required_bytes: int

    @property
    def confirmation_phrase(self) -> str:
        return f"IMPORT {self.manifest.transfer_id}"


@dataclass(frozen=True, slots=True)
class DeploymentImportResult:
    status: str
    transfer_id: str
    imported_files: int


def _transfer_files(
    entries: Sequence[tuple[Path, Path]],
) -> tuple[DeploymentTransferFile, ...]:
    unique: dict[str, Path] = {}
    for relative, path in entries:
        if path.is_symlink():
            raise DeploymentTransferError("TRANSFER_SYMLINK_REJECTED")
        if not path.is_file():
            raise DeploymentTransferError("DOCUMENT_MISSING")
        if relative.parts[0] not in _ALLOWED_LOGICAL_ROOTS:
            raise DeploymentTransferError("DOCUMENT_OUTSIDE_AUTHORIZED_ROOT")
        unique[relative.as_posix()] = path.resolve()
    result: list[DeploymentTransferFile] = []
    for index, (logical, source) in enumerate(sorted(unique.items()), start=1):
        suffix = source.suffix.casefold()
        if len(suffix) > 8 or any(not char.isalnum() and char != "." for char in suffix):
            suffix = ".bin"
        result.append(
            DeploymentTransferFile(
                source_path=source,
                logical_path=logical,
                archive_path=f"f/{index:06d}{suffix}",
                size=source.stat().st_size,
                sha256=_sha256_path(source),
                source_path_token=hashlib.sha256(
                    str(source).encode("utf-8")
                ).hexdigest(),
            )
        )
    return tuple(result)


def plan_deployment_export(
    *,
    project_root: str | Path,
    destination: str | Path,
    database_snapshot: DeploymentDatabaseSnapshot,
    source_identity: str,
    runtime_versions: Mapping[str, str],
    include_staging: bool = False,
) -> DeploymentExportPlan:
    root = Path(project_root).resolve()
    target = Path(destination).resolve()
    if not root.is_dir():
        raise DeploymentTransferError("PROJECT_ROOT_MISSING")
    try:
        target.relative_to(root)
    except ValueError:
        pass
    else:
        raise DeploymentTransferError("EXPORT_DESTINATION_INSIDE_PROJECT")
    if target.exists():
        raise FileExistsError(target.name)
    identity = str(source_identity).strip()
    if not identity or len(identity) > 128 or not all(
        character.isalnum() or character in {"-", "_", "."}
        for character in identity
    ):
        raise DeploymentTransferError("TRANSFER_IDENTITY_INVALID")

    entries: list[tuple[Path, Path]] = []
    for relative in _CONFIGURATION_FILES:
        candidate = root / relative
        if candidate.is_file():
            entries.append((Path(relative), candidate))
    document_paths: list[tuple[Path, Path]] = []
    for raw in database_snapshot.document_paths:
        document = Path(raw)
        if not document.is_file():
            raise DeploymentTransferError("DOCUMENT_MISSING")
        logical = _logical_document_path(document, root)
        document_paths.append((logical, document))
        entries.append((logical, document))
    if include_staging:
        staging_root = root / "analysis_artifacts"
        if staging_root.is_dir():
            entries.extend(
                (_relative_to_project(path, root), path)
                for path in staging_root.rglob("*")
                if path.is_file()
            )
    files = _transfer_files(entries)
    main_documents = tuple(
        sorted(logical.as_posix() for logical, _path in document_paths)
    )
    return DeploymentExportPlan(
        project_root=root,
        destination=target,
        database_snapshot=database_snapshot,
        source_identity=identity,
        runtime_versions={str(key): str(value) for key, value in runtime_versions.items()},
        files=files,
        main_documents=main_documents,
        reprovision_required=_REPROVISION_REQUIRED,
    )


def create_deployment_export(
    plan: DeploymentExportPlan,
    *,
    database: DeploymentDatabaseTransfer,
) -> DeploymentExportResult:
    if plan.destination.exists():
        raise FileExistsError(plan.destination.name)
    plan.destination.parent.mkdir(parents=True, exist_ok=True)
    transfer_id = uuid4().hex
    created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with tempfile.TemporaryDirectory(
        prefix=".deployment-export-",
        dir=plan.destination.parent,
    ) as directory_name:
        workspace = Path(directory_name)
        dump = workspace / "database.dump"
        database.create_dump(dump)
        if not dump.is_file():
            raise DeploymentTransferError("DATABASE_DUMP_MISSING")
        database_entry = {
            "archive_path": "db/database.dump",
            "size": dump.stat().st_size,
            "sha256": _sha256_path(dump),
        }
        for item in plan.files:
            if (
                not item.source_path.is_file()
                or item.source_path.stat().st_size != item.size
                or _sha256_path(item.source_path) != item.sha256
            ):
                raise DeploymentTransferError("SOURCE_FILE_CHANGED", detail=item.logical_path)
        manifest = DeploymentTransferManifest(
            schema_version=TRANSFER_SCHEMA_VERSION,
            transfer_id=transfer_id,
            created_at=created_at,
            source_identity=plan.source_identity,
            runtime_versions=plan.runtime_versions,
            schema_versions=plan.database_snapshot.schema_versions,
            main_references=plan.database_snapshot.main_references,
            main_documents=plan.main_documents,
            database_dump=database_entry,
            files=tuple(item.manifest_dict() for item in plan.files),
            reprovision_required=plan.reprovision_required,
        )
        temporary_archive = workspace / "transfer.zip"
        with zipfile.ZipFile(
            temporary_archive,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as package:
            package.writestr(
                "deployment-manifest.json",
                json.dumps(
                    manifest.to_dict(),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8"),
            )
            package.write(dump, "db/database.dump")
            for item in plan.files:
                package.write(item.source_path, item.archive_path)
        os.replace(temporary_archive, plan.destination)
    return DeploymentExportResult(
        status="COMPLETE",
        destination=plan.destination,
        transfer_id=transfer_id,
        file_count=len(plan.files),
    )


def _manifest_from_dict(payload: Mapping[str, object]) -> DeploymentTransferManifest:
    try:
        schema_version = int(payload["schema_version"])
        transfer_id = str(payload["transfer_id"])
        source_identity = str(payload["source_identity"])
        database_dump = dict(payload["database_dump"])  # type: ignore[arg-type]
        files = tuple(dict(item) for item in payload["files"])  # type: ignore[union-attr]
    except (KeyError, TypeError, ValueError) as exc:
        raise DeploymentTransferError("TRANSFER_MANIFEST_INVALID") from exc
    if schema_version != TRANSFER_SCHEMA_VERSION:
        raise DeploymentTransferError("TRANSFER_SCHEMA_UNSUPPORTED")
    if not transfer_id or not source_identity:
        raise DeploymentTransferError("TRANSFER_MANIFEST_INVALID")
    runtime = payload.get("runtime_versions")
    runtime_versions = dict(runtime) if isinstance(runtime, Mapping) else {}
    return DeploymentTransferManifest(
        schema_version=schema_version,
        transfer_id=transfer_id,
        created_at=str(payload.get("created_at") or ""),
        source_identity=source_identity,
        runtime_versions={str(key): str(value) for key, value in runtime_versions.items()},
        schema_versions=tuple(str(item) for item in payload.get("schema_versions", ())),
        main_references=tuple(str(item) for item in payload.get("main_references", ())),
        main_documents=tuple(str(item) for item in payload.get("main_documents", ())),
        database_dump=database_dump,
        files=files,
        reprovision_required=tuple(
            str(item) for item in payload.get("reprovision_required", ())
        ),
    )


def _read_and_validate_archive(
    archive: Path,
) -> tuple[DeploymentTransferManifest, int]:
    if not archive.is_file():
        raise DeploymentTransferError("TRANSFER_ARCHIVE_MISSING")
    try:
        with zipfile.ZipFile(archive) as package:
            infos = package.infolist()
            names = [item.filename for item in infos]
            if len(names) != len(set(names)):
                raise DeploymentTransferError("TRANSFER_PATH_DUPLICATED")
            for name in names:
                _safe_relative(name)
            try:
                raw_manifest = json.loads(package.read("deployment-manifest.json"))
            except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise DeploymentTransferError("TRANSFER_MANIFEST_INVALID") from exc
            if not isinstance(raw_manifest, Mapping):
                raise DeploymentTransferError("TRANSFER_MANIFEST_INVALID")
            manifest = _manifest_from_dict(raw_manifest)
            declared: dict[str, tuple[int, str]] = {}
            logical_paths: set[str] = set()
            for raw in (*manifest.files, manifest.database_dump):
                try:
                    archive_path = _safe_relative(str(raw["archive_path"])).as_posix()
                    size = int(raw["size"])
                    sha256 = str(raw["sha256"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise DeploymentTransferError("TRANSFER_MANIFEST_INVALID") from exc
                if archive_path in declared:
                    raise DeploymentTransferError("TRANSFER_PATH_DUPLICATED")
                if size < 0 or len(sha256) != 64:
                    raise DeploymentTransferError("TRANSFER_MANIFEST_INVALID")
                declared[archive_path] = (size, sha256)
                if raw is not manifest.database_dump:
                    logical = _safe_relative(str(raw.get("logical_path") or ""))
                    source_path_token = str(raw.get("source_path_token") or "")
                    if len(source_path_token) != 64:
                        raise DeploymentTransferError("TRANSFER_MANIFEST_INVALID")
                    if logical.parts[0] not in _ALLOWED_LOGICAL_ROOTS:
                        raise DeploymentTransferError("TRANSFER_PATH_UNSAFE")
                    logical_name = logical.as_posix()
                    if logical_name in logical_paths:
                        raise DeploymentTransferError("TRANSFER_PATH_DUPLICATED")
                    logical_paths.add(logical_name)
            expected_names = {"deployment-manifest.json", *declared}
            if set(names) != expected_names:
                unexpected = set(names) - expected_names
                if unexpected:
                    raise DeploymentTransferError("TRANSFER_PATH_UNSAFE")
                raise DeploymentTransferError("TRANSFER_FILE_MISSING")
            info_by_name = {item.filename: item for item in infos}
            total = 0
            for name, (size, expected_hash) in declared.items():
                if info_by_name[name].is_dir() or info_by_name[name].file_size != size:
                    raise DeploymentTransferError("TRANSFER_FILE_SIZE_MISMATCH")
                if _sha256_zip_entry(package, name) != expected_hash:
                    raise DeploymentTransferError("TRANSFER_FILE_HASH_MISMATCH")
                total += size
            if not set(manifest.main_documents).issubset(logical_paths):
                raise DeploymentTransferError("MAIN_DOCUMENT_MISSING")
    except zipfile.BadZipFile as exc:
        raise DeploymentTransferError("TRANSFER_ARCHIVE_INVALID") from exc
    return manifest, total


def plan_deployment_import(
    *,
    archive: str | Path,
    project_root: str | Path,
    destination_identity: str | None,
    available_bytes: Callable[[Path], int] | None = None,
    supported_schema_versions: Sequence[str] | None = None,
) -> DeploymentImportPlan:
    source = Path(archive).resolve()
    root = Path(project_root).resolve()
    manifest, payload_bytes = _read_and_validate_archive(source)
    if destination_identity and destination_identity != manifest.source_identity:
        raise DeploymentTransferError("TRANSFER_IDENTITY_CONFLICT")
    if supported_schema_versions is not None and not set(
        manifest.schema_versions
    ).issubset({str(item) for item in supported_schema_versions}):
        raise DeploymentTransferError("DATABASE_SCHEMA_UNSUPPORTED")
    required = payload_bytes * 3 + 64 * 1024 * 1024
    capacity = (
        available_bytes(_nearest_existing(root))
        if available_bytes is not None
        else shutil.disk_usage(_nearest_existing(root)).free
    )
    if capacity < required:
        raise DeploymentTransferError("TRANSFER_SPACE_INSUFFICIENT")
    return DeploymentImportPlan(
        archive=source,
        project_root=root,
        manifest=manifest,
        required_bytes=required,
    )


def _extract_declared(plan: DeploymentImportPlan, workspace: Path) -> tuple[Path, Path]:
    payload_root = workspace / "payload"
    dump_target = workspace / "database.dump"
    with zipfile.ZipFile(plan.archive) as package:
        dump_archive = str(plan.manifest.database_dump["archive_path"])
        with package.open(dump_archive) as source, dump_target.open("wb") as target:
            shutil.copyfileobj(source, target)
        for item in plan.manifest.files:
            logical = _safe_relative(str(item["logical_path"]))
            target_path = payload_root / logical
            target_path.parent.mkdir(parents=True, exist_ok=True)
            with package.open(str(item["archive_path"])) as source, target_path.open(
                "wb"
            ) as target:
                shutil.copyfileobj(source, target)
    return dump_target, payload_root


def _restore_files(
    *,
    project_root: Path,
    payload_root: Path,
    manifest: DeploymentTransferManifest,
    recovery_root: Path,
) -> tuple[tuple[Path, Path | None], ...]:
    operations: list[tuple[Path, Path | None]] = []
    entries = [
        (str(item["logical_path"]), payload_root / _safe_relative(str(item["logical_path"])))
        for item in manifest.files
    ]
    identity_stage = payload_root / "data" / ".deployment-identity"
    identity_stage.parent.mkdir(parents=True, exist_ok=True)
    identity_stage.write_text(manifest.source_identity + "\n", encoding="utf-8")
    entries.append(("data/.deployment-identity", identity_stage))
    try:
        for logical, staged in entries:
            target = project_root / _safe_relative(logical)
            target.parent.mkdir(parents=True, exist_ok=True)
            backup: Path | None = None
            if target.exists():
                backup = recovery_root / _safe_relative(logical)
                backup.parent.mkdir(parents=True, exist_ok=True)
                os.replace(target, backup)
            operations.append((target, backup))
            os.replace(staged, target)
    except Exception:
        _rollback_files(operations)
        raise
    return tuple(operations)


def _rollback_files(operations: Sequence[tuple[Path, Path | None]]) -> None:
    for target, backup in reversed(tuple(operations)):
        target.unlink(missing_ok=True)
        if backup is not None and backup.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(backup, target)


def apply_deployment_import(
    plan: DeploymentImportPlan,
    *,
    database: DeploymentDatabaseTransfer,
    confirmation: str,
) -> DeploymentImportResult:
    if confirmation != plan.confirmation_phrase:
        raise DeploymentTransferError("TRANSFER_CONFIRMATION_REQUIRED")
    refreshed_manifest, _ = _read_and_validate_archive(plan.archive)
    if refreshed_manifest.to_dict() != plan.manifest.to_dict():
        raise DeploymentTransferError("TRANSFER_PLAN_STALE")
    parent = _nearest_existing(plan.project_root.parent)
    staging_name = f"tr_stg_{plan.manifest.transfer_id[:24]}"
    rollback_token: str | None = None
    file_operations: tuple[tuple[Path, Path | None], ...] = ()
    with tempfile.TemporaryDirectory(prefix=".deployment-import-", dir=parent) as name:
        workspace = Path(name)
        dump, payload_root = _extract_declared(plan, workspace)
        recovery_root = workspace / "recovery"
        try:
            database.restore_to_staging(dump, staging_name)
            replacements = {
                str(item["source_path_token"]): str(
                    (plan.project_root / _safe_relative(str(item["logical_path"]))).resolve()
                )
                for item in plan.manifest.files
            }
            database.rebase_staging_paths(staging_name, replacements)
            restored = database.inspect_staging(staging_name)
            if restored.schema_versions != plan.manifest.schema_versions:
                raise DeploymentTransferError("DATABASE_SCHEMA_MISMATCH")
            if restored.main_references != plan.manifest.main_references:
                raise DeploymentTransferError("MAIN_REFERENCE_MISMATCH")
            rollback_token = database.promote_staging(staging_name)
            file_operations = _restore_files(
                project_root=plan.project_root,
                payload_root=payload_root,
                manifest=plan.manifest,
                recovery_root=recovery_root,
            )
            database.finalize_promotion(rollback_token)
        except Exception:
            if file_operations:
                _rollback_files(file_operations)
            if rollback_token is not None:
                database.rollback_promotion(rollback_token)
            else:
                database.discard_staging(staging_name)
            raise
    return DeploymentImportResult(
        status="APPLIED",
        transfer_id=plan.manifest.transfer_id,
        imported_files=len(plan.manifest.files),
    )


__all__ = [
    "DeploymentDatabaseSnapshot",
    "DeploymentExportPlan",
    "DeploymentExportResult",
    "DeploymentImportPlan",
    "DeploymentImportResult",
    "DeploymentTransferError",
    "DeploymentTransferManifest",
    "apply_deployment_import",
    "create_deployment_export",
    "plan_deployment_export",
    "plan_deployment_import",
]
