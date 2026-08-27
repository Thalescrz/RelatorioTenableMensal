from __future__ import annotations

import importlib

import pytest


def _module():
    return importlib.import_module(
        "tenable_reports.application.cloud_corrections"
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Apply the vendor security patch.", "patch_update"),
        ("Upgrade to version 4.8 or later.", "version_upgrade"),
        (
            "Disable anonymous access in the service configuration.",
            "configuration_change",
        ),
        ("Remove the affected package.", "remove_replace"),
        (
            "Restrict network access as a temporary workaround.",
            "mitigation",
        ),
        ("Contact the vendor and review the affected service manually.", "manual"),
    ],
)
def test_local_correction_rules_are_deterministic(
    text: str,
    expected: str,
) -> None:
    result = _module().classify_cloud_correction(text)

    assert result.correction_type == expected
    assert result.origin == "local_rule"
    assert result.rules_version == "cloud-correction-rules-v1"


def test_explicit_type_precedes_local_rules() -> None:
    result = _module().classify_cloud_correction(
        "Disable the service",
        explicit_type="Patch",
    )

    assert result.correction_type == "patch_update"
    assert result.origin == "api_explicit"


def test_conflicting_or_negated_local_evidence_is_not_classified() -> None:
    module = _module()

    assert module.classify_cloud_correction(
        "Upgrade the component or disable the affected service."
    ).correction_type == "undetermined"
    assert module.classify_cloud_correction(
        "No patch is currently available."
    ).correction_type == "undetermined"


def test_empty_correction_is_undetermined_without_fabricated_origin() -> None:
    result = _module().classify_cloud_correction("")

    assert result.correction_type == "undetermined"
    assert result.origin == "insufficient_evidence"
