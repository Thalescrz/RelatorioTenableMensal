from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


CLIENT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
REQUIRED_BASE_MODULES = (
    "summary",
    "infrastructure",
    "vm_top5",
    "was",
    "was_top5",
)
SUPPORTED_INTELLIGENCE_MODULES = (
    "vm_monthly_volume",
    "vm_previous_period_delta",
    "vm_network_comparison",
    "scan_auth_health",
    "vm_plugin_family",
    "vm_eol_software",
    "vm_executive_evolution",
    "vm_monthly_evolution",
    "cloud_container_images",
    "vm_exploit_vector",
    "was_unsupported_tech",
)
INTELLIGENCE_MODULE_CAPABILITIES = {
    "cloud_container_images": "cloud_security",
    "was_unsupported_tech": "was",
}
SECRET_MARKERS = (
    "access_key",
    "secret_key",
    "api_key",
    "api_secret",
    "password",
    "token",
    "tenable_access",
    "tenable_secret",
)


class ProfileError(ValueError):
    """Perfil de cliente invalido."""


def _reject_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(marker in normalized for marker in SECRET_MARKERS):
                raise ProfileError(f"Perfis nao podem conter segredos ({path}.{key}).")
            _reject_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_secrets(child, f"{path}[{index}]")


def _as_string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ProfileError(f"{field_name} deve ser uma lista de textos.")
    return tuple(dict.fromkeys(item.strip() for item in value if item.strip()))


def _as_bool(value: Any, field_name: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ProfileError(f"{field_name} deve ser booleano.")
    return value


@dataclass(frozen=True, slots=True)
class VmScope:
    asset_groups: tuple[str, ...] = ()
    include_unlicensed: bool = False


@dataclass(frozen=True, slots=True)
class WasScope:
    enabled: bool = False
    application_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CloudSecurityScope:
    enabled: bool = False
    environment: str = "global"
    layout: str = "comparison"


@dataclass(frozen=True, slots=True)
class TagReportSelection:
    tag_uuid: str
    category_uuid: str
    category_name: str
    value: str
    generate_report: bool = True
    include_temporal_comparison: bool = False


@dataclass(frozen=True, slots=True)
class TagReportsConfig:
    enabled: bool = False
    tags: tuple[TagReportSelection, ...] = ()


@dataclass(frozen=True, slots=True)
class ReportConfig:
    type: str = "vulnerabilities"
    base_modules: tuple[str, ...] = REQUIRED_BASE_MODULES
    intelligence_modules: tuple[str, ...] = ()
    tag_reports: TagReportsConfig = field(default_factory=TagReportsConfig)
    legacy_network_comparison_tags: tuple[str, ...] = ()

    @property
    def network_comparison_tags(self) -> tuple[str, ...]:
        return self.legacy_network_comparison_tags


@dataclass(frozen=True, slots=True)
class PresentationConfig:
    locale: str = "pt-BR"
    vm_top5_include_output: bool = False
    was_top5_include_output: bool = False
    show_source_filters: bool = False


@dataclass(frozen=True, slots=True)
class VmExportConfig:
    strategy: str = "combined"
    num_assets_per_chunk: int = 1000
    selective_properties: str = "disabled"
    historical_source: str = "legacy"
    historical_fallback: str = "warn_legacy"
    manual_no_progress_seconds: int = 900
    automatic_no_progress_seconds: int = 1800


@dataclass(frozen=True, slots=True)
class ReportingConfig:
    timezone: str = "America/Fortaleza"
    default_period: str = "previous_calendar_month"
    manual_default_period: str = "rolling_calendar_month"
    include_info_severity: bool = False
    top_assets_limit: int = 10
    top_vulnerabilities_limit: int = 5
    late_collection_grace_days: int = 1
    vm_export: VmExportConfig = field(default_factory=VmExportConfig)


@dataclass(frozen=True, slots=True)
class ClientProfile:
    schema_version: int
    client_id: str
    display_name: str
    tenant_id: str
    report: ReportConfig = field(default_factory=ReportConfig)
    vm_scope: VmScope = field(default_factory=VmScope)
    was_scope: WasScope = field(default_factory=WasScope)
    cloud_security_scope: CloudSecurityScope = field(default_factory=CloudSecurityScope)
    presentation: PresentationConfig = field(default_factory=PresentationConfig)
    reporting: ReportingConfig = field(default_factory=ReportingConfig)

    @staticmethod
    def _tag_reports(value: Any) -> TagReportsConfig:
        if value is None:
            return TagReportsConfig()
        if not isinstance(value, dict):
            raise ProfileError("report.tag_reports deve ser um objeto JSON.")
        enabled = _as_bool(
            value.get("enabled"),
            "report.tag_reports.enabled",
        )
        raw_tags = value.get("tags")
        if raw_tags is None:
            raw_tags = []
        if not isinstance(raw_tags, list) or any(
            not isinstance(item, dict) for item in raw_tags
        ):
            raise ProfileError("report.tag_reports.tags deve ser uma lista de objetos.")
        tags: list[TagReportSelection] = []
        seen: set[str] = set()
        for index, item in enumerate(raw_tags):
            tag_uuid = str(item.get("tag_uuid") or "").strip()
            category_uuid = str(item.get("category_uuid") or "").strip()
            category_name = str(item.get("category_name") or "").strip()
            tag_value = str(item.get("value") or "").strip()
            if not tag_uuid or not category_name or not tag_value:
                raise ProfileError(
                    "Cada TAG requer tag_uuid, category_name e value preenchidos."
                )
            if tag_uuid in seen:
                raise ProfileError(f"UUID de TAG duplicado: {tag_uuid}.")
            seen.add(tag_uuid)
            generate_report = _as_bool(
                item.get("generate_report"),
                f"report.tag_reports.tags[{index}].generate_report",
                default=True,
            )
            include_comparison = _as_bool(
                item.get("include_temporal_comparison"),
                f"report.tag_reports.tags[{index}].include_temporal_comparison",
            )
            if include_comparison and not generate_report:
                raise ProfileError(
                    "O comparativo temporal da TAG requer o relatorio da TAG habilitado."
                )
            tags.append(
                TagReportSelection(
                    tag_uuid=tag_uuid,
                    category_uuid=category_uuid,
                    category_name=category_name,
                    value=tag_value,
                    generate_report=generate_report,
                    include_temporal_comparison=include_comparison,
                )
            )
        return TagReportsConfig(enabled=enabled, tags=tuple(tags))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ClientProfile":
        _reject_secrets(data)
        if data.get("schema_version") != 1:
            raise ProfileError("schema_version deve ser 1.")
        client_id = str(data.get("client_id", "")).strip()
        if not CLIENT_ID_PATTERN.fullmatch(client_id):
            raise ProfileError("client_id deve usar 3-64 caracteres: a-z, 0-9, _ ou -.")
        display_name = str(data.get("display_name", "")).strip()
        tenant_id = str(data.get("tenant_id", "")).strip()
        if not display_name or not tenant_id:
            raise ProfileError("display_name e tenant_id sao obrigatorios.")

        report_data = data.get("report") or {}
        scope_data = data.get("scope") or {}
        vm_data = scope_data.get("vm") or {}
        was_data = scope_data.get("was") or {}
        cloud_security_data = scope_data.get("cloud_security") or {}
        presentation_data = data.get("presentation") or {}
        reporting_data = data.get("reporting") or {}
        vm_export_data = reporting_data.get("vm_export")
        if vm_export_data is None:
            vm_export_data = {}
        if not isinstance(vm_export_data, dict):
            raise ProfileError("reporting.vm_export deve ser um objeto JSON.")
        if not all(isinstance(item, dict) for item in (
            report_data,
            scope_data,
            vm_data,
            was_data,
            cloud_security_data,
            presentation_data,
            reporting_data,
            vm_export_data,
        )):
            raise ProfileError("report, scope e presentation devem ser objetos JSON.")

        report_type = str(report_data.get("type", "vulnerabilities")).strip()
        if report_type != "vulnerabilities":
            raise ProfileError("A Fase 2 suporta apenas report.type=vulnerabilities.")
        legacy_vm_tags = _as_string_list(vm_data.get("tags"), "scope.vm.tags")
        if legacy_vm_tags:
            raise ProfileError(
                "scope.vm.tags filtraria a coleta geral. Use "
                "report.network_comparison_tags para o comparativo temporal por rede."
            )

        base_modules = (
            _as_string_list(report_data.get("base_modules"), "report.base_modules")
            if "base_modules" in report_data
            else REQUIRED_BASE_MODULES
        )
        if set(base_modules) != set(REQUIRED_BASE_MODULES) or len(base_modules) != len(
            REQUIRED_BASE_MODULES
        ):
            raise ProfileError(
                "report.base_modules deve manter exatamente o nucleo padrao: "
                + ", ".join(REQUIRED_BASE_MODULES)
                + ". Customizacoes pertencem a report.intelligence_modules."
            )
        intelligence_modules = _as_string_list(
            report_data.get("intelligence_modules"),
            "report.intelligence_modules",
        )
        unsupported_modules = sorted(
            set(intelligence_modules) - set(SUPPORTED_INTELLIGENCE_MODULES)
        )
        if unsupported_modules:
            raise ProfileError(
                "report.intelligence_modules contem modulo(s) desconhecido(s): "
                + ", ".join(unsupported_modules)
            )
        was_enabled = _as_bool(scope_data.get("was", {}).get("enabled"), "scope.was.enabled")
        cloud_security_enabled = _as_bool(
            cloud_security_data.get("enabled"),
            "scope.cloud_security.enabled",
        )
        cloud_environment = str(
            cloud_security_data.get("environment", "global")
        ).strip().lower()
        if cloud_environment not in {"global", "us_gov"}:
            raise ProfileError(
                "scope.cloud_security.environment deve ser global ou us_gov."
            )
        cloud_layout = str(
            cloud_security_data.get("layout", "comparison")
        ).strip().lower()
        if cloud_layout not in {"comparison", "base", "expanded"}:
            raise ProfileError(
                "scope.cloud_security.layout deve ser comparison, base ou expanded."
            )
        missing_capabilities = []
        for module in intelligence_modules:
            capability = INTELLIGENCE_MODULE_CAPABILITIES.get(module)
            if capability == "was" and not was_enabled:
                missing_capabilities.append(f"{module} requer scope.was.enabled=true")
            if capability == "cloud_security" and not cloud_security_enabled:
                missing_capabilities.append(
                    f"{module} requer scope.cloud_security.enabled=true"
                )
        if missing_capabilities:
            raise ProfileError("; ".join(missing_capabilities) + ".")
        application_ids = _as_string_list(
            was_data.get("application_ids"), "scope.was.application_ids"
        )
        if application_ids and not was_enabled:
            raise ProfileError(
                "scope.was.application_ids requer scope.was.enabled=true."
            )
        vm_top5_include_output = _as_bool(
            presentation_data.get("vm_top5_include_output"),
            "presentation.vm_top5_include_output",
        )
        was_top5_include_output = _as_bool(
            presentation_data.get("was_top5_include_output"),
            "presentation.was_top5_include_output",
        )
        show_source_filters = _as_bool(
            presentation_data.get("show_source_filters"),
            "presentation.show_source_filters",
        )
        if was_top5_include_output and not was_enabled:
            raise ProfileError(
                "presentation.was_top5_include_output requer scope.was.enabled=true."
            )

        default_period = str(
            reporting_data.get("default_period", "previous_calendar_month")
        ).strip()
        if default_period != "previous_calendar_month":
            raise ProfileError("reporting.default_period deve ser previous_calendar_month.")
        manual_default_period = str(
            reporting_data.get("manual_default_period", "rolling_calendar_month")
        ).strip()
        if manual_default_period != "rolling_calendar_month":
            raise ProfileError(
                "reporting.manual_default_period deve ser rolling_calendar_month."
            )
        top_assets_limit = int(reporting_data.get("top_assets_limit", 10))
        top_vulnerabilities_limit = int(reporting_data.get("top_vulnerabilities_limit", 5))
        late_collection_grace_days = int(reporting_data.get("late_collection_grace_days", 1))
        vm_export_strategy = str(
            vm_export_data.get("strategy", "combined")
        ).strip().lower()
        if vm_export_strategy not in {"combined", "split"}:
            raise ProfileError(
                "reporting.vm_export.strategy deve ser combined ou split."
            )
        try:
            vm_num_assets_per_chunk = int(
                vm_export_data.get("num_assets_per_chunk", 1000)
            )
        except (TypeError, ValueError) as exc:
            raise ProfileError(
                "reporting.vm_export.num_assets_per_chunk deve ser inteiro."
            ) from exc
        if not 50 <= vm_num_assets_per_chunk <= 5000:
            raise ProfileError(
                "reporting.vm_export.num_assets_per_chunk deve estar entre 50 e 5000."
            )
        vm_selective_properties = str(
            vm_export_data.get("selective_properties", "disabled")
        ).strip().lower()
        if vm_selective_properties not in {"disabled", "validation", "enabled"}:
            raise ProfileError(
                "reporting.vm_export.selective_properties deve ser "
                "disabled, validation ou enabled."
            )
        vm_historical_source = str(
            vm_export_data.get("historical_source", "legacy")
        ).strip().lower()
        if vm_historical_source not in {"legacy", "inventory_beta"}:
            raise ProfileError(
                "reporting.vm_export.historical_source deve ser "
                "legacy ou inventory_beta."
            )
        vm_historical_fallback = str(
            vm_export_data.get("historical_fallback", "warn_legacy")
        ).strip().lower()
        if vm_historical_fallback not in {"warn_legacy", "fail"}:
            raise ProfileError(
                "reporting.vm_export.historical_fallback deve ser "
                "warn_legacy ou fail."
            )
        try:
            vm_manual_no_progress_seconds = int(
                vm_export_data.get("manual_no_progress_seconds", 900)
            )
            vm_automatic_no_progress_seconds = int(
                vm_export_data.get("automatic_no_progress_seconds", 1800)
            )
        except (TypeError, ValueError) as exc:
            raise ProfileError(
                "reporting.vm_export.*_no_progress_seconds deve ser inteiro."
            ) from exc
        for field_name, value in (
            ("manual_no_progress_seconds", vm_manual_no_progress_seconds),
            ("automatic_no_progress_seconds", vm_automatic_no_progress_seconds),
        ):
            if not 1 <= value <= 86400:
                raise ProfileError(
                    f"reporting.vm_export.{field_name} deve estar entre 1 e 86400."
                )
        if not 1 <= top_assets_limit <= 100:
            raise ProfileError("reporting.top_assets_limit deve estar entre 1 e 100.")
        if not 1 <= top_vulnerabilities_limit <= 50:
            raise ProfileError("reporting.top_vulnerabilities_limit deve estar entre 1 e 50.")
        if not 0 <= late_collection_grace_days <= 31:
            raise ProfileError("reporting.late_collection_grace_days deve estar entre 0 e 31.")

        return cls(
            schema_version=1,
            client_id=client_id,
            display_name=display_name,
            tenant_id=tenant_id,
            report=ReportConfig(
                type=report_type,
                base_modules=REQUIRED_BASE_MODULES,
                intelligence_modules=intelligence_modules,
                tag_reports=cls._tag_reports(report_data.get("tag_reports")),
                legacy_network_comparison_tags=_as_string_list(
                    report_data.get("network_comparison_tags"),
                    "report.network_comparison_tags",
                ),
            ),
            vm_scope=VmScope(
                asset_groups=_as_string_list(
                    vm_data.get("asset_groups"), "scope.vm.asset_groups"
                ),
                include_unlicensed=_as_bool(
                    vm_data.get("include_unlicensed"),
                    "scope.vm.include_unlicensed",
                ),
            ),
            was_scope=WasScope(
                enabled=was_enabled,
                application_ids=application_ids,
            ),
            cloud_security_scope=CloudSecurityScope(
                enabled=cloud_security_enabled,
                environment=cloud_environment,
                layout=cloud_layout,
            ),
            presentation=PresentationConfig(
                locale=str(presentation_data.get("locale", "pt-BR")).strip() or "pt-BR",
                vm_top5_include_output=vm_top5_include_output,
                was_top5_include_output=was_top5_include_output,
                show_source_filters=show_source_filters,
            ),
            reporting=ReportingConfig(
                timezone=str(reporting_data.get("timezone", "America/Fortaleza")).strip()
                or "America/Fortaleza",
                default_period=default_period,
                manual_default_period=manual_default_period,
                include_info_severity=_as_bool(
                    reporting_data.get("include_info_severity"),
                    "reporting.include_info_severity",
                ),
                top_assets_limit=top_assets_limit,
                top_vulnerabilities_limit=top_vulnerabilities_limit,
                late_collection_grace_days=late_collection_grace_days,
                vm_export=VmExportConfig(
                    strategy=vm_export_strategy,
                    num_assets_per_chunk=vm_num_assets_per_chunk,
                    selective_properties=vm_selective_properties,
                    historical_source=vm_historical_source,
                    historical_fallback=vm_historical_fallback,
                    manual_no_progress_seconds=vm_manual_no_progress_seconds,
                    automatic_no_progress_seconds=vm_automatic_no_progress_seconds,
                ),
            ),
        )


def load_client_profile(path: str | Path) -> ClientProfile:
    profile_path = Path(path)
    try:
        data = json.loads(profile_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProfileError(f"Nao foi possivel ler o perfil: {profile_path}") from exc
    except json.JSONDecodeError as exc:
        raise ProfileError(f"Perfil JSON invalido na linha {exc.lineno}.") from exc
    if not isinstance(data, dict):
        raise ProfileError("O perfil deve conter um objeto JSON na raiz.")
    return ClientProfile.from_dict(data)
