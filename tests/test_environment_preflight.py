from __future__ import annotations

from pathlib import Path

import pytest

from tenable_reports.application.environment_preflight import (
    CapabilityStatus,
    EnvironmentPreflightError,
    run_environment_preflight,
)
from tenable_reports.config.database import DatabaseConfig
from tenable_reports.infrastructure.libreoffice import (
    LibreOfficeError,
    LibreOfficeInstallation,
)
from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
)
from tenable_reports.presentation.font_profiles import FontProfileError
from tests.font_profile_helpers import linux_font_profile


def _platform(value: RuntimePlatform = RuntimePlatform.LINUX) -> RuntimePlatformInfo:
    return RuntimePlatformInfo(value, value.value, "x86_64")


def _database() -> DatabaseConfig:
    return DatabaseConfig(
        host="127.0.0.1",
        port=5432,
        database="reports",
        user="reports_app",
        password="fixture-secret",
    )


def _office() -> LibreOfficeInstallation:
    return LibreOfficeInstallation(
        executable=Path("soffice"),
        version="26.2.4.2",
        source="path",
    )


def test_preflight_reports_partial_failures_without_exposing_database_details(
    tmp_path: Path,
) -> None:
    writable = tmp_path / "writable"
    blocked = tmp_path / "blocked"
    writable.mkdir()
    blocked.mkdir()

    def database_unavailable(config: DatabaseConfig) -> None:
        assert config.password == "fixture-secret"
        raise RuntimeError("password=fixture-secret host=private.example")

    capabilities = run_environment_preflight(
        platform_info=_platform(),
        persistent_roots=(writable, blocked),
        database_config=_database(),
        font_probe=lambda _: linux_font_profile(),
        office_probe=lambda _: _office(),
        database_probe=database_unavailable,
        write_probe=lambda path: path == writable,
        scheduler_status=CapabilityStatus.WARNING,
        diagnostic_only=True,
    )

    assert capabilities.status is CapabilityStatus.BLOCKED
    assert capabilities.postgresql_status is CapabilityStatus.BLOCKED
    assert capabilities.storage_status is CapabilityStatus.BLOCKED
    assert capabilities.scheduler_status is CapabilityStatus.WARNING
    assert not capabilities.official_publication_ready
    payload = capabilities.to_dict()
    serialized = str(payload)
    assert "fixture-secret" not in serialized
    assert "private.example" not in serialized
    assert {issue["code"] for issue in payload["issues"]} >= {
        "POSTGRESQL_UNAVAILABLE",
        "STORAGE_NOT_WRITABLE",
    }


def test_scheduler_warning_does_not_block_manual_publication(tmp_path: Path) -> None:
    root = tmp_path / "data"
    root.mkdir()

    capabilities = run_environment_preflight(
        platform_info=_platform(),
        persistent_roots=(root,),
        database_config=_database(),
        font_probe=lambda _: linux_font_profile(),
        office_probe=lambda _: _office(),
        database_probe=lambda _: None,
        write_probe=lambda _: True,
        scheduler_status=CapabilityStatus.WARNING,
        diagnostic_only=True,
    )

    assert capabilities.status is CapabilityStatus.WARNING
    assert capabilities.official_publication_ready
    capabilities.require_official_publication()


@pytest.mark.parametrize(
    ("font_probe", "office_probe", "expected_code"),
    [
        (
            lambda _: (_ for _ in ()).throw(FontProfileError("FONT_PROFILE_INVALID")),
            lambda _: _office(),
            "FONT_PROFILE_INVALID",
        ),
        (
            lambda _: linux_font_profile(),
            lambda _: (_ for _ in ()).throw(
                LibreOfficeError("LIBREOFFICE_VERSION_MISMATCH")
            ),
            "LIBREOFFICE_VERSION_MISMATCH",
        ),
    ],
)
def test_official_preflight_fails_closed_before_collection(
    tmp_path: Path,
    font_probe,
    office_probe,
    expected_code: str,
) -> None:
    root = tmp_path / "data"
    root.mkdir()

    with pytest.raises(EnvironmentPreflightError) as error:
        run_environment_preflight(
            platform_info=_platform(),
            persistent_roots=(root,),
            database_config=_database(),
            font_probe=font_probe,
            office_probe=office_probe,
            database_probe=lambda _: None,
            write_probe=lambda _: True,
            diagnostic_only=False,
        )

    assert expected_code in error.value.issue_codes


def test_unsupported_platform_remains_available_for_diagnostics(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    capabilities = run_environment_preflight(
        platform_info=_platform(RuntimePlatform.UNSUPPORTED),
        persistent_roots=(tmp_path,),
        database_config=None,
        font_probe=lambda _: calls.append("font"),
        office_probe=lambda _: calls.append("office"),
        database_probe=lambda _: calls.append("database"),
        write_probe=lambda _: True,
        diagnostic_only=True,
    )

    assert capabilities.status is CapabilityStatus.BLOCKED
    assert capabilities.platform_status is CapabilityStatus.BLOCKED
    assert not capabilities.official_publication_ready
    assert calls == []
    assert "UNSUPPORTED_PLATFORM" in {
        issue.code for issue in capabilities.issues
    }
