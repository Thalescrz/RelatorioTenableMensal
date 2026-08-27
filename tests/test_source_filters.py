from docx import Document
from docx.shared import Pt, RGBColor

from tenable_reports.presentation.source_filters import (
    add_source_filter_note,
    format_source_filter_note,
)


def test_general_filter_note_never_inherits_network_tag_or_secrets() -> None:
    dataset = {"table_provenance": {"tables": {
        "top_assets": {
            "source": "Tenable VM", "period_start_at": "2026-07-01T00:00:00Z",
            "period_end_at": "2026-08-01T00:00:00Z", "states": ["OPEN"],
            "severities": ["CRITICAL", "HIGH"], "access_key": "must-not-leak",
            "tag_value": "Rede secreta", "view": "Explore > Findings > Vulnerabilities",
            "date_field": "Last Seen", "group_by": "Asset", "limit": 10,
            "rule": "contagem por instância de finding",
        },
        "network_tag_snapshots": [{
            "tag_uuid": "tag-a", "source": "Tenable VM",
            "tag_category": "Rede", "tag_value": "Matriz",
            "view": "Explore > Findings > Vulnerabilities",
        }],
    }}}
    general = format_source_filter_note(dataset, "top_assets")
    network = format_source_filter_note(dataset, "network_tag_snapshots", tag_uuid="tag-a")
    assert "Rede secreta" not in general
    assert "must-not-leak" not in general
    assert general.startswith("Validação rápida na Tenable:")
    assert "State = Active, New" in general
    assert "Last Seen = 01/07/2026 a 31/07/2026" in general
    assert "Agrupar por Asset" in general
    assert "Top 10" in general
    assert "Tag = Rede:Matriz" in network


def test_validation_note_is_small_gray_and_not_italic() -> None:
    document = Document()
    dataset = {"table_provenance": {"tables": {
        "overview": {
            "view": "Explore > Findings > Vulnerabilities",
            "period_start_at": "2026-07-01T03:00:00Z",
            "period_end_at": "2026-08-01T03:00:00Z",
            "date_field": "Last Seen",
            "states": ["OPEN", "REOPENED"],
            "severities": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
            "rule": "contagem por instância de finding",
        }
    }}}
    add_source_filter_note(document, dataset, "overview", enabled=True)
    paragraph = document.paragraphs[-1]
    run = paragraph.runs[0]
    assert run.italic is False
    assert run.font.size == Pt(8)
    assert run.font.color.rgb == RGBColor(0x7A, 0x83, 0x8C)


def test_filter_note_displays_exclusive_utc_period_in_report_timezone() -> None:
    dataset = {"table_provenance": {"tables": {"overview": {
        "period_start_at": "2026-07-01T03:00:00Z",
        "period_end_at": "2026-08-01T03:00:00Z",
        "timezone": "America/Fortaleza",
    }}}}
    note = format_source_filter_note(dataset, "overview")
    assert "01/07/2026 a 31/07/2026" in note


def test_period_label_overrides_current_dataset_dates_for_historical_table() -> None:
    dataset = {"table_provenance": {"tables": {"history": {
        "period_start_at": "2026-07-01T03:00:00Z",
        "period_end_at": "2026-08-01T03:00:00Z",
        "timezone": "America/Fortaleza",
        "date_field": "Last Seen",
    }}}}
    note = format_source_filter_note(dataset, "history", period_label="Junho/26")
    assert "Last Seen = Junho/26" in note
    assert "01/07/2026" not in note


def test_compound_note_keeps_state_specific_date_filters_separate() -> None:
    dataset = {"table_provenance": {"tables": {"overview": {
        "view": "Explore > Findings > Vulnerabilities",
        "period_start_at": "2026-07-01T03:00:00Z",
        "period_end_at": "2026-08-01T03:00:00Z",
        "timezone": "America/Fortaleza",
        "severities": ["CRITICAL", "HIGH", "MEDIUM", "LOW"],
        "validation_queries": [
            {
                "label": "Não mitigadas",
                "states": ["OPEN", "REOPENED"],
                "date_fields": ["Last Seen"],
            },
            {
                "label": "Mitigadas",
                "states": ["FIXED"],
                "date_fields": ["Last Fixed"],
            },
        ],
    }}}}

    note = format_source_filter_note(dataset, "overview")

    assert "Não mitigadas: State = Active, New, Resurfaced; Last Seen = 01/07/2026 a 31/07/2026" in note
    assert "Mitigadas: State = Fixed; Last Fixed = 01/07/2026 a 31/07/2026" in note
    assert "State = Fixed; Last Seen" not in note


def test_resurfaced_note_can_require_last_seen_and_resurfaced_date() -> None:
    dataset = {"table_provenance": {"tables": {"resurfaced": {
        "view": "Explore > Findings > Vulnerabilities",
        "period_start_at": "2026-07-01T03:00:00Z",
        "period_end_at": "2026-08-01T03:00:00Z",
        "timezone": "America/Fortaleza",
        "states": ["REOPENED"],
        "date_fields": ["Last Seen", "Resurfaced Date"],
    }}}}

    note = format_source_filter_note(dataset, "resurfaced")

    assert "State = Resurfaced" in note
    assert "Last Seen = 01/07/2026 a 31/07/2026" in note
    assert "Resurfaced Date = 01/07/2026 a 31/07/2026" in note


def test_platform_filters_are_rendered_and_unvalidated_sources_are_omitted() -> None:
    dataset = {"table_provenance": {"tables": {
        "vectors": {
            "view": "Explore > Findings > Vulnerabilities",
            "platform_filters": {"Plugin > Exploit Available": "Yes"},
        },
        "cloud": {
            "view": "Cloud Security > Findings > Container Images",
            "platform_validation_available": False,
        },
    }}}

    assert "Plugin > Exploit Available = Yes" in format_source_filter_note(dataset, "vectors")
    assert format_source_filter_note(dataset, "cloud") is None


def test_compound_historical_note_accepts_one_period_per_query() -> None:
    dataset = {"table_provenance": {"tables": {"movement": {
        "view": "Explore > Findings > Vulnerabilities",
        "validation_queries": [
            {"label": "Consulta 1", "states": ["OPEN", "REOPENED"], "date_fields": ["Last Seen"]},
            {"label": "Consulta 2", "states": ["OPEN", "REOPENED"], "date_fields": ["Last Seen"]},
        ],
    }}}}

    note = format_source_filter_note(
        dataset,
        "movement",
        period_labels=("Junho/26", "Julho/26"),
    )

    assert "Consulta 1: State = Active, New, Resurfaced; Last Seen = Junho/26" in note
    assert "Consulta 2: State = Active, New, Resurfaced; Last Seen = Julho/26" in note


def test_cloud_snapshot_note_uses_collection_timestamp_without_fake_period() -> None:
    dataset = {"table_provenance": {"tables": {"cloud_top_hosts": {
        "view": "Cloud Security > Vulnerability Management",
        "snapshot_collected_at": "2026-08-26T12:00:00Z",
        "platform_filters": {
            "Asset type": "Virtual Machine",
            "Severity": "Critical, High, Medium, Low",
        },
        "group_by": "Resource Id",
        "limit": 10,
        "rule": "fotografia GraphQL atual, CVE deduplicada por recurso",
    }}}}

    note = format_source_filter_note(dataset, "cloud_top_hosts")

    assert "Fotografia coletada em 26/08/2026 12:00 UTC" in note
    assert "Período =" not in note
    assert "Asset type = Virtual Machine" in note
