"""Deterministic classification of Tenable Cloud remediation text."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Pattern


CORRECTION_RULES_VERSION = "cloud-correction-rules-v1"

CORRECTION_RULES: tuple[tuple[str, Pattern[str]], ...] = (
    (
        "patch_update",
        re.compile(
            r"\b(apply|install|deploy).{0,40}\b"
            r"(patch|hotfix|security update|update)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "version_upgrade",
        re.compile(
            r"\b(upgrade|fixed version|update to version|"
            r"version .{0,20} or later)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "remove_replace",
        re.compile(
            r"\b(remove|uninstall|replace)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "configuration_change",
        re.compile(
            r"\b(configure|configuration|disable|enable|set)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "mitigation",
        re.compile(
            r"\b(mitigat\w*|workaround|restrict|block)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "manual",
        re.compile(
            r"\b(manual(?:ly)?|contact the vendor|review)\b",
            re.IGNORECASE,
        ),
    ),
)

_NEGATED_EVIDENCE = (
    re.compile(
        r"\b(no|not|without)\b.{0,35}\b"
        r"(patch|fix|update|remediation|workaround)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(patch|fix|update|remediation)\b.{0,35}\b"
        r"(unavailable|not available|does not exist)\b",
        re.IGNORECASE,
    ),
)

_EXPLICIT_TYPES = {
    "patch": "patch_update",
    "security patch": "patch_update",
    "update": "patch_update",
    "hotfix": "patch_update",
    "version upgrade": "version_upgrade",
    "upgrade": "version_upgrade",
    "configuration": "configuration_change",
    "configuration change": "configuration_change",
    "remove": "remove_replace",
    "replacement": "remove_replace",
    "mitigation": "mitigation",
    "workaround": "mitigation",
    "manual": "manual",
}


@dataclass(frozen=True, slots=True)
class CloudCorrectionClassification:
    correction_type: str
    origin: str
    rules_version: str
    matched_categories: tuple[str, ...] = ()


def _normalized_explicit(value: str) -> str:
    return " ".join(
        re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split()
    )


def classify_cloud_correction(
    text: str,
    *,
    explicit_type: str | None = None,
) -> CloudCorrectionClassification:
    """Classify without generative inference or ambiguous fallbacks."""

    if explicit_type is not None and str(explicit_type).strip():
        correction_type = _EXPLICIT_TYPES.get(
            _normalized_explicit(explicit_type),
            "undetermined",
        )
        return CloudCorrectionClassification(
            correction_type=correction_type,
            origin="api_explicit",
            rules_version=CORRECTION_RULES_VERSION,
            matched_categories=(
                (correction_type,)
                if correction_type != "undetermined"
                else ()
            ),
        )

    normalized_text = str(text or "").strip()
    if not normalized_text:
        return CloudCorrectionClassification(
            correction_type="undetermined",
            origin="insufficient_evidence",
            rules_version=CORRECTION_RULES_VERSION,
        )
    if any(pattern.search(normalized_text) for pattern in _NEGATED_EVIDENCE):
        return CloudCorrectionClassification(
            correction_type="undetermined",
            origin="local_rule",
            rules_version=CORRECTION_RULES_VERSION,
        )

    matches = tuple(
        correction_type
        for correction_type, pattern in CORRECTION_RULES
        if pattern.search(normalized_text)
    )
    if len(matches) != 1:
        return CloudCorrectionClassification(
            correction_type="undetermined",
            origin="local_rule",
            rules_version=CORRECTION_RULES_VERSION,
            matched_categories=matches,
        )
    return CloudCorrectionClassification(
        correction_type=matches[0],
        origin="local_rule",
        rules_version=CORRECTION_RULES_VERSION,
        matched_categories=matches,
    )


__all__ = [
    "CORRECTION_RULES",
    "CORRECTION_RULES_VERSION",
    "CloudCorrectionClassification",
    "classify_cloud_correction",
]
