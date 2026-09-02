"""Validated handoff between remote collection and local report building."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from tenable_reports.application.publishing import sha256_file
from tenable_reports.application.web_batches import assert_sanitized_payload
from tenable_reports.domain.report_components import ReportComponent
from tenable_reports.domain.reporting import parse_utc


CHECKPOINT_SCHEMA_VERSION = 1
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_KIND_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_JSON_SCALARS = (str, int, float, bool, type(None))


class CheckpointValidationError(ValueError):
    """A safe, classified validation failure for a staged checkpoint."""

    def __init__(self, failure_code: str, safe_message: str) -> None:
        self.failure_code = str(failure_code)
        self.safe_message = str(safe_message)
        super().__init__(f"{self.failure_code}: {self.safe_message}")


def _invalid_schema(message: str = "Checkpoint de coleta inválido.") -> None:
    raise CheckpointValidationError("CHECKPOINT_INVALID_SCHEMA", message)


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        _invalid_schema(f"Campo obrigatório inválido: {field_name}.")
    text = value.strip()
    if not text:
        _invalid_schema(f"Campo obrigatório ausente: {field_name}.")
    return text


def _assert_safe_json(value: Any) -> None:
    try:
        assert_sanitized_payload(value, path="checkpoint")
    except ValueError as exc:
        raise CheckpointValidationError(
            "CHECKPOINT_SENSITIVE_PAYLOAD",
            "Checkpoint contém campo que não pode ser persistido.",
        ) from exc

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                if not isinstance(key, str) or not key.strip():
                    _invalid_schema("Checkpoint contém chave JSON inválida.")
                visit(nested)
            return
        if isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)
            return
        if not isinstance(item, _JSON_SCALARS):
            _invalid_schema("Checkpoint contém valor não serializável.")

    visit(value)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_json(nested) for key, nested in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _normalize_sha256(value: Any) -> str:
    digest = str(value or "").strip().lower()
    if _SHA256_PATTERN.fullmatch(digest) is None:
        _invalid_schema("Checkpoint contém hash SHA-256 inválido.")
    return digest


def _normalize_component(value: Any) -> str:
    try:
        return ReportComponent(value).value
    except (TypeError, ValueError) as exc:
        raise CheckpointValidationError(
            "CHECKPOINT_INVALID_SCHEMA",
            "Checkpoint contém componente desconhecido.",
        ) from exc


def _normalize_period(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _invalid_schema("Período do checkpoint inválido.")
    period = dict(value)
    _assert_safe_json(period)
    start_at = parse_utc(
        period.get("start_at") if isinstance(period.get("start_at"), str) else None
    )
    end_at = parse_utc(
        period.get("end_at") if isinstance(period.get("end_at"), str) else None
    )
    if start_at is None or end_at is None or start_at >= end_at:
        _invalid_schema("Período do checkpoint precisa respeitar [início, fim).")
    return _freeze_json(period)


def _strict_child(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _normalize_storage_and_checkpoint(
    storage_root: str | Path,
    checkpoint_path: str | Path,
) -> tuple[Path, Path]:
    raw_root = Path(storage_root)
    raw_checkpoint = Path(checkpoint_path)
    if not raw_root.is_absolute() or not raw_checkpoint.is_absolute():
        raise CheckpointValidationError(
            "CHECKPOINT_PATH_OUTSIDE_ROOT",
            "Storage e checkpoint precisam usar caminhos absolutos.",
        )
    root = raw_root.resolve()
    checkpoint = raw_checkpoint.resolve()
    if not _strict_child(checkpoint, root):
        raise CheckpointValidationError(
            "CHECKPOINT_PATH_OUTSIDE_ROOT",
            "Checkpoint fora do storage autorizado.",
        )
    return root, checkpoint


@dataclass(frozen=True, slots=True)
class CheckpointArtifact:
    component: ReportComponent | str
    kind: str
    path: Path
    sha256: str

    def __post_init__(self) -> None:
        component = _normalize_component(self.component)
        kind = _required_text(self.kind, "artifact.kind")
        if _KIND_PATTERN.fullmatch(kind) is None:
            _invalid_schema("Checkpoint contém tipo de artefato inválido.")
        raw_path = Path(self.path)
        if not raw_path.is_absolute():
            raise CheckpointValidationError(
                "CHECKPOINT_PATH_OUTSIDE_ROOT",
                "Artefato precisa usar caminho absoluto.",
            )
        object.__setattr__(self, "component", component)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "path", raw_path.resolve())
        object.__setattr__(self, "sha256", _normalize_sha256(self.sha256))

    def to_dict(self) -> dict[str, str]:
        return {
            "component": str(self.component),
            "kind": self.kind,
            "path": str(self.path),
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: Any) -> CheckpointArtifact:
        if not isinstance(value, Mapping):
            _invalid_schema("Artefato do checkpoint inválido.")
        return cls(
            component=value.get("component"),
            kind=value.get("kind"),
            path=Path(str(value.get("path") or "")),
            sha256=str(value.get("sha256") or ""),
        )


@dataclass(frozen=True, slots=True)
class CollectionCheckpoint:
    schema_version: int
    client_id: str
    tenant_id: str
    run_id: str
    logical_job_id: str
    execution_type: str
    mode: str
    origin: str
    attempt_number: int
    period: Mapping[str, Any]
    component_metadata: Mapping[str, Mapping[str, Any]]
    artifacts: tuple[CheckpointArtifact, ...]
    hashes: Mapping[str, str]

    def __post_init__(self) -> None:
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise CheckpointValidationError(
                "CHECKPOINT_INVALID_SCHEMA",
                "Versão do checkpoint incompatível.",
            )
        for name in (
            "client_id",
            "tenant_id",
            "run_id",
            "logical_job_id",
            "execution_type",
            "mode",
            "origin",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if isinstance(self.attempt_number, bool) or int(self.attempt_number) < 1:
            _invalid_schema("Número da tentativa do checkpoint inválido.")
        object.__setattr__(self, "attempt_number", int(self.attempt_number))
        object.__setattr__(self, "period", _normalize_period(self.period))

        if not isinstance(self.component_metadata, Mapping):
            _invalid_schema("Metadados de componentes inválidos.")
        normalized_metadata: dict[str, Any] = {}
        for raw_component, metadata in self.component_metadata.items():
            component = _normalize_component(raw_component)
            if component in normalized_metadata:
                _invalid_schema("Componente duplicado no checkpoint.")
            if not isinstance(metadata, Mapping):
                _invalid_schema("Metadados de componente inválidos.")
            copied = dict(metadata)
            _assert_safe_json(copied)
            normalized_metadata[component] = _freeze_json(copied)
        ordered_metadata = {
            component.value: normalized_metadata[component.value]
            for component in ReportComponent
            if component.value in normalized_metadata
        }
        object.__setattr__(
            self,
            "component_metadata",
            MappingProxyType(ordered_metadata),
        )

        if isinstance(self.artifacts, (str, bytes)) or not isinstance(
            self.artifacts,
            (list, tuple),
        ):
            _invalid_schema("Lista de artefatos do checkpoint inválida.")
        normalized_artifacts = tuple(self.artifacts)
        if any(not isinstance(item, CheckpointArtifact) for item in normalized_artifacts):
            _invalid_schema("Artefato do checkpoint inválido.")
        object.__setattr__(self, "artifacts", normalized_artifacts)

        if not isinstance(self.hashes, Mapping):
            _invalid_schema("Mapa de hashes do checkpoint inválido.")
        normalized_hashes: dict[str, str] = {}
        for raw_kind, raw_digest in self.hashes.items():
            kind = _required_text(raw_kind, "hashes.kind")
            if _KIND_PATTERN.fullmatch(kind) is None or kind in normalized_hashes:
                _invalid_schema("Mapa de hashes do checkpoint inválido.")
            normalized_hashes[kind] = _normalize_sha256(raw_digest)
        _assert_safe_json(normalized_hashes)
        object.__setattr__(
            self,
            "hashes",
            MappingProxyType(normalized_hashes),
        )
        _validate_artifact_index(self)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "client_id": self.client_id,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "logical_job_id": self.logical_job_id,
            "execution_type": self.execution_type,
            "mode": self.mode,
            "origin": self.origin,
            "attempt_number": self.attempt_number,
            "period": _thaw_json(self.period),
            "component_metadata": _thaw_json(self.component_metadata),
            "artifacts": [item.to_dict() for item in self.artifacts],
            "hashes": dict(self.hashes),
        }
        _assert_safe_json(payload)
        return payload

    @classmethod
    def from_dict(cls, value: Any) -> CollectionCheckpoint:
        if not isinstance(value, Mapping):
            _invalid_schema("Checkpoint precisa ser um objeto JSON.")
        _assert_safe_json(value)
        try:
            schema_version = int(value.get("schema_version"))
            attempt_number = int(value.get("attempt_number"))
        except (TypeError, ValueError) as exc:
            raise CheckpointValidationError(
                "CHECKPOINT_INVALID_SCHEMA",
                "Checkpoint contém campos numéricos inválidos.",
            ) from exc
        artifacts = value.get("artifacts")
        if not isinstance(artifacts, list):
            _invalid_schema("Lista de artefatos do checkpoint inválida.")
        return cls(
            schema_version=schema_version,
            client_id=value.get("client_id"),
            tenant_id=value.get("tenant_id"),
            run_id=value.get("run_id"),
            logical_job_id=value.get("logical_job_id"),
            execution_type=value.get("execution_type"),
            mode=value.get("mode"),
            origin=value.get("origin"),
            attempt_number=attempt_number,
            period=value.get("period"),
            component_metadata=value.get("component_metadata"),
            artifacts=tuple(CheckpointArtifact.from_dict(item) for item in artifacts),
            hashes=value.get("hashes"),
        )


def _validate_artifact_index(checkpoint: CollectionCheckpoint) -> None:
    artifacts_by_kind: dict[str, CheckpointArtifact] = {}
    for artifact in checkpoint.artifacts:
        if artifact.kind in artifacts_by_kind:
            _invalid_schema("Tipo de artefato duplicado no checkpoint.")
        artifacts_by_kind[artifact.kind] = artifact
    if set(artifacts_by_kind) != set(checkpoint.hashes):
        raise CheckpointValidationError(
            "CHECKPOINT_HASH_MISMATCH",
            "Mapa de hashes não corresponde aos artefatos.",
        )
    if any(
        checkpoint.hashes[kind] != artifact.sha256
        for kind, artifact in artifacts_by_kind.items()
    ):
        raise CheckpointValidationError(
            "CHECKPOINT_HASH_MISMATCH",
            "Mapa de hashes não corresponde aos artefatos.",
        )


def _validate_artifact_files(
    checkpoint: CollectionCheckpoint,
    *,
    storage_root: Path,
) -> None:
    for artifact in checkpoint.artifacts:
        path = artifact.path.resolve()
        if not _strict_child(path, storage_root):
            raise CheckpointValidationError(
                "CHECKPOINT_PATH_OUTSIDE_ROOT",
                "Artefato fora do storage autorizado.",
            )
        if not path.is_file():
            raise CheckpointValidationError(
                "CHECKPOINT_ARTIFACT_MISSING",
                "Artefato do checkpoint ausente ou inválido.",
            )
        try:
            actual = sha256_file(path)
        except OSError as exc:
            raise CheckpointValidationError(
                "CHECKPOINT_HASH_MISMATCH",
                "Artefato do checkpoint ausente ou inválido.",
            ) from exc
        if actual != artifact.sha256:
            raise CheckpointValidationError(
                "CHECKPOINT_HASH_MISMATCH",
                "Hash de artefato do checkpoint não confere.",
            )


@dataclass(frozen=True, slots=True)
class RemoteCollectionRequest:
    storage_root: Path
    checkpoint_path: Path
    client_id: str
    tenant_id: str
    run_id: str
    logical_job_id: str
    execution_type: str
    mode: str
    origin: str
    attempt_number: int
    period: Mapping[str, Any]

    def __post_init__(self) -> None:
        root, checkpoint = _normalize_storage_and_checkpoint(
            self.storage_root,
            self.checkpoint_path,
        )
        object.__setattr__(self, "storage_root", root)
        object.__setattr__(self, "checkpoint_path", checkpoint)
        for name in (
            "client_id",
            "tenant_id",
            "run_id",
            "logical_job_id",
            "execution_type",
            "mode",
            "origin",
        ):
            object.__setattr__(self, name, _required_text(getattr(self, name), name))
        if isinstance(self.attempt_number, bool) or int(self.attempt_number) < 1:
            _invalid_schema("Número da tentativa inválido.")
        object.__setattr__(self, "attempt_number", int(self.attempt_number))
        object.__setattr__(self, "period", _normalize_period(self.period))


@dataclass(frozen=True, slots=True)
class RemoteCollectionDependencies:
    collect: Callable[[RemoteCollectionRequest], CollectionCheckpoint]


@dataclass(frozen=True, slots=True)
class LocalBuildRequest:
    storage_root: Path
    checkpoint_path: Path

    def __post_init__(self) -> None:
        root, checkpoint = _normalize_storage_and_checkpoint(
            self.storage_root,
            self.checkpoint_path,
        )
        object.__setattr__(self, "storage_root", root)
        object.__setattr__(self, "checkpoint_path", checkpoint)


@dataclass(frozen=True, slots=True)
class LocalBuildDependencies:
    build: Callable[[CollectionCheckpoint], Any]


def _validate_identity(
    checkpoint: CollectionCheckpoint,
    request: RemoteCollectionRequest,
) -> None:
    expected = (
        request.client_id,
        request.tenant_id,
        request.run_id,
        request.logical_job_id,
        request.execution_type,
        request.mode,
        request.origin,
        request.attempt_number,
        request.period,
    )
    actual = (
        checkpoint.client_id,
        checkpoint.tenant_id,
        checkpoint.run_id,
        checkpoint.logical_job_id,
        checkpoint.execution_type,
        checkpoint.mode,
        checkpoint.origin,
        checkpoint.attempt_number,
        checkpoint.period,
    )
    if actual != expected:
        raise CheckpointValidationError(
            "CHECKPOINT_IDENTITY_MISMATCH",
            "Identidade do checkpoint não corresponde à solicitação.",
        )


def _write_collection_checkpoint(
    path: Path,
    checkpoint: CollectionCheckpoint,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                checkpoint.to_dict(),
                stream,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    finally:
        temp_path.unlink(missing_ok=True)


def load_collection_checkpoint(
    path: str | Path,
    *,
    storage_root: str | Path,
) -> CollectionCheckpoint:
    root, checkpoint_path = _normalize_storage_and_checkpoint(storage_root, path)
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CheckpointValidationError(
            "CHECKPOINT_INVALID_SCHEMA",
            "Não foi possível carregar um checkpoint válido.",
        ) from exc
    checkpoint = CollectionCheckpoint.from_dict(payload)
    _validate_artifact_files(checkpoint, storage_root=root)
    return checkpoint


def collect_client_remote(
    request: RemoteCollectionRequest,
    *,
    dependencies: RemoteCollectionDependencies,
) -> CollectionCheckpoint:
    checkpoint = dependencies.collect(request)
    if not isinstance(checkpoint, CollectionCheckpoint):
        _invalid_schema("Coletor remoto não retornou um checkpoint válido.")
    _validate_identity(checkpoint, request)
    _validate_artifact_files(checkpoint, storage_root=request.storage_root)
    _write_collection_checkpoint(request.checkpoint_path, checkpoint)
    return load_collection_checkpoint(
        request.checkpoint_path,
        storage_root=request.storage_root,
    )


def build_client_local(
    request: LocalBuildRequest,
    *,
    dependencies: LocalBuildDependencies,
) -> Any:
    checkpoint = load_collection_checkpoint(
        request.checkpoint_path,
        storage_root=request.storage_root,
    )
    return dependencies.build(checkpoint)


__all__ = [
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointArtifact",
    "CheckpointValidationError",
    "CollectionCheckpoint",
    "LocalBuildDependencies",
    "LocalBuildRequest",
    "RemoteCollectionDependencies",
    "RemoteCollectionRequest",
    "build_client_local",
    "collect_client_remote",
    "load_collection_checkpoint",
]
