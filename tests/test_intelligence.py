from __future__ import annotations

from dataclasses import replace

from tenable_reports.domain.intelligence import (
    IntelligenceStatus,
    build_attack_vectors,
    build_current_intelligence,
    build_eol_data,
    build_plugin_family,
    build_scan_auth_health,
    load_unsupported_catalog,
)
from tenable_reports.domain.normalization import normalize_and_link
from tenable_reports.domain.reporting import previous_calendar_month

from tests.test_normalization import fixture_assets, fixture_findings


PERIOD = previous_calendar_month(reference_at="2026-08-01T00:00:00-03:00")


def _normalized():
    return normalize_and_link(
        asset_records=fixture_assets(),
        finding_records=fixture_findings(),
        client_id="client-fixture",
    )


def test_scan_auth_health_classifies_the_reconciled_observed_population() -> None:
    result = _normalized()
    observed = replace(
        result.assets[0],
        last_scan_at="2026-07-20T12:00:00Z",
        last_authenticated_scan_at="2026-07-20T12:00:00Z",
    )
    authentication_failure = replace(
        result.assets[1],
        last_scan_at="2026-07-21T12:00:00Z",
        last_authenticated_scan_at=None,
    )
    assert build_scan_auth_health((observed, authentication_failure), PERIOD) == {
        "success": 1, "failure": 1, "total": 2,
    }


def test_plugin_family_uses_only_fixed_findings_in_period() -> None:
    finding = replace(
        _normalized().findings[0], state="FIXED", last_fixed_at="2026-07-18T12:00:00Z"
    )
    assert build_plugin_family((finding,), PERIOD) == [{"family": "General", "total": 1}]


def test_eol_requires_explicit_catalog_signal_and_records_provenance() -> None:
    result = _normalized()
    explicit = replace(result.findings[0], plugin_name="Product end-of-life detection")
    ordinary = replace(result.findings[1], state="OPEN", plugin_name="Old product version")
    assets, software, evidence = build_eol_data(
        (explicit, ordinary), result.assets, load_unsupported_catalog()
    )
    assert len(assets) == 1
    assert [row["plugin_id"] for row in software] == [100001]
    assert evidence[0]["field"] == "plugin_name"


def test_attack_vectors_group_exploitable_and_frameworks() -> None:
    finding = replace(
        _normalized().findings[0],
        cvss_attack_vector="NETWORK",
        exploit_frameworks=("Metasploit",),
    )
    assert build_attack_vectors((finding,)) == [
        {"framework": "Exploitable", "local": 0, "network": 1, "adjacent_network": 0, "physical": 0},
        {"framework": "Metasploit", "local": 0, "network": 1, "adjacent_network": 0, "physical": 0},
    ]


def test_current_intelligence_distinguishes_no_occurrences_from_unavailable() -> None:
    result = build_current_intelligence(
        assets=(), findings=(), was_findings=(), period=PERIOD,
        open_collected=True, fixed_collected=True, was_collected=False,
    )
    assert result.statuses["vm_eol_software"] == IntelligenceStatus.NO_OCCURRENCES
    assert result.statuses["was_unsupported_tech"] == IntelligenceStatus.DATA_UNAVAILABLE
    assert result.provenance["unsupported_catalog_version"] == "unsupported-signals-v1"
