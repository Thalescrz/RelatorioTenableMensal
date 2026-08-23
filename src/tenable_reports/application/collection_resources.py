from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from tenable_reports.application.plugin_catalog import build_plugin_catalog_entries
from tenable_reports.config.database import DatabaseConfig
from tenable_reports.config.environment import CredentialConfig, load_dotenv_file
from tenable_reports.config.profile import ClientProfile
from tenable_reports.infrastructure.compact_snapshots_postgresql import (
    PostgresCompactSnapshotRepository,
)
from tenable_reports.infrastructure.plugin_catalog_postgresql import (
    PostgresPluginCatalogRepository,
)
from tenable_reports.infrastructure.postgresql import PostgresDatabase
from tenable_reports.infrastructure.tenable_inventory.client import InventoryFindingsClient
from tenable_reports.infrastructure.tenable_vm.client import TenableVmConfig


def _database(args: Any) -> PostgresDatabase | None:
    env_file = getattr(args, "database_env_file", None)
    if not env_file or not Path(env_file).is_file():
        return None
    load_dotenv_file(Path(env_file), override=True)
    if not DatabaseConfig.is_configured():
        return None
    return PostgresDatabase(DatabaseConfig.from_environment())


def compact_snapshot_repository(
    args: Any,
) -> PostgresCompactSnapshotRepository | None:
    database = _database(args)
    return (
        PostgresCompactSnapshotRepository(database)
        if database is not None
        else None
    )


def plugin_catalog_repository(
    args: Any,
) -> PostgresPluginCatalogRepository | None:
    database = _database(args)
    return (
        PostgresPluginCatalogRepository(database)
        if database is not None
        else None
    )


def inventory_client(credentials: CredentialConfig) -> InventoryFindingsClient:
    return InventoryFindingsClient(TenableVmConfig(
        access_key=credentials.access_key,
        secret_key=credentials.secret_key,
        base_url=credentials.base_url,
        timeout_seconds=credentials.timeout_seconds,
        poll_seconds=credentials.export_poll_seconds,
        max_poll_seconds=credentials.export_max_poll_seconds,
        max_wait_seconds=credentials.export_queue_timeout_seconds,
        max_processing_wait_seconds=credentials.export_processing_timeout_seconds,
        stall_warning_seconds=credentials.export_stall_warning_seconds,
        ca_bundle=credentials.ca_bundle,
        validate_tls=credentials.validate_tls,
    ))


def plugin_catalog_callback(
    repository: PostgresPluginCatalogRepository | None,
    profile: ClientProfile,
):
    if repository is None:
        return None

    def persist(records: Iterable[Mapping[str, Any]]) -> None:
        repository.upsert(build_plugin_catalog_entries(
            records,
            client_id=profile.client_id,
            tenant_id=profile.tenant_id,
            source="tenable_vm_vulnerabilities",
        ))

    return persist
