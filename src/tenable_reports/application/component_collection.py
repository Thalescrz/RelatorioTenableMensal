"""Independent, validated collection checkpoints for VM, WAS and Cloud."""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from tenable_reports.application.publishing import sha256_file, write_json_atomic
from tenable_reports.application.staged_execution import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointArtifact,
    CheckpointValidationError,
    CollectionCheckpoint,
    RemoteCollectionRequest,
)
from tenable_reports.application.web_batches import assert_sanitized_payload
from tenable_reports.domain.remote_components import RemoteComponentState
from tenable_reports.domain.report_components import ReportComponent
from tenable_reports.domain.reporting import parse_utc


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_TERMINAL_STATES = frozenset(
    {
        RemoteComponentState.COMPLETE,
        RemoteComponentState.COMPLETE_WITH_WARNINGS,
        RemoteComponentState.NOT_APPLICABLE,
        RemoteComponentState.WAITING_MANUAL_RETRY,
        RemoteComponentState.NON_RETRYABLE_FAILURE,
        RemoteComponentState.INTERRUPTED,
    }
)


def _failure(code: str, message: str) -> None:
    raise CheckpointValidationError(code, message)


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        _failure(
            "CHECKPOINT_INVALID_SCHEMA",
            f"Campo obrigatório ausente: {field}.",
        )
    return text


def _safe_mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _failure("CHECKPOINT_INVALID_SCHEMA", f"{field} precisa ser um objeto.")
    copied = json.loads(json.dumps(dict(value), ensure_ascii=False))
    try:
        assert_sanitized_payload(copied, path=f"component_checkpoint.{field}")
    except ValueError as exc:
        raise CheckpointValidationError(
            "CHECKPOINT_SENSITIVE_PAYLOAD",
            "Checkpoint contém campo que não pode ser persistido.",
        ) from exc
    return MappingProxyType(copied)


def _safe_period(value: Any) -> Mapping[str, Any]:
    period = _safe_mapping(value, field="period")
    start_at = parse_utc(
        period.get("start_at") if isinstance(period.get("start_at"), str) else None
    )
    end_at = parse_utc(
        period.get("end_at") if isinstance(period.get("end_at"), str) else None
    )
    if start_at is None or end_at is None or start_at >= end_at:
        _failure(
            "CHECKPOINT_INVALID_SCHEMA",
            "Período do checkpoint precisa respeitar [início, fim).",
        )
    return period


def _strict_child(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def component_checkpoint_path(
    request: RemoteCollectionRequest,
    component: ReportComponent | str,
) -> Path:
    """Return a short disjoint path owned by one remote component.

    The component collectors append client, run, source and export identifiers to
    this directory. Keeping the workspace independent from the verbose
    orchestration checkpoint prevents those segments from being duplicated past
    the classic Windows path limit. The checkpoint location itself is excluded
    from the digest so the dashboard and the isolated CLI process derive exactly
    the same workspace without passing another long path argument.
    """

    normalized = ReportComponent(component)
    identity = json.dumps(
        {
            "run_id": request.run_id,
            "logical_job_id": request.logical_job_id,
            "execution_type": request.execution_type,
            "mode": request.mode,
            "origin": request.origin,
            "attempt_number": request.attempt_number,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    workspace_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return (
        request.storage_root
        / ".components"
        / workspace_id
        / normalized.value.lower()
        / "checkpoint.json"
    ).resolve()


def _legacy_component_checkpoint_path(
    request: RemoteCollectionRequest,
    component: ReportComponent | str,
) -> Path:
    normalized = ReportComponent(component)
    return (
        request.checkpoint_path.parent
        / request.run_id
        / normalized.value.lower()
        / "checkpoint.json"
    ).resolve()


def component_query_fingerprint(
    component: ReportComponent | str,
    payload: Mapping[str, Any],
) -> str:
    """Hash a sanitized query contract without persisting credentials."""

    normalized = ReportComponent(component)
    safe_payload = dict(_safe_mapping(payload, field="query"))
    canonical = json.dumps(
        {"component": normalized.value, "query": safe_payload},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ComponentCollectionCheckpoint:
    schema_version: int
    checkpoint_path: Path
    component: ReportComponent
    client_id: str
    tenant_id: str
    run_id: str
    logical_job_id: str
    execution_type: str
    mode: str
    origin: str
    attempt_number: int
    period: Mapping[str, Any]
    status: RemoteComponentState
    artifacts: tuple[CheckpointArtifact, ...]
    metadata: Mapping[str, Any]
    query_fingerprint: str

    def __post_init__(self) -> None:
        if int(self.schema_version) != CHECKPOINT_SCHEMA_VERSION:
            _failure(
                "CHECKPOINT_INVALID_SCHEMA",
                "Versão do checkpoint de componente incompatível.",
            )
        checkpoint_path = Path(self.checkpoint_path)
        if not checkpoint_path.is_absolute() or checkpoint_path.name != "checkpoint.json":
            _failure(
                "CHECKPOINT_PATH_OUTSIDE_ROOT",
                "Caminho do checkpoint de componente inválido.",
            )
        component = ReportComponent(self.component)
        expected_parent = component.value.lower()
        if checkpoint_path.parent.name != expected_parent:
            _failure(
                "CHECKPOINT_COMPONENT_MISMATCH",
                "Diretório do checkpoint não corresponde ao componente.",
            )
        status = RemoteComponentState(self.status)
        if status not in _TERMINAL_STATES:
            _failure(
                "CHECKPOINT_INVALID_SCHEMA",
                "Checkpoint de componente exige um estado terminal.",
            )
        for field in (
            "client_id",
            "tenant_id",
            "run_id",
            "logical_job_id",
            "execution_type",
            "mode",
            "origin",
        ):
            object.__setattr__(self, field, _required_text(getattr(self, field), field))
        if isinstance(self.attempt_number, bool) or int(self.attempt_number) < 1:
            _failure(
                "CHECKPOINT_INVALID_SCHEMA",
                "Número da tentativa do checkpoint inválido.",
            )
        digest = str(self.query_fingerprint or "").strip().lower()
        if _SHA256_PATTERN.fullmatch(digest) is None:
            _failure(
                "CHECKPOINT_INVALID_SCHEMA",
                "Fingerprint de consulta inválido.",
            )
        artifacts = tuple(self.artifacts)
        if any(not isinstance(item, CheckpointArtifact) for item in artifacts):
            _failure("CHECKPOINT_INVALID_SCHEMA", "Artefato de componente inválido.")
        seen_kinds: set[str] = set()
        for artifact in artifacts:
            if artifact.component != component.value:
                _failure(
                    "CHECKPOINT_COMPONENT_MISMATCH",
                    "Artefato pertence a outro componente.",
                )
            if artifact.kind in seen_kinds:
                _failure(
                    "CHECKPOINT_INVALID_SCHEMA",
                    "Tipo de artefato duplicado no checkpoint de componente.",
                )
            seen_kinds.add(artifact.kind)
            if not _strict_child(artifact.path, checkpoint_path.parent):
                _failure(
                    "CHECKPOINT_PATH_OUTSIDE_ROOT",
                    "Artefato fora do diretório exclusivo do componente.",
                )
        object.__setattr__(self, "schema_version", CHECKPOINT_SCHEMA_VERSION)
        object.__setattr__(self, "checkpoint_path", checkpoint_path.resolve())
        object.__setattr__(self, "component", component)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "attempt_number", int(self.attempt_number))
        object.__setattr__(self, "period", _safe_period(self.period))
        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(self, "metadata", _safe_mapping(self.metadata, field="metadata"))
        object.__setattr__(self, "query_fingerprint", digest)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "checkpoint_path": str(self.checkpoint_path),
            "component": self.component.value,
            "client_id": self.client_id,
            "tenant_id": self.tenant_id,
            "run_id": self.run_id,
            "logical_job_id": self.logical_job_id,
            "execution_type": self.execution_type,
            "mode": self.mode,
            "origin": self.origin,
            "attempt_number": self.attempt_number,
            "period": dict(self.period),
            "status": self.status.value,
            "artifacts": [item.to_dict() for item in self.artifacts],
            "metadata": dict(self.metadata),
            "query_fingerprint": self.query_fingerprint,
        }
        try:
            assert_sanitized_payload(payload, path="component_checkpoint")
        except ValueError as exc:
            raise CheckpointValidationError(
                "CHECKPOINT_SENSITIVE_PAYLOAD",
                "Checkpoint contém campo que não pode ser persistido.",
            ) from exc
        return payload

    @classmethod
    def from_dict(cls, value: Any) -> ComponentCollectionCheckpoint:
        if not isinstance(value, Mapping):
            _failure(
                "CHECKPOINT_INVALID_SCHEMA",
                "Checkpoint de componente precisa ser um objeto JSON.",
            )
        artifacts = value.get("artifacts")
        if not isinstance(artifacts, (list, tuple)):
            _failure(
                "CHECKPOINT_INVALID_SCHEMA",
                "Lista de artefatos do componente inválida.",
            )
        try:
            schema_version = int(value.get("schema_version"))
            attempt_number = int(value.get("attempt_number"))
        except (TypeError, ValueError) as exc:
            raise CheckpointValidationError(
                "CHECKPOINT_INVALID_SCHEMA",
                "Checkpoint de componente contém campos numéricos inválidos.",
            ) from exc
        return cls(
            schema_version=schema_version,
            checkpoint_path=Path(str(value.get("checkpoint_path") or "")),
            component=ReportComponent(value.get("component")),
            client_id=value.get("client_id"),
            tenant_id=value.get("tenant_id"),
            run_id=value.get("run_id"),
            logical_job_id=value.get("logical_job_id"),
            execution_type=value.get("execution_type"),
            mode=value.get("mode"),
            origin=value.get("origin"),
            attempt_number=attempt_number,
            period=value.get("period"),
            status=RemoteComponentState(value.get("status")),
            artifacts=tuple(CheckpointArtifact.from_dict(item) for item in artifacts),
            metadata=value.get("metadata"),
            query_fingerprint=value.get("query_fingerprint"),
        )


def _validate_component_files(
    checkpoint: ComponentCollectionCheckpoint,
    *,
    storage_root: Path,
) -> None:
    if not _strict_child(checkpoint.checkpoint_path, storage_root):
        _failure(
            "CHECKPOINT_PATH_OUTSIDE_ROOT",
            "Checkpoint de componente fora do storage autorizado.",
        )
    for artifact in checkpoint.artifacts:
        if not _strict_child(artifact.path, storage_root):
            _failure(
                "CHECKPOINT_PATH_OUTSIDE_ROOT",
                "Artefato fora do storage autorizado.",
            )
        if not artifact.path.is_file():
            _failure(
                "CHECKPOINT_ARTIFACT_MISSING",
                "Artefato do checkpoint de componente ausente.",
            )
        if sha256_file(artifact.path) != artifact.sha256:
            _failure(
                "CHECKPOINT_HASH_MISMATCH",
                "Hash do artefato do checkpoint de componente não confere.",
            )


def persist_component_checkpoint(
    checkpoint: ComponentCollectionCheckpoint,
    *,
    storage_root: str | Path,
) -> ComponentCollectionCheckpoint:
    root = Path(storage_root).resolve()
    _validate_component_files(checkpoint, storage_root=root)
    write_json_atomic(checkpoint.checkpoint_path, checkpoint.to_dict())
    return load_component_checkpoint(checkpoint.checkpoint_path, storage_root=root)


def load_component_checkpoint(
    path: str | Path,
    *,
    storage_root: str | Path,
) -> ComponentCollectionCheckpoint:
    root = Path(storage_root).resolve()
    checkpoint_path = Path(path).resolve()
    if not _strict_child(checkpoint_path, root):
        _failure(
            "CHECKPOINT_PATH_OUTSIDE_ROOT",
            "Checkpoint de componente fora do storage autorizado.",
        )
    try:
        payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CheckpointValidationError(
            "CHECKPOINT_INVALID_SCHEMA",
            "Não foi possível carregar um checkpoint de componente válido.",
        ) from exc
    checkpoint = ComponentCollectionCheckpoint.from_dict(payload)
    if checkpoint.checkpoint_path != checkpoint_path:
        _failure(
            "CHECKPOINT_IDENTITY_MISMATCH",
            "Caminho declarado pelo checkpoint não corresponde ao arquivo.",
        )
    _validate_component_files(checkpoint, storage_root=root)
    return checkpoint


def _validate_identity(
    checkpoint: ComponentCollectionCheckpoint,
    request: RemoteCollectionRequest,
    *,
    allow_prior_attempt: bool = False,
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
        dict(request.period),
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
        dict(checkpoint.period),
    )
    if actual != expected and not (
        allow_prior_attempt
        and actual[:-2] == expected[:-2]
        and actual[-1] == expected[-1]
        and checkpoint.attempt_number <= request.attempt_number
    ):
        _failure(
            "CHECKPOINT_IDENTITY_MISMATCH",
            "A identidade do checkpoint de componente não corresponde à solicitação.",
        )
    if allow_prior_attempt and checkpoint.attempt_number < request.attempt_number:
        # A selective component retry deliberately combines the freshly
        # collected component with checkpoints that were already complete in
        # an earlier attempt of the same logical run. ``load_component_checkpoint``
        # has already revalidated the persisted file and artifact hashes; its
        # original disjoint path is therefore the ownership proof we preserve.
        return
    expected_paths = {
        component_checkpoint_path(request, checkpoint.component),
        _legacy_component_checkpoint_path(request, checkpoint.component),
    }
    if checkpoint.checkpoint_path not in expected_paths:
        _failure(
            "CHECKPOINT_IDENTITY_MISMATCH",
            "O caminho do checkpoint de componente não corresponde à solicitação.",
        )


def _merged_status(checkpoint: ComponentCollectionCheckpoint) -> str:
    if checkpoint.status in {
        RemoteComponentState.COMPLETE,
        RemoteComponentState.COMPLETE_WITH_WARNINGS,
        RemoteComponentState.NOT_APPLICABLE,
    }:
        return checkpoint.status.value
    return "FAILED"


def merge_component_checkpoints(
    *,
    request: RemoteCollectionRequest,
    checkpoints: Sequence[ComponentCollectionCheckpoint],
    allow_prior_attempts: bool = False,
) -> CollectionCheckpoint:
    """Consolidate terminal component checkpoints into the existing build contract."""

    seen: set[ReportComponent] = set()
    metadata: dict[str, dict[str, Any]] = {}
    artifacts: list[CheckpointArtifact] = []
    seen_kinds: set[str] = set()
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, ComponentCollectionCheckpoint):
            _failure("CHECKPOINT_INVALID_SCHEMA", "Checkpoint de componente inválido.")
        if allow_prior_attempts:
            checkpoint = load_component_checkpoint(
                checkpoint.checkpoint_path,
                storage_root=request.storage_root,
            )
        _validate_identity(
            checkpoint,
            request,
            allow_prior_attempt=allow_prior_attempts,
        )
        if checkpoint.component in seen:
            _failure(
                "CHECKPOINT_INVALID_SCHEMA",
                "Componente duplicado na consolidação de checkpoints.",
            )
        seen.add(checkpoint.component)
        component_metadata = dict(checkpoint.metadata)
        component_metadata["status"] = _merged_status(checkpoint)
        metadata[checkpoint.component.value] = component_metadata
        for artifact in checkpoint.artifacts:
            if artifact.kind in seen_kinds:
                _failure(
                    "CHECKPOINT_INVALID_SCHEMA",
                    "Tipo de artefato duplicado entre componentes.",
                )
            seen_kinds.add(artifact.kind)
            artifacts.append(artifact)

    return CollectionCheckpoint(
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        client_id=request.client_id,
        tenant_id=request.tenant_id,
        run_id=request.run_id,
        logical_job_id=request.logical_job_id,
        execution_type=request.execution_type,
        mode=request.mode,
        origin=request.origin,
        attempt_number=request.attempt_number,
        period=dict(request.period),
        component_metadata=metadata,
        artifacts=tuple(artifacts),
        hashes={artifact.kind: artifact.sha256 for artifact in artifacts},
    )


__all__ = [
    "ComponentCollectionCheckpoint",
    "component_checkpoint_path",
    "component_query_fingerprint",
    "load_component_checkpoint",
    "merge_component_checkpoints",
    "persist_component_checkpoint",
]
