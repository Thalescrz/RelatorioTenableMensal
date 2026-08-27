from tenable_reports.domain.reporting import explicit_reporting_period, previous_calendar_month
from tenable_reports.presentation.report_filenames import (
    cloud_report_filename,
    period_suffix,
    report_filename,
    tag_report_filename,
)


def july_period():
    return previous_calendar_month(
        reference_at="2026-08-01T00:00:00-03:00",
        timezone_name="America/Fortaleza",
    )


def range_period(start: str, end: str):
    return explicit_reporting_period(
        start_at=start,
        end_at=end,
        reference_at="2026-03-01T00:00:00-03:00" if end < "2026-03" else "2026-09-01T00:00:00-03:00",
        timezone_name="America/Fortaleza",
    )


def test_monthly_filename_uses_portuguese_abbreviation() -> None:
    assert report_filename("CLIENTE", july_period(), "base") == (
        "[CLIENTE] Relatório de Vulnerabilidades Tenable JUL26.docx"
    )


def test_custom_monthly_filename() -> None:
    assert report_filename("CLIENTE", july_period(), "custom") == (
        "[CLIENTE] Inteligência e Customizações Tenable JUL26.docx"
    )


def test_partial_range_uses_inclusive_last_day() -> None:
    assert report_filename(
        "CLIENTE", range_period("2026-07-15", "2026-08-15"), "base"
    ) == "[CLIENTE] Relatório de Vulnerabilidades Tenable 15JUL26-14AGO26.docx"


def test_cross_year_range_keeps_both_years() -> None:
    assert period_suffix(range_period("2025-12-01", "2026-02-01")) == "DEZ25-JAN26"


def test_windows_invalid_characters_are_removed_only_from_filename() -> None:
    assert report_filename('CLIENTE: NORTE/1', july_period(), "base").startswith(
        "[CLIENTE NORTE1]"
    )


def test_tag_filename_contains_category_value_and_period() -> None:
    assert tag_report_filename(
        "CLIENTE K",
        july_period(),
        "Equipe",
        "Infraestrutura",
        "tag-a",
    ) == (
        "[CLIENTE K] Relatório de Vulnerabilidades Tenable "
        "TAG Equipe - Infraestrutura JUL26.docx"
    )


def test_tag_filename_uses_uuid_when_sanitized_label_is_empty() -> None:
    name = tag_report_filename(
        "Cliente",
        july_period(),
        "///",
        "***",
        "12345678-abcd",
    )
    assert "12345678" in name
    assert name.endswith("JUL26.docx")


def test_cloud_prototype_filenames_are_distinct_and_windows_safe() -> None:
    assert cloud_report_filename("CLIENTE", july_period(), "base") == (
        "[CLIENTE] Relatório Tenable Cloud Security JUL26 - MODELO BASE.docx"
    )
    assert cloud_report_filename("CLIENTE", july_period(), "expanded") == (
        "[CLIENTE] Relatório Tenable Cloud Security JUL26 - MODELO AMPLIADO.docx"
    )
    assert cloud_report_filename(
        "CLIENTE: CLOUD/1",
        july_period(),
        "base",
    ).startswith("[CLIENTE CLOUD1]")