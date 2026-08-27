"""Compact, immutable history for Tenable Cloud report datasets."""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol, Sequence

from tenable_reports.application.cloud_report_dataset import (
    CLOUD_DATASET_SCHEMA_VERSION,
    CLOUD_METRIC_DEFINITION_VERSION,
)


CLOUD_SNAPSHOT_SCHEMA_VERSION = 1
CLOUD_NORMALIZER_VERSION = "cloud-normalizer-v1"


@dataclass(frozen=True, slots=True)
class CloudSnapshotCompatibility:
    client_id: str
    tenant_id: str
    execution_type: str
    period_mode: str
    timezone: str
    scope_hash: str
    metric_definition_version: str
    connector_version: str
    normalizer_version: str
    schema_version: int


@dataclass(frozen=True, slots=True)
class CloudReportSnapshot:
    snapshot_id: str
    schema_version: int
    connector_version: str
    normalizer_version: str
    client_id: str
    tenant_id: str
    run_id: str
    attempt_number: int
    execution_type: str
    period_mode: str
    timezone: str
    period_start_at: str
    period_end_at: str
    scope_hash: str
    metric_definition_version: str
    collected_at: str
    created_at: str
    content_sha256: str
    payload_gzip: bytes
    capabilities: Mapping[str, Any]
    record_counts: Mapping[str, int]

    @property
    def compatibility(self) -> CloudSnapshotCompatibility:
        return CloudSnapshotCompatibility(
            client_id=self.client_id,
            tenant_id=self.tenant_id,
            execution_type=self.execution_type,
            period_mode=self.period_mode,
            timezone=self.timezone,
            scope_hash=self.scope_hash,
            metric_definition_version=self.metric_definition_version,
            connector_version=self.connector_version,
            normalizer_version=self.normalizer_version,
            schema_version=self.schema_version,
        )


@dataclass(frozen=True, slots=True)
class ReplayedCloudSnapshot:
    dataset: Mapping[str, Any]
    content_sha256: str


@dataclass(frozen=True, slots=True)
class CloudContractCheck:
    client_id: str
    environment: str
    connector_version: str
    credential_revision: str
    endpoint: str
    required_ready: bool
    capabilities: Mapping[str, Any]
    checked_at: str


class CloudSnapshotRepository(Protocol):
    def publish(self, snapshot: CloudReportSnapshot) -> None: ...

    def find_exact(
        self,
        *,
        compatibility: CloudSnapshotCompatibility,
        period_start_at: str,
        period_end_at: str,
    ) -> CloudReportSnapshot | None: ...

    def latest_compatible_since(
        self,
        *,
        compatibility: CloudSnapshotCompatibility,
        collected_since: str,
    ) -> CloudReportSnapshot | None: ...

    def list_main_before(
        self,
        *,
        compatibility: CloudSnapshotCompatibility,
        period_end_before: str,
    ) -> tuple[CloudReportSnapshot, ...]: ...

    def save_contract_check(self, check: CloudContractCheck) -> None: ...

    def latest_contract_check(
        self,
        *,
        client_id: str,
        environment: str,
        connector_version: str,
        credential_revision: str,
    ) -> CloudContractCheck | None: ...

    def invalidate_contract_checks(
        self,
        *,
        client_id: str,
        environment: str,
    ) -> int: ...


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _parse(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Data Cloud invalida; use ISO-8601.") from exc
    if parsed.tzinfo is None:
        raise ValueError("Data Cloud precisa conter timezone.")
    return parsed.astimezone(UTC)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _snapshot_id(
    *,
    client_id: str,
    tenant_id: str,
    run_id: str,
    attempt_number: int,
) -> str:
    identity = "|".join(
        (
            str(CLOUD_SNAPSHOT_SCHEMA_VERSION),
            client_id,
            tenant_id,
            run_id,
            str(attempt_number),
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


_FORBIDDEN_CAPABILITY_KEYS = (
    "secret",
    "token",
    "password",
    "authorization",
    "access_key",
    "api_key",
)


def _safe_capabilities(value: Mapping[str, Any]) -> dict[str, Any]:
    def visit(item: Any, path: str) -> Any:
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for raw_key, raw_value in item.items():
                key = str(raw_key)
                normalized = key.lower()
                if any(token in normalized for token in _FORBIDDEN_CAPABILITY_KEYS):
                    raise ValueError(
                        f"Capacidade Cloud contem campo sensivel: {path}{key}."
                    )
                result[key] = visit(raw_value, f"{path}{key}.")
            return result
        if isinstance(item, Sequence) and not isinstance(
            item,
            (str, bytes, bytearray),
        ):
            return [visit(child, path) for child in item]
        if item is None or isinstance(item, (str, int, float, bool)):
            return item
        return str(item)

    return visit(value, "")


def _record_counts(dataset: Mapping[str, Any]) -> dict[str, int]:
    overview = dataset.get("overview")
    overview = overview if isinstance(overview, Mapping) else {}
    return {
        "assets": int(overview.get("assets") or 0),
        "vulnerability_occurrences": int(
            overview.get("vulnerability_occurrences") or 0
        ),
        "unique_cves": int(overview.get("unique_cves") or 0),
        "posture_findings": int(overview.get("posture_findings") or 0),
        "top_critical_cves": len(dataset.get("top_critical_cves") or ()),
        "top_correctable_vulnerabilities": len(
            dataset.get("top_correctable_vulnerabilities") or ()
        ),
    }


def build_cloud_snapshot(
    *,
    dataset: Mapping[str, Any],
    client_id: str,
    tenant_id: str,
    run_id: str,
    attempt_number: int,
    execution_type: str,
    period_mode: str,
    timezone: str,
    period_start_at: str,
    period_end_at: str,
    scope_hash: str,
    collected_at: str,
    capabilities: Mapping[str, Any],
    connector_version: str = "cloud-graphql-v1",
    normalizer_version: str = CLOUD_NORMALIZER_VERSION,
    created_at: str | None = None,
) -> CloudReportSnapshot:
    if (
        dataset.get("schema_version") != CLOUD_DATASET_SCHEMA_VERSION
        or dataset.get("document_kind") != "cloud"
        or dataset.get("metric_definition_version")
        != CLOUD_METRIC_DEFINITION_VERSION
    ):
        raise ValueError("Dataset Cloud incompativel com o snapshot.")
    if int(attempt_number) < 1:
        raise ValueError("attempt_number Cloud deve ser positivo.")
    start = _parse(period_start_at)
    end = _parse(period_end_at)
    if start >= end:
        raise ValueError("Periodo Cloud invalido.")
    _parse(collected_at)
    timestamp = created_at or _utc_now()
    _parse(timestamp)
    logical = _canonical_json(dataset)
    safe_capabilities = _safe_capabilities(capabilities)
    return CloudReportSnapshot(
        snapshot_id=_snapshot_id(
            client_id=client_id,
            tenant_id=tenant_id,
            run_id=run_id,
            attempt_number=int(attempt_number),
        ),
        schema_version=CLOUD_SNAPSHOT_SCHEMA_VERSION,
        connector_version=str(connector_version),
        normalizer_version=str(normalizer_version),
        client_id=str(client_id),
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        attempt_number=int(attempt_number),
        execution_type=str(execution_type),
        period_mode=str(period_mode),
        timezone=str(timezone),
        period_start_at=str(period_start_at),
        period_end_at=str(period_end_at),
        scope_hash=str(scope_hash),
        metric_definition_version=CLOUD_METRIC_DEFINITION_VERSION,
        collected_at=str(collected_at),
        created_at=str(timestamp),
        content_sha256=hashlib.sha256(logical).hexdigest(),
        payload_gzip=gzip.compress(logical, compresslevel=9, mtime=0),
        capabilities=safe_capabilities,
        record_counts=_record_counts(dataset),
    )


def replay_cloud_snapshot(
    snapshot: CloudReportSnapshot,
) -> ReplayedCloudSnapshot:
    if snapshot.schema_version != CLOUD_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"Versao de snapshot Cloud nao suportada: {snapshot.schema_version}"
        )
    try:
        logical = gzip.decompress(snapshot.payload_gzip)
    except (OSError, EOFError) as exc:
        raise ValueError("Payload do snapshot Cloud esta corrompido.") from exc
    if hashlib.sha256(logical).hexdigest() != snapshot.content_sha256:
        raise ValueError("Checksum do snapshot Cloud nao confere.")
    try:
        dataset = json.loads(logical.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Payload do snapshot Cloud nao e JSON valido.") from exc
    if (
        not isinstance(dataset, Mapping)
        or dataset.get("schema_version") != CLOUD_DATASET_SCHEMA_VERSION
        or dataset.get("document_kind") != "cloud"
        or dataset.get("metric_definition_version")
        != snapshot.metric_definition_version
    ):
        raise ValueError("Dataset interno do snapshot Cloud e incompativel.")
    if _record_counts(dataset) != dict(snapshot.record_counts):
        raise ValueError("Contagens do snapshot Cloud divergem do payload.")
    return ReplayedCloudSnapshot(
        dataset=dict(dataset),
        content_sha256=snapshot.content_sha256,
    )


def _compatible(
    snapshot: CloudReportSnapshot,
    compatibility: CloudSnapshotCompatibility,
) -> bool:
    return snapshot.compatibility == compatibility


class MemoryCloudSnapshotRepository:
    def __init__(self) -> None:
        self._snapshots: dict[str, CloudReportSnapshot] = {}
        self._main_run_ids: set[str] = set()
        self._contract_checks: dict[
            tuple[str, str, str, str],
            CloudContractCheck,
        ] = {}

    @property
    def count(self) -> int:
        return len(self._snapshots)

    def publish(self, snapshot: CloudReportSnapshot) -> None:
        existing = self._snapshots.get(snapshot.snapshot_id)
        if existing is not None:
            if (
                existing.content_sha256 == snapshot.content_sha256
                and existing.payload_gzip == snapshot.payload_gzip
                and existing.compatibility == snapshot.compatibility
            ):
                return
            raise ValueError(
                "Snapshot Cloud imutavel ja existe com conteudo diferente."
            )
        self._snapshots[snapshot.snapshot_id] = snapshot

    def mark_main(self, run_id: str) -> None:
        if not any(item.run_id == run_id for item in self._snapshots.values()):
            raise KeyError(f"Snapshot Cloud nao encontrado para run: {run_id}")
        self._main_run_ids.add(run_id)

    def find_exact(
        self,
        *,
        compatibility: CloudSnapshotCompatibility,
        period_start_at: str,
        period_end_at: str,
    ) -> CloudReportSnapshot | None:
        matches = [
            item
            for item in self._snapshots.values()
            if _compatible(item, compatibility)
            and item.period_start_at == period_start_at
            and item.period_end_at == period_end_at
        ]
        return max(
            matches,
            key=lambda item: (
                _parse(item.collected_at),
                item.attempt_number,
                item.run_id,
            ),
            default=None,
        )

    def latest_compatible_since(
        self,
        *,
        compatibility: CloudSnapshotCompatibility,
        collected_since: str,
    ) -> CloudReportSnapshot | None:
        boundary = _parse(collected_since)
        matches = [
            item
            for item in self._snapshots.values()
            if _compatible(item, compatibility)
            and _parse(item.collected_at) >= boundary
        ]
        return max(
            matches,
            key=lambda item: (
                _parse(item.collected_at),
                item.attempt_number,
                item.run_id,
            ),
            default=None,
        )

    def list_main_before(
        self,
        *,
        compatibility: CloudSnapshotCompatibility,
        period_end_before: str,
    ) -> tuple[CloudReportSnapshot, ...]:
        boundary = _parse(period_end_before)
        values = [
            item
            for item in self._snapshots.values()
            if item.run_id in self._main_run_ids
            and _compatible(item, compatibility)
            and _parse(item.period_end_at) < boundary
        ]
        return tuple(
            sorted(
                values,
                key=lambda item: (
                    _parse(item.period_end_at),
                    item.run_id,
                ),
            )
        )

    def save_contract_check(self, check: CloudContractCheck) -> None:
        _parse(check.checked_at)
        safe = CloudContractCheck(
            client_id=check.client_id,
            environment=check.environment,
            connector_version=check.connector_version,
            credential_revision=check.credential_revision,
            endpoint=check.endpoint,
            required_ready=check.required_ready,
            capabilities=_safe_capabilities(check.capabilities),
            checked_at=check.checked_at,
        )
        key = (
            safe.client_id,
            safe.environment,
            safe.connector_version,
            safe.credential_revision,
        )
        current = self._contract_checks.get(key)
        if current is None or _parse(safe.checked_at) >= _parse(current.checked_at):
            self._contract_checks[key] = safe

    def latest_contract_check(
        self,
        *,
        client_id: str,
        environment: str,
        connector_version: str,
        credential_revision: str,
    ) -> CloudContractCheck | None:
        return self._contract_checks.get(
            (
                client_id,
                environment,
                connector_version,
                credential_revision,
            )
        )

    def invalidate_contract_checks(
        self,
        *,
        client_id: str,
        environment: str,
    ) -> int:
        keys = [
            key
            for key in self._contract_checks
            if key[0] == client_id and key[1] == environment
        ]
        for key in keys:
            del self._contract_checks[key]
        return len(keys)


__all__ = [
    "CLOUD_NORMALIZER_VERSION",
    "CLOUD_SNAPSHOT_SCHEMA_VERSION",
    "CloudContractCheck",
    "CloudReportSnapshot",
    "CloudSnapshotCompatibility",
    "CloudSnapshotRepository",
    "MemoryCloudSnapshotRepository",
    "ReplayedCloudSnapshot",
    "build_cloud_snapshot",
    "replay_cloud_snapshot",
]
