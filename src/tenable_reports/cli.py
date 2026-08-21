from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from tenable_reports.application.collect import (
    AssetExportRequest,
    VulnerabilityExportRequest,
    collect_asset_snapshot,
    collect_vm_snapshot,
)
from tenable_reports.application.collect_was import (
    WasExportRequest,
    collect_optional_was_snapshot,
    collect_was_snapshot,
)
from tenable_reports.application.failures import (
    FailureCode,
    OperationalFailure,
    classify_failure,
)
from tenable_reports.application.normalize import normalize_collections
from tenable_reports.application.normalize_was import normalize_was_collection
from tenable_reports.application.report_dataset import build_report_dataset_from_snapshot
from tenable_reports.application.tag_report_dataset import (
    build_tag_report_datasets_from_snapshot,
)
from tenable_reports.application.history import (
    SQLiteSnapshotRepository,
    finalize_history_publication,
    import_history_csv,
    prepare_dataset_history,
    publish_dataset_history,
)
from tenable_reports.application.report_registry import InMemoryReportRegistry
from tenable_reports.application.postgresql_migration import (
    main_backfill_source_state,
    migrate_legacy_state,
)
from tenable_reports.application.report_main_backfill import plan_main_backfill
from tenable_reports.application.orchestration import (
    OrchestrationRequest,
    load_orchestration_config,
    run_orchestration,
)
from tenable_reports.application.publishing import (
    PublicationDocument,
    create_publication_manifest,
)
from tenable_reports.application.retention import (
    apply_cleanup_plan,
    plan_published_run_cleanup,
)
from tenable_reports.application.tag_scope import (
    VmTag,
    collect_tag_scope_snapshot,
    parse_tag_values,
    prompt_tag_selection,
    resolve_tag_selectors,
)
from tenable_reports.config.environment import CredentialConfig, EnvironmentError, load_dotenv_file
from tenable_reports.config.database import DatabaseAdminConfig, DatabaseConfig
from tenable_reports.config.profile import ClientProfile, ProfileError, load_client_profile
from tenable_reports.domain.normalization import normalize_and_link
from tenable_reports.domain.reporting import (
    previous_calendar_month,
    resolve_manual_period,
)
from tenable_reports.infrastructure.tenable_vm.client import (
    ApiError,
    CredentialError,
    ExportTimeoutError,
    TenableVmClient,
    TenableVmConfig,
)
from tenable_reports.infrastructure.tenable_was.client import TenableWasClient
from tenable_reports.infrastructure.postgresql import (
    PostgresDatabase,
    PostgresOperationsRepository,
    PostgresSnapshotRepository,
    provision_postgresql,
)
from tenable_reports.infrastructure.report_registry_postgresql import (
    PostgresReportRegistry,
)
from tenable_reports.domain.report_reference import (
    READY_STATUS,
    ReportCandidate,
    ReportOrigin,
    reference_key_for_candidate,
)
from tenable_reports.presentation.base_report_docx import (
    create_base_template,
    generate_base_report,
)
from tenable_reports.presentation.full_base_report_docx import generate_full_base_report
from tenable_reports.presentation.report_filenames import (
    report_filename,
    tag_report_filename,
)
from tenable_reports.presentation.customizations_report_docx import (
    generate_customizations_report,
)
from tenable_reports.presentation.tag_report_docx import generate_tag_report


SELECTIVE_PROPERTIES = (
    "source",
    "severity",
    "state",
    "first_observed",
    "last_seen",
    "last_fixed",
    "resurfaced_date",
    "port",
    "protocol",
    "service",
    "asset.id",
    "asset.name",
    "asset.ipv4_addresses",
    "definition.id",
    "definition.name",
    "definition.cve",
    "definition.vpr.score",
)


@dataclass(frozen=True, slots=True)
class _CollectedPeriodExecution:
    profile: ClientProfile
    output_root: Path
    run_id: str
    period: Any
    execution_type: str
    artifact: Any
    history_publication: Any
    selected_tag_count: int
    was_collection_status: str
    tag_artifacts: tuple[Any, ...] = ()
    tag_enriched_dataset_paths: Mapping[str, Path] | None = None
    tag_reports_requested: int = 0
    warnings: tuple[Mapping[str, Any], ...] = ()
    snapshot_repository: Any = None
    report_registry: Any = None

    @property
    def dataset_path(self) -> Path:
        if self.history_publication is not None:
            return Path(self.history_publication.enriched_dataset_path)
        return Path(self.artifact.dataset_path)

    @property
    def history_database_path(self) -> Path | None:
        if (
            self.history_publication is None
            or self.history_publication.database_path is None
        ):
            return None
        return Path(self.history_publication.database_path)

    @property
    def history_store(self) -> dict[str, str | None]:
        if self.history_publication is None:
            return {"backend": None, "location": None}
        return {
            "backend": self.history_publication.repository_backend,
            "location": self.history_publication.repository_location,
        }

    def to_dict(self) -> dict[str, Any]:
        dataset = self.artifact.result.dataset
        return {
            "status": "complete",
            "client_id": self.profile.client_id,
            "run_id": self.run_id,
            "execution_type": self.execution_type,
            "storage_root": str(self.output_root.resolve()),
            "period": self.period.to_dict(),
            "general_collection_filtered_by_tags": False,
            "network_comparison_tags_selected": self.selected_tag_count > 0,
            "selected_tag_count": self.selected_tag_count,
            "tag_reports_requested": self.tag_reports_requested,
            "dataset": str(self.dataset_path.resolve()),
            "canonical_dataset": str(Path(self.artifact.dataset_path).resolve()),
            "history_status": (
                self.history_publication.history_status
                if self.history_publication is not None else "SKIPPED"
            ),
            "history_store": self.history_store,
            "history_predecessor_period_id": (
                self.history_publication.predecessor.period_id
                if self.history_publication is not None
                and self.history_publication.predecessor is not None
                else None
            ),
            "assets_observed": dataset.metrics["assets"]["observed_in_period"],
            "assets_excluded": dataset.metrics["assets"]["excluded_from_period"],
            "non_mitigated": dataset.metrics["non_mitigated"]["total"],
            "mitigated": dataset.metrics["mitigated"]["total"],
            "was_findings_in_period": dataset.populations["was_findings"]["included"],
            "was_top5": len(dataset.top_web_vulnerabilities),
            "was_collection_status": self.was_collection_status,
            "warnings": [dict(item) for item in self.warnings],
            "quality_issue_codes": [item.code for item in dataset.quality_issues],
        }


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s %(message)s",
    )


def _load_filters(path: str | None) -> dict[str, object]:
    if not path:
        return {"state": ["OPEN", "REOPENED"]}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Nao foi possivel ler o arquivo de filtros: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Filtros JSON invalidos na linha {exc.lineno}.") from exc
    if not isinstance(data, dict):
        raise ValueError("O arquivo de filtros deve conter um objeto JSON.")
    return data


def _schema_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    observed: dict[str, set[str]] = {}

    def visit(value: Any, path: str) -> None:
        kind = (
            "null" if value is None
            else "boolean" if isinstance(value, bool)
            else "integer" if isinstance(value, int)
            else "number" if isinstance(value, float)
            else "object" if isinstance(value, Mapping)
            else "array" if isinstance(value, list)
            else "string" if isinstance(value, str)
            else type(value).__name__
        )
        observed.setdefault(path, set()).add(kind)
        if isinstance(value, Mapping):
            for key, child in value.items():
                visit(child, f"{path}.{key}" if path else str(key))
        elif isinstance(value, list):
            for child in value[:10]:
                visit(child, f"{path}[]")

    for record in records[:10]:
        visit(record, "$")
    return {path: sorted(types) for path, types in sorted(observed.items())}


def _client_from_environment(credentials: CredentialConfig) -> TenableVmClient:
    return TenableVmClient(
        TenableVmConfig(
            access_key=credentials.access_key,
            secret_key=credentials.secret_key,
            base_url=credentials.base_url,
            timeout_seconds=credentials.timeout_seconds,
            ca_bundle=credentials.ca_bundle,
            validate_tls=credentials.validate_tls,
        )
    )


def _was_client_from_environment(credentials: CredentialConfig) -> TenableWasClient:
    return TenableWasClient(
        TenableVmConfig(
            access_key=credentials.access_key,
            secret_key=credentials.secret_key,
            base_url=credentials.base_url,
            timeout_seconds=credentials.timeout_seconds,
            ca_bundle=credentials.ca_bundle,
            validate_tls=credentials.validate_tls,
        )
    )


def _load_credentials(env_file: str) -> CredentialConfig:
    # O arquivo explicitamente escolhido governa a execucao. Isso evita usar,
    # sem intencao, credenciais antigas herdadas do ambiente do sistema.
    load_dotenv_file(env_file, override=True)
    credentials = CredentialConfig.from_environment()
    if not credentials.is_complete:
        raise CredentialError(
            f"Preencha TENABLE_ACCESS e TENABLE_SECRET no arquivo {env_file} antes da validacao online."
        )
    return credentials


def _load_database_config(
    env_file: str | Path | None,
    *,
    required: bool,
) -> DatabaseConfig | None:
    if env_file:
        load_dotenv_file(env_file, override=True)
    if not DatabaseConfig.is_configured():
        if required:
            raise EnvironmentError(
                "Configure credentials/database.env com as variaveis "
                "TENABLE_REPORTS_DB_* antes de usar o PostgreSQL."
            )
        return None
    return DatabaseConfig.from_environment()


def _history_repository(
    args: argparse.Namespace,
    *,
    sqlite_default: Path,
) -> tuple[Any, Path | None]:
    legacy_path = getattr(args, "history_database", None)
    if legacy_path:
        path = Path(legacy_path)
        return SQLiteSnapshotRepository(path), path
    config = _load_database_config(
        getattr(args, "database_env_file", None),
        required=False,
    )
    if config is not None:
        return PostgresSnapshotRepository(PostgresDatabase(config)), None
    return SQLiteSnapshotRepository(sqlite_default), sqlite_default


def _postgres_operations(
    env_file: str | Path | None,
    *,
    required: bool,
) -> PostgresOperationsRepository | None:
    config = _load_database_config(env_file, required=required)
    if config is None:
        return None
    return PostgresOperationsRepository(PostgresDatabase(config))


def _candidate_from_legacy_snapshot(snapshot: Any) -> ReportCandidate:
    execution_type = str(snapshot.compatibility.execution_type)
    return ReportCandidate(
        run_id=str(snapshot.run_id),
        client_id=str(snapshot.compatibility.client_id),
        tenant_id=str(snapshot.compatibility.tenant_id),
        origin=(
            ReportOrigin.SCHEDULED
            if execution_type == "AUTOMATIC_MONTHLY"
            else ReportOrigin.MANUAL
        ),
        execution_type=execution_type,
        period_start_at=str(snapshot.period_start_at),
        period_end_at=str(snapshot.period_end_at),
        period_mode=str(snapshot.compatibility.period_mode),
        timezone=str(snapshot.compatibility.timezone),
        scope_hash=str(snapshot.compatibility.scope_hash),
        metric_definition_version=str(
            snapshot.compatibility.metric_definition_version
        ),
        publication_status=READY_STATUS,
        documents_valid=True,
    )


def _report_registry(
    args: argparse.Namespace,
    repository: Any,
) -> Any:
    config = _load_database_config(
        getattr(args, "database_env_file", None),
        required=False,
    )
    if config is not None:
        return PostgresReportRegistry(PostgresDatabase(config))
    registry = InMemoryReportRegistry()
    if isinstance(repository, SQLiteSnapshotRepository):
        for snapshot in repository.all_snapshots():
            candidate = _candidate_from_legacy_snapshot(snapshot)
            key = reference_key_for_candidate(candidate)
            registry.register_report(candidate, snapshot)
            registry.promote_main(
                key,
                candidate.run_id,
                actor="legacy-sqlite",
                reason="LEGACY_LAST_SNAPSHOT",
            )
    return registry


def command_validate_profile(args: argparse.Namespace) -> int:
    profile = load_client_profile(args.profile)
    print(
        json.dumps(
            {
                "status": "valid",
                "schema_version": profile.schema_version,
                "client_id": profile.client_id,
                "report_type": profile.report.type,
                "base_modules": list(profile.report.base_modules),
                "intelligence_modules": list(profile.report.intelligence_modules),
                "capabilities": {
                    "was": profile.was_scope.enabled,
                    "cloud_security": profile.cloud_security_scope.enabled,
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


def _automatic_period(args: argparse.Namespace, profile: Any) -> Any:
    return previous_calendar_month(
        timezone_name=profile.reporting.timezone,
        reference_at=getattr(args, "reference_at", None),
    )


def _manual_period(args: argparse.Namespace, profile: Any) -> Any:
    return resolve_manual_period(
        timezone_name=profile.reporting.timezone,
        reference_at=getattr(args, "reference_at", None),
        days=getattr(args, "days", None),
        start_at=getattr(args, "start_at", None),
        end_at=getattr(args, "end_at", None),
    )


def _period_for_mode(args: argparse.Namespace, profile: Any) -> Any:
    if getattr(args, "mode", "automatic") == "manual":
        return _manual_period(args, profile)
    if any(
        getattr(args, name, None) is not None
        for name in ("days", "start_at", "end_at")
    ):
        raise ValueError("--days/--start-at/--end-at pertencem ao modo manual.")
    return _automatic_period(args, profile)


def _scoped_output_root(output_root: str | Path, execution_type: str) -> Path:
    directory = "automatic-monthly" if execution_type == "AUTOMATIC_MONTHLY" else "manual"
    root = Path(output_root)
    return root if root.name == directory else root / directory


def _period_filters(
    *,
    period: Any,
    asset_filters_path: str | None,
    finding_filters_path: str | None,
) -> tuple[dict[str, object], dict[str, object]]:
    asset_filters = _load_filters(asset_filters_path) if asset_filters_path else {}
    finding_filters = _load_filters(finding_filters_path) if finding_filters_path else {}
    asset_filters["since"] = period.start_epoch
    asset_filters.setdefault("types", ["host"])
    finding_filters["since"] = period.start_epoch
    finding_filters.setdefault("state", ["OPEN", "REOPENED", "FIXED"])
    finding_filters.setdefault("severity", ["low", "medium", "high", "critical"])
    tag_filter_keys = {
        str(key)
        for filters in (asset_filters, finding_filters)
        for key in filters
        if str(key).startswith("tag.")
    }
    if tag_filter_keys:
        raise ValueError(
            "Filtros tag.* nao podem limitar a coleta geral do relatorio. Use "
            "--select-tags, --tag ou report.network_comparison_tags somente para "
            "o comparativo temporal por rede."
        )
    return asset_filters, finding_filters


def _was_period_filters(*, period: Any, profile: ClientProfile) -> dict[str, object]:
    filters: dict[str, object] = {
        "since": period.start_epoch,
        "state": ["OPEN", "REOPENED", "FIXED"],
        "severity": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
    }
    if profile.was_scope.application_ids:
        filters["asset_uuid"] = list(profile.was_scope.application_ids)
    return filters


def _selected_tags(
    client: TenableVmClient,
    profile: ClientProfile,
    args: argparse.Namespace,
) -> tuple[VmTag, ...]:
    interactive = bool(getattr(args, "select_tags", False))
    explicit = tuple(getattr(args, "tags", None) or ())
    configured = tuple(profile.report.network_comparison_tags)
    tag_report_config = profile.report.tag_reports
    report_tags = tuple(
        VmTag(
            uuid=item.tag_uuid,
            category_uuid=item.category_uuid,
            category_name=item.category_name,
            value=item.value,
        )
        for item in tag_report_config.tags
        if tag_report_config.enabled and item.generate_report
    )
    if interactive and (explicit or configured or report_tags):
        raise ValueError(
            "--select-tags nao pode ser combinado com --tag nem "
            "TAGs configuradas no perfil."
        )
    selectors = explicit or configured
    if interactive:
        available = parse_tag_values(client.list_tag_values())
        return prompt_tag_selection(available)
    resolved = ()
    if selectors:
        available = parse_tag_values(client.list_tag_values())
        resolved = resolve_tag_selectors(available, selectors)
    combined: list[VmTag] = []
    seen: set[str] = set()
    for tag in (*report_tags, *resolved):
        if tag.uuid not in seen:
            combined.append(tag)
            seen.add(tag.uuid)
    return tuple(combined)


def command_preview_period(args: argparse.Namespace) -> int:
    profile = load_client_profile(args.profile)
    period = _period_for_mode(args, profile)
    execution_type = "MANUAL" if args.mode == "manual" else "AUTOMATIC_MONTHLY"
    print(json.dumps({
        "status": "valid",
        "client_id": profile.client_id,
        "period": period.to_dict(),
        "execution_type": execution_type,
        "storage_directory": str(_scoped_output_root("data", execution_type)),
        "recommended_schedule": (
            "primeiro dia do mes no timezone do relatorio"
            if execution_type == "AUTOMATIC_MONTHLY" else None
        ),
    }, ensure_ascii=False))
    return 0


def command_contract_check(args: argparse.Namespace) -> int:
    if not args.confirm_live_api:
        raise ValueError(
            "A validacao online exige --confirm-live-api; ela inicia um export real no tenant."
        )
    credentials = _load_credentials(args.env_file)
    profile = load_client_profile(args.profile)
    client = _client_from_environment(credentials)
    filters = _load_filters(args.filters)
    export_uuid = args.export_uuid
    if not export_uuid:
        export_uuid = client.start_vulnerability_export(
            filters=filters,
            num_assets=args.num_assets,
            include_unlicensed=profile.vm_scope.include_unlicensed,
            include_software_vulns=False,
            include_plugin_output=False,
            properties=list(SELECTIVE_PROPERTIES) if args.select_properties else None,
        )
    status, chunks = client.wait_for_completion(export_uuid)
    result: dict[str, object] = {
        "status": "valid",
        "client_id": profile.client_id,
        "source": "tenable_vm_vulnerabilities",
        "export_uuid": export_uuid,
        "export_state": str(status.get("status") or status.get("state") or "unknown"),
        "chunk_count": len(chunks),
    }
    if chunks:
        records = client.download_chunk(export_uuid, chunks[0])
        result["sample_chunk_id"] = chunks[0]
        result["sample_record_count"] = len(records)
        result["sample_fields"] = sorted(records[0].keys()) if records else []
        result["sample_schema"] = _schema_summary(records)
    else:
        result["sample_record_count"] = 0
        result["sample_fields"] = []
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_contract_check_assets(args: argparse.Namespace) -> int:
    if not args.confirm_live_api:
        raise ValueError(
            "A validacao online exige --confirm-live-api; ela inicia um export real no tenant."
        )
    credentials = _load_credentials(args.env_file)
    profile = load_client_profile(args.profile)
    client = _client_from_environment(credentials)
    filters = _load_filters(args.filters) if args.filters else {}
    export_uuid = args.export_uuid
    if not export_uuid:
        export_uuid = client.start_asset_export_v2(
            filters=filters or None,
            chunk_size=args.chunk_size,
            include_open_ports=False,
            include_resource_tags=False,
        )
    status, chunks = client.wait_for_asset_completion(export_uuid)
    result: dict[str, object] = {
        "status": "valid",
        "client_id": profile.client_id,
        "source": "tenable_vm_assets_v2",
        "export_uuid": export_uuid,
        "export_state": str(status.get("status") or status.get("state") or "unknown"),
        "chunk_count": len(chunks),
    }
    if chunks:
        records = client.download_asset_chunk(export_uuid, chunks[0])
        result["sample_chunk_id"] = chunks[0]
        result["sample_record_count"] = len(records)
        result["sample_fields"] = sorted(records[0].keys()) if records else []
        result["sample_schema"] = _schema_summary(records)
    else:
        result["sample_record_count"] = 0
        result["sample_fields"] = []
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_contract_check_was(args: argparse.Namespace) -> int:
    if not args.confirm_live_api:
        raise ValueError(
            "A validacao online exige --confirm-live-api; ela inicia um export WAS real."
        )
    credentials = _load_credentials(args.env_file)
    profile = load_client_profile(args.profile)
    if not profile.was_scope.enabled:
        raise ValueError("O perfil precisa habilitar scope.was.enabled=true.")
    period = _automatic_period(args, profile)
    client = _was_client_from_environment(credentials)
    filters: dict[str, Any] = {
        "since": period.start_epoch,
        "state": ["OPEN", "REOPENED", "FIXED"],
        "severity": ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
    }
    if profile.was_scope.application_ids:
        filters["asset_uuid"] = list(profile.was_scope.application_ids)
    export_uuid = args.export_uuid or client.start_findings_export(
        filters=filters,
        num_assets=args.num_assets,
        include_unlicensed=False,
    )
    status, chunks = client.wait_for_findings_completion(export_uuid)
    result: dict[str, object] = {
        "status": "valid",
        "client_id": profile.client_id,
        "source": "tenable_was_findings",
        "period": period.to_dict(),
        "export_uuid": export_uuid,
        "export_state": str(status.get("status") or status.get("state") or "unknown"),
        "chunk_count": len(chunks),
    }
    if chunks:
        records = client.download_findings_chunk(export_uuid, chunks[0])
        result["sample_chunk_id"] = chunks[0]
        result["sample_record_count"] = len(records)
        result["sample_fields"] = sorted(records[0].keys()) if records else []
        result["sample_schema"] = _schema_summary(records)
    else:
        result["sample_record_count"] = 0
        result["sample_fields"] = []
    print(json.dumps(result, ensure_ascii=False))
    return 0


def command_contract_check_link(args: argparse.Namespace) -> int:
    if not args.confirm_live_api:
        raise ValueError(
            "A validacao online exige --confirm-live-api; ela baixa chunks dos exports informados."
        )
    credentials = _load_credentials(args.env_file)
    profile = load_client_profile(args.profile)
    client = _client_from_environment(credentials)
    _, asset_chunks = client.wait_for_asset_completion(args.asset_export_uuid)
    _, finding_chunks = client.wait_for_completion(args.vm_export_uuid)
    asset_records: list[dict[str, Any]] = []
    for chunk_id in asset_chunks:
        asset_records.extend(client.download_asset_chunk(args.asset_export_uuid, chunk_id))
    finding_records = (
        client.download_chunk(args.vm_export_uuid, finding_chunks[0]) if finding_chunks else []
    )
    normalized = normalize_and_link(
        asset_records=asset_records,
        finding_records=finding_records,
        client_id=profile.client_id,
    )
    issue_codes: dict[str, int] = {}
    for issue in normalized.issues:
        issue_codes[issue.code] = issue_codes.get(issue.code, 0) + 1
    print(json.dumps({
        "status": "valid",
        "client_id": profile.client_id,
        "asset_export_uuid": args.asset_export_uuid,
        "vm_export_uuid": args.vm_export_uuid,
        "asset_chunks": len(asset_chunks),
        "finding_chunks_sampled": 1 if finding_chunks else 0,
        "reconciliation": normalized.reconciliation.to_dict(),
        "quality_issue_codes": dict(sorted(issue_codes.items())),
    }, ensure_ascii=False))
    return 0


def command_collect_vm(args: argparse.Namespace) -> int:
    credentials = _load_credentials(args.env_file)
    profile = load_client_profile(args.profile)
    client = _client_from_environment(credentials)
    filters = _load_filters(args.filters)
    result = collect_vm_snapshot(
        client=client,
        profile=profile,
        request=VulnerabilityExportRequest(
            filters=filters,
            num_assets=args.num_assets,
            include_unlicensed=profile.vm_scope.include_unlicensed,
            include_software_vulns=args.include_software_vulns,
            include_plugin_output=args.include_output,
            properties=SELECTIVE_PROPERTIES if args.select_properties else (),
        ),
        output_root=args.output_root,
        run_id=args.run_id,
    )
    print(
        json.dumps(
            {
                "status": "complete",
                "snapshot": str(result.snapshot_path.resolve()),
                "record_count": result.snapshot.record_count,
                "availability": result.snapshot.availability.value,
            },
            ensure_ascii=False,
        )
    )
    return 0


def command_collect_phase3(args: argparse.Namespace) -> int:
    if not args.confirm_live_api:
        raise ValueError(
            "A coleta completa exige --confirm-live-api; ela inicia exports reais no tenant."
        )
    credentials = _load_credentials(args.env_file)
    profile = load_client_profile(args.profile)
    client = _client_from_environment(credentials)
    actual_run_id = args.run_id or str(uuid.uuid4())
    finding_filters = _load_filters(args.finding_filters)
    asset_filters = _load_filters(args.asset_filters) if args.asset_filters else {}

    assets = collect_asset_snapshot(
        client=client,
        profile=profile,
        request=AssetExportRequest(
            filters=asset_filters,
            chunk_size=args.asset_chunk_size,
        ),
        output_root=args.output_root,
        run_id=actual_run_id,
        export_uuid=args.asset_export_uuid,
        resume_from=getattr(args, "asset_resume_manifest", None),
        minimum_free_gb=getattr(args, "minimum_free_gb", 10),
    )
    findings = collect_vm_snapshot(
        client=client,
        profile=profile,
        request=VulnerabilityExportRequest(
            filters=finding_filters,
            num_assets=args.num_assets,
            include_unlicensed=profile.vm_scope.include_unlicensed,
            include_software_vulns=args.include_software_vulns,
            include_plugin_output=args.include_output,
        ),
        output_root=args.output_root,
        run_id=actual_run_id,
        export_uuid=args.vm_export_uuid,
        resume_from=getattr(args, "vm_resume_manifest", None),
        minimum_free_gb=getattr(args, "minimum_free_gb", 10),
    )
    normalized = normalize_collections(
        profile=profile,
        asset_collection=assets,
        finding_collection=findings,
        output_root=args.output_root,
    )
    reconciliation = normalized.result.reconciliation
    print(json.dumps({
        "status": "complete",
        "run_id": actual_run_id,
        "asset_snapshot": str(assets.snapshot_path.resolve()),
        "finding_snapshot": str(findings.snapshot_path.resolve()),
        "normalized_manifest": str(normalized.manifest_path.resolve()),
        "assets": reconciliation.normalized_assets,
        "findings": reconciliation.normalized_findings,
        "linked_findings": reconciliation.linked_findings,
        "orphan_findings": reconciliation.orphan_findings,
        "quality_issues": len(normalized.result.issues),
    }, ensure_ascii=False))
    return 0


def command_build_report_dataset(args: argparse.Namespace) -> int:
    profile = load_client_profile(args.profile)
    period = _period_for_mode(args, profile)
    execution_type = "MANUAL" if args.mode == "manual" else "AUTOMATIC_MONTHLY"
    output_root = _scoped_output_root(args.output_root, execution_type)
    artifact = build_report_dataset_from_snapshot(
        profile=profile,
        run_id=args.run_id,
        period=period,
        output_root=output_root,
        include_output=args.include_output,
        execution_type=execution_type,
    )
    tag_dataset_bundle = build_tag_report_datasets_from_snapshot(
        profile=profile,
        run_id=actual_run_id,
        period=period,
        output_root=output_root,
        include_output=args.include_output,
        execution_type=execution_type,
    )
    tag_dataset_paths = {
        item.tag.uuid: item.dataset_path for item in tag_dataset_bundle.artifacts
    }
    dataset = artifact.result.dataset
    history_publication = None
    if not args.skip_history:
        repository, history_database = _history_repository(
            args,
            sqlite_default=(
                output_root / "history" / profile.client_id / "tenable-history.sqlite"
            ),
        )
        history_publication = publish_dataset_history(
            profile=profile,
            dataset_path=artifact.dataset_path,
            normalized_findings_path=(
                output_root / "normalized" / profile.client_id / args.run_id
                / "findings.jsonl.gz"
            ),
            database_path=history_database,
            output_path=artifact.directory / "report-dataset-with-history.json",
            csv_path=args.history_export_csv,
            repository=repository,
        )
    print(json.dumps({
        "status": "complete",
        "client_id": profile.client_id,
        "run_id": args.run_id,
        "execution_type": execution_type,
        "period": period.to_dict(),
        "dataset": str(
            (
                history_publication.enriched_dataset_path
                if history_publication else artifact.dataset_path
            ).resolve()
        ),
        "canonical_dataset": str(artifact.dataset_path.resolve()),
        "history_status": (
            history_publication.history_status if history_publication else "SKIPPED"
        ),
        "history_predecessor_period_id": (
            history_publication.predecessor.period_id
            if history_publication and history_publication.predecessor else None
        ),
        "manifest": str(artifact.manifest_path.resolve()),
        "asset_population": dataset.populations["assets"],
        "finding_population": dataset.populations["findings"],
        "non_mitigated": dataset.metrics["non_mitigated"],
        "mitigated": dataset.metrics["mitigated"],
        "collection_timing": dataset.collection_timing,
        "quality_issue_codes": [item.code for item in dataset.quality_issues],
    }, ensure_ascii=False))
    return 0


def command_publish_history(args: argparse.Namespace) -> int:
    profile = load_client_profile(args.profile)
    repository, database_path = _history_repository(
        args,
        sqlite_default=Path(args.database),
    )
    result = publish_dataset_history(
        profile=profile,
        dataset_path=args.dataset,
        normalized_findings_path=args.normalized_findings,
        database_path=database_path,
        output_path=args.output,
        csv_path=args.export_csv,
        repository=repository,
    )
    print(json.dumps({
        "status": "complete",
        "client_id": profile.client_id,
        "period_id": result.snapshot.period_id,
        "history_status": result.history_status,
        "predecessor_period_id": (
            result.predecessor.period_id if result.predecessor else None
        ),
        "history_store": {
            "backend": result.repository_backend,
            "location": result.repository_location,
        },
        "dataset": str(result.enriched_dataset_path.resolve()),
        "csv": str(result.csv_path.resolve()) if result.csv_path else None,
    }, ensure_ascii=False))
    return 0


def command_import_history_csv(args: argparse.Namespace) -> int:
    repository, database_path = _history_repository(
        args,
        sqlite_default=Path(args.database),
    )
    snapshots = import_history_csv(
        csv_path=args.csv,
        database_path=database_path,
        repository=repository,
    )
    print(json.dumps({
        "status": "complete",
        "imported_snapshots": len(snapshots),
        "period_ids": [item.period_id for item in snapshots],
        "history_store": {
            "backend": (
                "sqlite" if isinstance(repository, SQLiteSnapshotRepository)
                else "postgresql"
            ),
            "location": (
                str(repository.path.resolve())
                if isinstance(repository, SQLiteSnapshotRepository)
                else str(repository.location)
            ),
        },
    }, ensure_ascii=False))
    return 0


def _execute_period(
    args: argparse.Namespace,
    *,
    execution_type: str,
    period: Any,
) -> _CollectedPeriodExecution:
    credentials = _load_credentials(args.env_file)
    profile = load_client_profile(args.profile)
    output_root = _scoped_output_root(args.output_root, execution_type)
    client = _client_from_environment(credentials)
    was_client = _was_client_from_environment(credentials)
    selected_tags = _selected_tags(client, profile, args)
    asset_filters, finding_filters = _period_filters(
        period=period,
        asset_filters_path=args.asset_filters,
        finding_filters_path=args.finding_filters,
    )
    actual_run_id = args.run_id or str(uuid.uuid4())
    tag_scope = (
        collect_tag_scope_snapshot(
            client=client,
            profile=profile,
            tags=selected_tags,
            output_root=output_root,
            run_id=actual_run_id,
        )
        if selected_tags else None
    )
    assets = collect_asset_snapshot(
        client=client,
        profile=profile,
        request=AssetExportRequest(
            filters=asset_filters,
            chunk_size=args.asset_chunk_size,
        ),
        output_root=output_root,
        run_id=actual_run_id,
        export_uuid=args.asset_export_uuid,
    )
    findings = collect_vm_snapshot(
        client=client,
        profile=profile,
        request=VulnerabilityExportRequest(
            filters=finding_filters,
            num_assets=args.num_assets,
            include_unlicensed=profile.vm_scope.include_unlicensed,
            include_software_vulns=args.include_software_vulns,
            include_plugin_output=args.include_output,
        ),
        output_root=output_root,
        run_id=actual_run_id,
        export_uuid=args.vm_export_uuid,
    )
    was_collection = None
    was_collection_status = "DISABLED"
    collection_warnings: tuple[Mapping[str, Any], ...] = ()
    if profile.was_scope.enabled:
        was_attempt = collect_optional_was_snapshot(
            client=was_client,
            profile=profile,
            request=WasExportRequest(
                filters=_was_period_filters(period=period, profile=profile),
                num_assets=args.was_num_assets,
                include_unlicensed=False,
            ),
            output_root=output_root,
            run_id=actual_run_id,
            export_uuid=args.was_export_uuid,
        )
        was_collection = was_attempt.result
        was_collection_status = was_attempt.status
        collection_warnings = was_attempt.warnings
    normalized = normalize_collections(
        profile=profile,
        asset_collection=assets,
        finding_collection=findings,
        output_root=output_root,
        allowed_asset_ids=None,
    )
    if was_collection is not None:
        normalize_was_collection(
            profile=profile,
            collection=was_collection,
            output_root=output_root,
        )
    artifact = build_report_dataset_from_snapshot(
        profile=profile,
        run_id=actual_run_id,
        period=period,
        output_root=output_root,
        include_output=args.include_output,
        execution_type=execution_type,
    )
    history_publication = None
    if not getattr(args, "skip_history", False):
        repository, history_database = _history_repository(
            args,
            sqlite_default=(
                output_root / "history" / profile.client_id / "tenable-history.sqlite"
            ),
        )
        report_registry = _report_registry(args, repository)
        history_publication = prepare_dataset_history(
            profile=profile,
            dataset_path=artifact.dataset_path,
            normalized_findings_path=normalized.findings_path,
            tag_dataset_paths=tag_dataset_paths,
            database_path=history_database,
            output_path=artifact.directory / "report-dataset-with-history.json",
            csv_path=getattr(args, "history_export_csv", None),
            repository=repository,
            registry=report_registry,
            origin=getattr(args, "origin", None),
        )
    else:
        repository = None
        report_registry = None
    tag_report_config = profile.report.tag_reports
    requested_tag_reports = sum(
        tag_report_config.enabled and item.generate_report
        for item in tag_report_config.tags
    )
    tag_enriched_dataset_paths = (
        history_publication.tag_enriched_dataset_paths
        if history_publication is not None
        else tag_dataset_paths
    )
    return _CollectedPeriodExecution(
        profile=profile,
        output_root=output_root,
        run_id=actual_run_id,
        period=period,
        execution_type=execution_type,
        artifact=artifact,
        history_publication=history_publication,
        selected_tag_count=len(selected_tags),
        was_collection_status=was_collection_status,
        tag_artifacts=tag_dataset_bundle.artifacts,
        tag_enriched_dataset_paths=tag_enriched_dataset_paths,
        tag_reports_requested=requested_tag_reports,
        warnings=tuple((*collection_warnings, *tag_dataset_bundle.warnings)),
        snapshot_repository=repository,
        report_registry=report_registry,
    )


def _collect_period(args: argparse.Namespace, *, execution_type: str, period: Any) -> int:
    result = _execute_period(args, execution_type=execution_type, period=period)
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 0


def command_collect_monthly(args: argparse.Namespace) -> int:
    if not args.confirm_live_api:
        raise ValueError(
            "A coleta mensal exige --confirm-live-api; ela inicia exports reais no tenant."
        )
    profile = load_client_profile(args.profile)
    return _collect_period(
        args,
        execution_type="AUTOMATIC_MONTHLY",
        period=_automatic_period(args, profile),
    )


def command_collect_manual(args: argparse.Namespace) -> int:
    if not args.confirm_live_api:
        raise ValueError(
            "A coleta manual exige --confirm-live-api; ela inicia exports reais no tenant."
        )
    profile = load_client_profile(args.profile)
    return _collect_period(
        args,
        execution_type="MANUAL",
        period=_manual_period(args, profile),
    )


def command_build_base_template(args: argparse.Namespace) -> int:
    template_path = create_base_template(
        args.output,
        assets_dir=args.assets_dir,
    )
    print(json.dumps({
        "status": "complete",
        "template": str(template_path.resolve()),
    }, ensure_ascii=False))
    return 0


def command_generate_base_docx(args: argparse.Namespace) -> int:
    profile = load_client_profile(args.profile)
    result = generate_base_report(
        template_path=args.template,
        dataset_path=args.dataset,
        profile=profile,
        output_path=args.output,
        mask_sensitive=args.mask_sensitive,
    )
    print(json.dumps({
        "status": "complete",
        "client_id": result.client_id,
        "period_id": result.period_id,
        "template_version": result.template_version,
        "top_asset_rows": result.top_asset_rows,
        "masked_sensitive_fields": result.masked_sensitive_fields,
        "document": str(result.output_path.resolve()),
    }, ensure_ascii=False))
    return 0


def command_generate_full_base_docx(args: argparse.Namespace) -> int:
    profile = load_client_profile(args.profile)
    result = generate_full_base_report(
        template_path=args.template,
        dataset_path=args.dataset,
        profile=profile,
        output_path=args.output,
        assets_dir=args.assets_dir,
        mask_sensitive=args.mask_sensitive,
    )
    print(json.dumps({
        "status": "complete",
        "client_id": result.client_id,
        "period_id": result.period_id,
        "template_version": result.template_version,
        "top_asset_rows": result.top_asset_rows,
        "top_open_rows": result.top_open_rows,
        "masked_sensitive_fields": result.masked_sensitive_fields,
        "document": str(result.output_path.resolve()),
    }, ensure_ascii=False))
    return 0


def command_generate_customizations_docx(args: argparse.Namespace) -> int:
    profile = load_client_profile(args.profile)
    dataset_path = args.dataset
    history_publication = None
    if args.history_database or args.use_history:
        if not args.normalized_findings:
            raise ValueError(
                "--normalized-findings e obrigatorio quando --history-database for usado."
            )
        history_output = (
            Path(args.history_dataset_output)
            if args.history_dataset_output
            else Path(args.output).with_name(
                f"{Path(args.output).stem}.report-dataset-with-history.json"
            )
        )
        repository, database_path = _history_repository(
            args,
            sqlite_default=Path(
                args.history_database or "data/history/tenable-history.sqlite"
            ),
        )
        history_publication = publish_dataset_history(
            profile=profile,
            dataset_path=args.dataset,
            normalized_findings_path=args.normalized_findings,
            database_path=database_path,
            output_path=history_output,
            csv_path=args.history_export_csv,
            repository=repository,
        )
        dataset_path = history_publication.enriched_dataset_path
    result = generate_customizations_report(
        template_path=args.template,
        dataset_path=dataset_path,
        profile=profile,
        output_path=args.output,
        mask_sensitive=args.mask_sensitive,
    )
    print(json.dumps({
        "status": "complete",
        "client_id": result.client_id,
        "period_id": result.period_id,
        "template_version": result.template_version,
        "rendered_modules": list(result.rendered_modules),
        "omitted_modules": list(result.omitted_modules),
        "history_status": (
            history_publication.history_status if history_publication else None
        ),
        "history_predecessor_period_id": (
            history_publication.predecessor.period_id
            if history_publication and history_publication.predecessor else None
        ),
        "document": str(result.output_path.resolve()),
    }, ensure_ascii=False))
    return 0


def command_generate_report_pair(args: argparse.Namespace) -> int:
    profile = load_client_profile(args.profile)
    base_result = generate_full_base_report(
        template_path=args.template,
        dataset_path=args.dataset,
        profile=profile,
        output_path=args.base_output,
        assets_dir=args.assets_dir,
        mask_sensitive=args.mask_sensitive,
    )
    custom_result = generate_customizations_report(
        template_path=args.template,
        dataset_path=args.dataset,
        profile=profile,
        output_path=args.custom_output,
        mask_sensitive=args.mask_sensitive,
    )
    print(json.dumps({
        "status": "complete",
        "client_id": base_result.client_id,
        "period_id": base_result.period_id,
        "base_document": str(base_result.output_path.resolve()),
        "customizations_document": str(custom_result.output_path.resolve()),
        "rendered_customization_modules": list(custom_result.rendered_modules),
        "omitted_customization_modules": list(custom_result.omitted_modules),
    }, ensure_ascii=False))
    return 0


def _safe_filename_component(value: str) -> str:
    component = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-.")
    return component or "periodo"


def command_run_client(args: argparse.Namespace) -> int:
    if not args.confirm_live_api:
        raise ValueError(
            "A execucao completa exige --confirm-live-api; ela inicia exports reais no tenant."
        )
    profile = load_client_profile(args.profile)
    period = _period_for_mode(args, profile)
    execution_type = "MANUAL" if args.mode == "manual" else "AUTOMATIC_MONTHLY"
    collected = _execute_period(
        args,
        execution_type=execution_type,
        period=period,
    )
    period_slug = _safe_filename_component(str(period.period_id))
    report_directory = (
        collected.output_root
        / "reports"
        / profile.client_id
        / collected.run_id
        / period_slug
    )
    base_output = Path(args.base_output) if args.base_output else (
        report_directory / report_filename(profile.display_name, period, "base")
    )
    custom_output = Path(args.custom_output) if args.custom_output else (
        report_directory / report_filename(profile.display_name, period, "custom")
    )
    base_result = generate_full_base_report(
        template_path=args.template,
        dataset_path=collected.dataset_path,
        profile=profile,
        output_path=base_output,
        assets_dir=args.assets_dir,
        mask_sensitive=args.mask_sensitive,
    )
    custom_result = generate_customizations_report(
        template_path=args.template,
        dataset_path=collected.dataset_path,
        profile=profile,
        output_path=custom_output,
        mask_sensitive=args.mask_sensitive,
    )
    publication_documents: list[PublicationDocument] = [
        PublicationDocument(base_result.output_path, "base"),
        PublicationDocument(custom_result.output_path, "custom"),
    ]
    tag_documents: list[dict[str, str]] = []
    tag_warnings = [dict(item) for item in getattr(collected, "warnings", ())]
    tag_artifacts = tuple(getattr(collected, "tag_artifacts", ()) or ())
    tag_enriched_paths = dict(
        getattr(collected, "tag_enriched_dataset_paths", None) or {}
    )
    requested_tag_reports = int(
        getattr(collected, "tag_reports_requested", len(tag_artifacts)) or 0
    )
    for index, tag_artifact in enumerate(tag_artifacts, start=1):
        tag = tag_artifact.tag
        print(json.dumps({
            "event": "TAG_REPORT_PROGRESS",
            "current": index,
            "total": requested_tag_reports,
            "tag_uuid": tag.uuid,
            "tag_label": tag.label,
        }, ensure_ascii=False), flush=True)
        tag_dataset_path = tag_enriched_paths.get(
            tag.uuid, Path(tag_artifact.dataset_path)
        )
        tag_output = report_directory / tag_report_filename(
            profile.display_name,
            period,
            tag.category_name,
            tag.value,
            tag.uuid,
        )
        try:
            tag_result = generate_tag_report(
                template_path=args.template,
                dataset_path=tag_dataset_path,
                profile=profile,
                output_path=tag_output,
                mask_sensitive=args.mask_sensitive,
            )
        except Exception as exc:
            tag_warnings.append({
                "code": "TAG_REPORT_RENDER_FAILED",
                "tag_uuid": tag.uuid,
                "tag_label": tag.label,
                "stage": "tag_report_render",
                "message": str(exc)[:500],
            })
            continue
        publication_documents.append(PublicationDocument(
            path=tag_result.output_path,
            document_kind="tag",
            tag_uuid=tag.uuid,
            tag_category=tag.category_name,
            tag_value=tag.value,
        ))
        tag_documents.append({
            "tag_uuid": tag.uuid,
            "tag_label": tag.label,
            "path": str(Path(tag_result.output_path).resolve()),
        })
    tag_reports_generated = len(tag_documents)
    tag_reports_failed = max(0, requested_tag_reports - tag_reports_generated)
    publication_manifest = create_publication_manifest(
        output_path=report_directory / "publication-manifest.json",
        client_id=profile.client_id,
        tenant_id=profile.tenant_id,
        run_id=collected.run_id,
        execution_type=execution_type,
        period=period.to_dict(),
        dataset_path=collected.dataset_path,
        documents=tuple(publication_documents),
        history_database=collected.history_database_path,
        history_store=collected.history_store,
        origin=getattr(args, "origin", None),
        logical_job_id=getattr(args, "logical_job_id", None),
        attempt_number=getattr(args, "attempt_number", 1),
    )
    operations = _postgres_operations(args.database_env_file, required=False)
    if collected.history_publication is not None:
        if collected.snapshot_repository is None or collected.report_registry is None:
            raise RuntimeError("Componentes do histórico não foram preservados na execução.")
        finalize_history_publication(
            collected.history_publication,
            snapshot_repository=collected.snapshot_repository,
            registry=collected.report_registry,
            publication_validated=True,
            auto_promote=True,
        )
    if operations is not None:
        operations.record_publication_manifest(publication_manifest)
    cleanup_payload: dict[str, Any] = {
        "status": "NOT_REQUIRED",
        "removed_bytes": 0,
        "removed_categories": [],
    }
    history_confirmed = (
        collected.history_publication is not None
        and collected.history_store.get("backend") == "postgresql"
    )
    if (
        operations is not None
        and history_confirmed
        and getattr(args, "cleanup_after_publish", True)
    ):
        operations.record_cleanup_status(collected.run_id, "PENDING")
        try:
            cleanup_plan = plan_published_run_cleanup(
                scoped_output_root=collected.output_root,
                client_id=profile.client_id,
                run_id=collected.run_id,
                publication_confirmed=True,
                history_confirmed=True,
            )
            cleanup_result = apply_cleanup_plan(
                scoped_output_root=collected.output_root,
                candidates=cleanup_plan.candidates,
            )
            operations.record_cleanup_status(
                collected.run_id,
                cleanup_result.status,
                cleanup_bytes=cleanup_result.removed_bytes,
            )
            cleanup_payload = {
                "status": cleanup_result.status,
                "removed_bytes": cleanup_result.removed_bytes,
                "removed_categories": [
                    path.parent.parent.name for path in cleanup_result.removed
                ],
                "failed_categories": [
                    failure.path.parent.parent.name
                    for failure in cleanup_result.failures
                ],
            }
        except Exception:
            operations.record_cleanup_status(collected.run_id, "FAILED")
            cleanup_payload = {
                "status": "FAILED",
                "removed_bytes": 0,
                "removed_categories": [],
                "warning": "A reciclagem automática ficou pendente para nova tentativa.",
            }
    payload = collected.to_dict()
    payload.update({
        "status": (
            "complete_with_warnings" if tag_reports_failed else "complete"
        ),
        "base_document": str(base_result.output_path.resolve()),
        "customizations_document": str(custom_result.output_path.resolve()),
        "rendered_customization_modules": list(custom_result.rendered_modules),
        "omitted_customization_modules": list(custom_result.omitted_modules),
        "publication_manifest": str(publication_manifest.resolve()),
        "tag_reports_requested": requested_tag_reports,
        "tag_reports_generated": tag_reports_generated,
        "tag_reports_failed": tag_reports_failed,
        "tag_documents": tag_documents,
        "warnings": tag_warnings,
        "external_distribution_performed": False,
        "cleanup": cleanup_payload,
    })
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def command_validate_orchestration(args: argparse.Namespace) -> int:
    config = load_orchestration_config(args.config)
    print(json.dumps({
        "status": "valid",
        "schema_version": 1,
        "orchestration_id": config.orchestration_id,
        "output_root": str(config.output_root),
        "max_parallel": config.max_parallel,
        "retention_days": config.retention_days,
        "retention_policy": {
            "failed_raw_days": config.failed_raw_days,
            "failed_staging_days": config.failed_staging_days,
            "logs_days": config.logs_days,
            "cleanup_after_publish": config.cleanup_after_publish,
            "successful_raw_days": config.successful_raw_days,
            "normalized_days": config.normalized_days,
            "documents_days": config.documents_days,
        },
        "database_env_file": str(config.database_env_file),
        "database_env_file_exists": config.database_env_file.is_file(),
        "clients": [
            {
                "client_id": item.client_id,
                "enabled": item.enabled,
                "profile": str(item.profile_path),
                "env_file": str(item.env_file),
                "env_file_exists": item.env_file.is_file(),
                "tag_count": len(item.tags),
            }
            for item in config.clients
        ],
    }, ensure_ascii=False))
    return 0


def command_orchestrate(args: argparse.Namespace) -> int:
    if not args.dry_run and not args.confirm_live_api:
        raise ValueError(
            "A orquestracao real exige --confirm-live-api; use --dry-run para apenas planejar."
        )
    config = load_orchestration_config(args.config)
    operations = (
        None
        if args.dry_run
        else _postgres_operations(config.database_env_file, required=False)
    )
    retention_state = operations.retention_state() if operations is not None else {}
    result = run_orchestration(
        config=config,
        request=OrchestrationRequest(
            mode=args.mode,
            reference_at=args.reference_at,
            days=args.days,
            start_at=args.start_at,
            end_at=args.end_at,
            selected_client_ids=tuple(args.clients or ()),
            max_parallel=args.max_parallel,
            dry_run=args.dry_run,
            apply_retention_policy=args.apply_retention,
        ),
        run_status=retention_state.get("run_status"),
        history_confirmed_run_ids=retention_state.get(
            "history_confirmed_run_ids", ()
        ),
        main_run_ids=retention_state.get("main_run_ids", ()),
        retry_required_run_ids=retention_state.get(
            "retry_required_run_ids", ()
        ),
        last_success_bytes_by_client=retention_state.get(
            "last_success_bytes_by_client"
        ),
        progress_callback=lambda event: print(
            json.dumps(dict(event), ensure_ascii=False), flush=True
        ),
    )
    # A dry run only plans commands and must remain usable without database
    # connectivity (or even the PostgreSQL driver) on the analyst's machine.
    if not args.dry_run:
        if operations is not None:
            operations.record_orchestration_manifest(result.manifest_path)
    print(json.dumps(result.to_dict(), ensure_ascii=False))
    return 1 if result.failed_count else 0


def command_serve_web(args: argparse.Namespace) -> int:
    from tenable_reports.webapp.server import serve_dashboard

    serve_dashboard(
        project_root=args.project_root,
        config_path=args.config,
        host=args.host,
        port=args.port,
        open_browser=args.open_browser,
    )
    return 0


def command_database_bootstrap(args: argparse.Namespace) -> int:
    load_dotenv_file(args.database_env_file, override=True)
    load_dotenv_file(args.admin_env_file, override=False)
    result = provision_postgresql(
        admin=DatabaseAdminConfig.from_environment(),
        application=DatabaseConfig.from_environment(),
    )
    print(json.dumps({"status": "complete", **result}, ensure_ascii=False))
    return 0


def command_database_migrate(args: argparse.Namespace) -> int:
    config = _load_database_config(args.database_env_file, required=True)
    if config is None:
        raise EnvironmentError("Configuracao PostgreSQL ausente.")
    database = PostgresDatabase(config)
    applied = database.apply_migrations()
    print(json.dumps({
        "status": "complete",
        "location": config.safe_location,
        "migrations_applied": list(applied),
    }, ensure_ascii=False))
    return 0


def command_database_status(args: argparse.Namespace) -> int:
    config = _load_database_config(args.database_env_file, required=True)
    if config is None:
        raise EnvironmentError("Configuracao PostgreSQL ausente.")
    database = PostgresDatabase(config)
    database.apply_migrations()
    print(json.dumps({"status": "complete", **database.status()}, ensure_ascii=False))
    return 0


def command_backfill_report_main(args: argparse.Namespace) -> int:
    config = _load_database_config(args.database_env_file, required=True)
    if config is None:
        raise EnvironmentError("Configuracao PostgreSQL ausente.")
    database = PostgresDatabase(config)
    database.apply_migrations()
    registry = PostgresReportRegistry(database, migrate=False)
    operations = PostgresOperationsRepository(database, migrate=False)
    source_state = main_backfill_source_state(operations)
    plan = plan_main_backfill(
        registry.list_reports(include_deleted=True),
        used_history_run_ids=source_state.used_history_run_ids,
        existing_main_run_ids=source_state.existing_main_run_ids,
    )
    applied: list[dict[str, str]] = []
    if args.apply:
        for key, run_id in plan.promotions:
            registry.promote_main(
                key,
                run_id,
                actor="system-backfill",
                reason="migração inicial",
            )
            applied.append({"reference_key": key.stable_key, "run_id": run_id})
    print(json.dumps({
        "status": "complete",
        "location": config.safe_location,
        "applied": bool(args.apply),
        **plan.to_dict(),
        "applied_promotions": applied,
    }, ensure_ascii=False))
    return 0


def command_migrate_legacy_state(args: argparse.Namespace) -> int:
    config = _load_database_config(args.database_env_file, required=True)
    if config is None:
        raise EnvironmentError("Configuracao PostgreSQL ausente.")
    database = PostgresDatabase(config)
    database.apply_migrations()
    result = migrate_legacy_state(
        roots=args.root or ("data", "analysis_artifacts"),
        snapshots=PostgresSnapshotRepository(database, migrate=False),
        operations=PostgresOperationsRepository(database, migrate=False),
    )
    print(json.dumps({
        "status": "complete",
        "location": config.safe_location,
        **result.to_dict(),
    }, ensure_ascii=False))
    return 0


def _add_complete_collection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mode", choices=("automatic", "manual"), default="automatic")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument(
        "--database-env-file", default="credentials/database.env"
    )
    parser.add_argument("--output-root", default="data")
    parser.add_argument("--run-id")
    parser.add_argument(
        "--logical-job-id",
        help="Identidade estável da competência usada entre tentativas.",
    )
    parser.add_argument("--attempt-number", type=int, default=1)
    parser.add_argument(
        "--origin",
        choices=("SCHEDULED", "AUTOMATIC_RETRY", "MANUAL"),
    )
    parser.add_argument("--reference-at")
    parser.add_argument("--days", type=int)
    parser.add_argument("--start-at")
    parser.add_argument("--end-at")
    parser.add_argument("--asset-filters")
    parser.add_argument("--finding-filters")
    parser.add_argument("--asset-chunk-size", type=int, default=1000)
    parser.add_argument("--num-assets", type=int, default=1000)
    parser.add_argument("--asset-export-uuid")
    parser.add_argument("--vm-export-uuid")
    parser.add_argument("--asset-resume-manifest")
    parser.add_argument("--vm-resume-manifest")
    parser.add_argument("--minimum-free-gb", type=int, default=10)
    parser.add_argument("--was-export-uuid")
    parser.add_argument("--was-num-assets", type=int, default=1000)
    parser.add_argument("--include-software-vulns", action="store_true")
    parser.add_argument("--include-output", action="store_true")
    parser.add_argument("--history-database")
    parser.add_argument("--history-export-csv")
    parser.add_argument("--skip-history", action="store_true")
    parser.add_argument("--template", default="templates/corporate/base-v1.docx")
    parser.add_argument("--assets-dir", default="templates/corporate/assets")
    parser.add_argument("--base-output")
    parser.add_argument("--custom-output")
    parser.add_argument("--mask-sensitive", action="store_true")
    tag_scope = parser.add_mutually_exclusive_group()
    tag_scope.add_argument("--select-tags", action="store_true")
    tag_scope.add_argument("--tag", dest="tags", action="append")
    parser.add_argument("--confirm-live-api", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tenable-reports")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile = subparsers.add_parser("validate-profile", help="Valida um perfil sem acessar APIs.")
    profile.add_argument("--profile", required=True)
    profile.set_defaults(handler=command_validate_profile)

    period = subparsers.add_parser(
        "preview-period",
        help="Mostra a janela automática mensal ou a janela manual efetiva.",
    )
    period.add_argument("--profile", required=True)
    period.add_argument("--mode", choices=("automatic", "manual"), default="automatic")
    period.add_argument("--reference-at")
    period.add_argument("--days", type=int)
    period.add_argument("--start-at")
    period.add_argument("--end-at")
    period.set_defaults(handler=command_preview_period)

    contract = subparsers.add_parser(
        "contract-check-vm",
        help="Executa um export VM minimo e inspeciona somente o primeiro chunk.",
    )
    contract.add_argument("--profile", required=True)
    contract.add_argument("--env-file", default=".env")
    contract.add_argument("--filters")
    contract.add_argument("--num-assets", type=int, default=50)
    contract.add_argument(
        "--select-properties",
        action="store_true",
        help=(
            "Experimental: usa properties seletivas. Desativado por padrao, "
            "pois o tenant validado falhou ao processar todos os chunks."
        ),
    )
    contract.add_argument(
        "--export-uuid",
        help="Retoma um export existente sem iniciar outro job.",
    )
    contract.add_argument(
        "--confirm-live-api",
        action="store_true",
        help="Confirma que este comando pode iniciar um export real no tenant.",
    )
    contract.set_defaults(handler=command_contract_check)

    asset_contract = subparsers.add_parser(
        "contract-check-assets",
        help="Executa um export de ativos v2 e inspeciona somente o primeiro chunk.",
    )
    asset_contract.add_argument("--profile", required=True)
    asset_contract.add_argument("--env-file", default=".env")
    asset_contract.add_argument("--filters")
    asset_contract.add_argument("--chunk-size", type=int, default=100)
    asset_contract.add_argument("--export-uuid")
    asset_contract.add_argument("--confirm-live-api", action="store_true")
    asset_contract.set_defaults(handler=command_contract_check_assets)

    was_contract = subparsers.add_parser(
        "contract-check-was",
        help="Executa um export WAS do mes anterior e inspeciona apenas o primeiro chunk.",
    )
    was_contract.add_argument("--profile", required=True)
    was_contract.add_argument("--env-file", default=".env")
    was_contract.add_argument("--reference-at")
    was_contract.add_argument("--num-assets", type=int, default=50)
    was_contract.add_argument("--export-uuid")
    was_contract.add_argument("--confirm-live-api", action="store_true")
    was_contract.set_defaults(handler=command_contract_check_was)

    link_contract = subparsers.add_parser(
        "contract-check-link",
        help="Valida o vinculo asset.id/finding.asset.uuid sem exibir valores dos registros.",
    )
    link_contract.add_argument("--profile", required=True)
    link_contract.add_argument("--env-file", default=".env")
    link_contract.add_argument("--asset-export-uuid", required=True)
    link_contract.add_argument("--vm-export-uuid", required=True)
    link_contract.add_argument("--confirm-live-api", action="store_true")
    link_contract.set_defaults(handler=command_contract_check_link)

    collect = subparsers.add_parser("collect-vm", help="Coleta e persiste um snapshot VM raw.")
    collect.add_argument("--profile", required=True)
    collect.add_argument("--env-file", default=".env")
    collect.add_argument("--filters")
    collect.add_argument("--num-assets", type=int, default=1000)
    collect.add_argument("--output-root", default="data")
    collect.add_argument("--run-id")
    collect.add_argument("--include-software-vulns", action="store_true")
    collect.add_argument(
        "--select-properties",
        action="store_true",
        help="Experimental; o tenant validado nao processou chunks seletivos.",
    )
    collect.add_argument(
        "--include-output",
        action="store_true",
        help="Inclui Plugin Output de forma explicita; desligado por padrao.",
    )
    collect.set_defaults(handler=command_collect_vm)

    phase3 = subparsers.add_parser(
        "collect-phase3",
        help="Coleta ativos e findings no mesmo run e publica o snapshot normalizado.",
    )
    phase3.add_argument("--profile", required=True)
    phase3.add_argument("--env-file", default=".env")
    phase3.add_argument("--output-root", default="data")
    phase3.add_argument("--run-id")
    phase3.add_argument("--asset-filters")
    phase3.add_argument("--finding-filters")
    phase3.add_argument("--asset-chunk-size", type=int, default=1000)
    phase3.add_argument("--num-assets", type=int, default=1000)
    phase3.add_argument("--asset-export-uuid")
    phase3.add_argument("--vm-export-uuid")
    phase3.add_argument("--include-software-vulns", action="store_true")
    phase3.add_argument("--include-output", action="store_true")
    phase3.add_argument("--confirm-live-api", action="store_true")
    phase3.set_defaults(handler=command_collect_phase3)

    report_dataset = subparsers.add_parser(
        "build-report-dataset",
        help="Constroi um dataset de período a partir de snapshot normalizado existente.",
    )
    report_dataset.add_argument("--profile", required=True)
    report_dataset.add_argument("--run-id", required=True)
    report_dataset.add_argument("--output-root", default="data")
    report_dataset.add_argument(
        "--mode", choices=("automatic", "manual"), default="automatic"
    )
    report_dataset.add_argument("--reference-at")
    report_dataset.add_argument("--days", type=int)
    report_dataset.add_argument("--start-at")
    report_dataset.add_argument("--end-at")
    report_dataset.add_argument("--include-output", action="store_true")
    report_dataset.add_argument("--history-database")
    report_dataset.add_argument(
        "--database-env-file", default="credentials/database.env"
    )
    report_dataset.add_argument("--history-export-csv")
    report_dataset.add_argument("--skip-history", action="store_true")
    report_dataset.set_defaults(handler=command_build_report_dataset)

    history = subparsers.add_parser(
        "publish-history",
        help=(
            "Persiste uma competencia no PostgreSQL e publica o dataset enriquecido "
            "com tendencias compativeis."
        ),
    )
    history.add_argument("--profile", required=True)
    history.add_argument("--dataset", required=True)
    history.add_argument("--normalized-findings", required=True)
    history.add_argument("--database", default="data/history/tenable-history.sqlite")
    history.add_argument("--history-database")
    history.add_argument("--database-env-file", default="credentials/database.env")
    history.add_argument("--output", required=True)
    history.add_argument("--export-csv")
    history.set_defaults(handler=command_publish_history)

    history_import = subparsers.add_parser(
        "import-history-csv",
        help="Importa no PostgreSQL um CSV previamente exportado pela Fase 9.",
    )
    history_import.add_argument("--csv", required=True)
    history_import.add_argument(
        "--database", default="data/history/tenable-history.sqlite"
    )
    history_import.add_argument("--history-database")
    history_import.add_argument(
        "--database-env-file", default="credentials/database.env"
    )
    history_import.set_defaults(handler=command_import_history_csv)

    monthly = subparsers.add_parser(
        "collect-monthly",
        help="Coleta, normaliza e publica o mes anterior; fluxo recomendado para agendamento.",
    )
    monthly.add_argument("--profile", required=True)
    monthly.add_argument("--env-file", default=".env")
    monthly.add_argument("--database-env-file", default="credentials/database.env")
    monthly.add_argument("--output-root", default="data")
    monthly.add_argument("--run-id")
    monthly.add_argument("--reference-at")
    monthly.add_argument("--asset-filters")
    monthly.add_argument("--finding-filters")
    monthly.add_argument("--asset-chunk-size", type=int, default=1000)
    monthly.add_argument("--num-assets", type=int, default=1000)
    monthly.add_argument("--asset-export-uuid")
    monthly.add_argument("--vm-export-uuid")
    monthly.add_argument("--was-export-uuid")
    monthly.add_argument("--was-num-assets", type=int, default=1000)
    monthly.add_argument("--include-software-vulns", action="store_true")
    monthly.add_argument("--include-output", action="store_true")
    monthly.add_argument("--history-database")
    monthly.add_argument("--history-export-csv")
    monthly.add_argument("--skip-history", action="store_true")
    monthly_tag_scope = monthly.add_mutually_exclusive_group()
    monthly_tag_scope.add_argument(
        "--select-tags",
        action="store_true",
        help="Lista as tags do tenant e abre uma selecao multipla interativa.",
    )
    monthly_tag_scope.add_argument(
        "--tag",
        dest="tags",
        action="append",
        help="Tag por UUID ou 'Categoria: Valor'; pode ser repetida para automacao.",
    )
    monthly.add_argument("--confirm-live-api", action="store_true")
    monthly.set_defaults(handler=command_collect_monthly)

    manual = subparsers.add_parser(
        "collect-manual",
        help=(
            "Coleta pontual; por padrão usa um mês móvel até a execução e aceita "
            "--days ou intervalo explícito."
        ),
    )
    manual.add_argument("--profile", required=True)
    manual.add_argument("--env-file", default=".env")
    manual.add_argument("--database-env-file", default="credentials/database.env")
    manual.add_argument("--output-root", default="data")
    manual.add_argument("--run-id")
    manual.add_argument("--reference-at")
    manual.add_argument("--days", type=int)
    manual.add_argument("--start-at")
    manual.add_argument("--end-at")
    manual.add_argument("--asset-filters")
    manual.add_argument("--finding-filters")
    manual.add_argument("--asset-chunk-size", type=int, default=1000)
    manual.add_argument("--num-assets", type=int, default=1000)
    manual.add_argument("--asset-export-uuid")
    manual.add_argument("--vm-export-uuid")
    manual.add_argument("--was-export-uuid")
    manual.add_argument("--was-num-assets", type=int, default=1000)
    manual.add_argument("--include-software-vulns", action="store_true")
    manual.add_argument("--include-output", action="store_true")
    manual.add_argument("--history-database")
    manual.add_argument("--history-export-csv")
    manual.add_argument("--skip-history", action="store_true")
    manual_tag_scope = manual.add_mutually_exclusive_group()
    manual_tag_scope.add_argument(
        "--select-tags",
        action="store_true",
        help="Lista as tags do tenant e abre uma selecao multipla interativa.",
    )
    manual_tag_scope.add_argument(
        "--tag",
        dest="tags",
        action="append",
        help="Tag por UUID ou 'Categoria: Valor'; pode ser repetida para automacao.",
    )
    manual.add_argument("--confirm-live-api", action="store_true")
    manual.set_defaults(handler=command_collect_manual)

    template = subparsers.add_parser(
        "build-base-template",
        help="Reconstrói o template Word corporativo sanitizado da Fase 5.",
    )
    template.add_argument(
        "--assets-dir",
        default="templates/corporate/assets",
        help="Diretório com logotipos e grafismo aprovados.",
    )
    template.add_argument(
        "--output",
        default="templates/corporate/base-v1.docx",
    )
    template.set_defaults(handler=command_build_base_template)

    base_docx = subparsers.add_parser(
        "generate-base-docx",
        help="Gera o DOCX-base somente a partir do perfil e do report-dataset.json.",
    )
    base_docx.add_argument("--profile", required=True)
    base_docx.add_argument("--dataset", required=True)
    base_docx.add_argument(
        "--template",
        default="templates/corporate/base-v1.docx",
    )
    base_docx.add_argument("--output", required=True)
    base_docx.add_argument(
        "--mask-sensitive",
        action="store_true",
        help="Mantém IP Address e Asset Name vazios no documento gerado.",
    )
    base_docx.set_defaults(handler=command_generate_base_docx)

    full_base_docx = subparsers.add_parser(
        "generate-full-base-docx",
        help="Gera o primeiro relatório-base completo da Fase 6.",
    )
    full_base_docx.add_argument("--profile", required=True)
    full_base_docx.add_argument("--dataset", required=True)
    full_base_docx.add_argument(
        "--template",
        default="templates/corporate/base-v1.docx",
    )
    full_base_docx.add_argument(
        "--assets-dir",
        default="templates/corporate/assets",
    )
    full_base_docx.add_argument("--output", required=True)
    full_base_docx.add_argument(
        "--mask-sensitive",
        action="store_true",
        help="Mantém IP, Asset Name e hosts vazios no documento gerado.",
    )
    full_base_docx.set_defaults(handler=command_generate_full_base_docx)

    custom_docx = subparsers.add_parser(
        "generate-customizations-docx",
        help="Gera o segundo DOCX somente com módulos customizados habilitados.",
    )
    custom_docx.add_argument("--profile", required=True)
    custom_docx.add_argument("--dataset", required=True)
    custom_docx.add_argument("--template", default="templates/corporate/base-v1.docx")
    custom_docx.add_argument("--output", required=True)
    custom_docx.add_argument("--mask-sensitive", action="store_true")
    custom_docx.add_argument("--history-database")
    custom_docx.add_argument("--use-history", action="store_true")
    custom_docx.add_argument(
        "--database-env-file", default="credentials/database.env"
    )
    custom_docx.add_argument("--normalized-findings")
    custom_docx.add_argument("--history-dataset-output")
    custom_docx.add_argument("--history-export-csv")
    custom_docx.set_defaults(handler=command_generate_customizations_docx)

    report_pair = subparsers.add_parser(
        "generate-report-pair",
        help="Gera o DOCX-base fiel e o DOCX separado de customizações.",
    )
    report_pair.add_argument("--profile", required=True)
    report_pair.add_argument("--dataset", required=True)
    report_pair.add_argument("--template", default="templates/corporate/base-v1.docx")
    report_pair.add_argument("--assets-dir", default="templates/corporate/assets")
    report_pair.add_argument("--base-output", required=True)
    report_pair.add_argument("--custom-output", required=True)
    report_pair.add_argument("--mask-sensitive", action="store_true")
    report_pair.set_defaults(handler=command_generate_report_pair)

    run_client = subparsers.add_parser(
        "run-client",
        help="Executa coleta, historico, os dois DOCX e a publicacao controlada de um cliente.",
    )
    _add_complete_collection_arguments(run_client)
    run_client.add_argument(
        "--no-cleanup-after-publish",
        dest="cleanup_after_publish",
        action="store_false",
        help="Mantém temporários após a publicação; use somente para diagnóstico.",
    )
    run_client.set_defaults(cleanup_after_publish=True)
    run_client.set_defaults(handler=command_run_client)

    validate_orchestration = subparsers.add_parser(
        "validate-orchestration",
        help="Valida a configuracao multi-cliente sem acessar APIs.",
    )
    validate_orchestration.add_argument("--config", required=True)
    validate_orchestration.set_defaults(handler=command_validate_orchestration)

    orchestrate = subparsers.add_parser(
        "orchestrate",
        help="Executa varios clientes em processos isolados, automaticamente ou pontualmente.",
    )
    orchestrate.add_argument("--config", required=True)
    orchestrate.add_argument("--mode", choices=("automatic", "manual"), default="automatic")
    orchestrate.add_argument("--reference-at")
    orchestrate.add_argument("--days", type=int)
    orchestrate.add_argument("--start-at")
    orchestrate.add_argument("--end-at")
    orchestrate.add_argument(
        "--client",
        dest="clients",
        action="append",
        help="Executa somente este client_id; pode ser repetido.",
    )
    orchestrate.add_argument("--max-parallel", type=int)
    orchestrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Cria plano, comandos e logs sem acessar APIs nem gerar relatorios.",
    )
    retention_group = orchestrate.add_mutually_exclusive_group()
    retention_group.add_argument(
        "--apply-retention",
        dest="apply_retention",
        action="store_true",
        help="Aplica a retenção por camadas (comportamento padrão).",
    )
    retention_group.add_argument(
        "--no-apply-retention",
        dest="apply_retention",
        action="store_false",
        help="Somente planeja a retenção nesta execução, sem remover artefatos.",
    )
    orchestrate.set_defaults(apply_retention=True)
    orchestrate.add_argument("--confirm-live-api", action="store_true")
    orchestrate.set_defaults(handler=command_orchestrate)

    database_bootstrap = subparsers.add_parser(
        "database-bootstrap",
        help="Cria o papel, o banco e o schema PostgreSQL com menor privilegio.",
    )
    database_bootstrap.add_argument(
        "--database-env-file", default="credentials/database.env"
    )
    database_bootstrap.add_argument(
        "--admin-env-file", default="credentials/postgresql-admin.env"
    )
    database_bootstrap.set_defaults(handler=command_database_bootstrap)

    database_migrate = subparsers.add_parser(
        "database-migrate",
        help="Aplica migrations versionadas ao PostgreSQL configurado.",
    )
    database_migrate.add_argument(
        "--database-env-file", default="credentials/database.env"
    )
    database_migrate.set_defaults(handler=command_database_migrate)

    database_status = subparsers.add_parser(
        "database-status",
        help="Testa a conexao e mostra migrations e contagens sem segredos.",
    )
    database_status.add_argument(
        "--database-env-file", default="credentials/database.env"
    )
    database_status.set_defaults(handler=command_database_status)

    backfill_main = subparsers.add_parser(
        "backfill-report-main",
        help="Planeja ou aplica a referência MAIN inicial para relatórios existentes.",
    )
    backfill_main.add_argument(
        "--database-env-file", default="credentials/database.env"
    )
    backfill_mode = backfill_main.add_mutually_exclusive_group()
    backfill_mode.add_argument(
        "--dry-run", dest="apply", action="store_false",
        help="Somente mostra promoções, ambiguidades e inválidos (padrão).",
    )
    backfill_mode.add_argument(
        "--apply", dest="apply", action="store_true",
        help="Aplica somente as promoções inequívocas do plano.",
    )
    backfill_main.set_defaults(apply=False, handler=command_backfill_report_main)

    legacy_migration = subparsers.add_parser(
        "migrate-legacy-state",
        help="Importa SQLite legado, manifestos e cataloga artefatos no PostgreSQL.",
    )
    legacy_migration.add_argument(
        "--database-env-file", default="credentials/database.env"
    )
    legacy_migration.add_argument(
        "--root",
        action="append",
        default=None,
        help="Raiz a migrar/catalogar; pode ser repetida. Padrao: data e analysis_artifacts.",
    )
    legacy_migration.set_defaults(handler=command_migrate_legacy_state)

    serve_web = subparsers.add_parser(
        "serve-web",
        help="Inicia o painel web local para clientes, fila e relatorios.",
    )
    serve_web.add_argument("--project-root", default=".")
    serve_web.add_argument("--config", default="orchestration/clients.json")
    serve_web.add_argument("--host", default="127.0.0.1")
    serve_web.add_argument("--port", type=int, default=8765)
    serve_web.add_argument("--open-browser", action="store_true")
    serve_web.set_defaults(handler=command_serve_web)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    try:
        return int(args.handler(args))
    except (
        ProfileError,
        EnvironmentError,
        CredentialError,
        ApiError,
        ExportTimeoutError,
        FileExistsError,
        ValueError,
        RuntimeError,
    ) as exc:
        failure = classify_failure(exc)
        if isinstance(exc, (CredentialError,)):
            failure = OperationalFailure(
                FailureCode.TENABLE_AUTH_INVALID,
                failure.message,
                False,
            )
        elif isinstance(exc, ProfileError):
            failure = OperationalFailure(
                FailureCode.PROFILE_INVALID,
                failure.message,
                False,
            )
        print(json.dumps(failure.to_dict(), ensure_ascii=False))
        print(f"erro: {failure.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
