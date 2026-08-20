from __future__ import annotations

import pytest

from tenable_reports.domain.fingerprints import (
    FINGERPRINT_SIZE,
    fingerprint_finding_key,
    pack_fingerprints,
    unpack_fingerprints,
)
from tenable_reports.domain.history import HistorySnapshot


def test_fingerprint_is_stable_fixed_size_and_non_reversible() -> None:
    source = "asset-1|19506|443|tcp"
    value = fingerprint_finding_key(source)

    assert len(value) == FINGERPRINT_SIZE == 16
    assert value == fingerprint_finding_key(source)
    assert source.encode("utf-8") not in value
    assert value != fingerprint_finding_key("asset-2|19506|443|tcp")


def test_packed_fingerprints_round_trip_sorted_and_unique() -> None:
    values = [b"b" * 16, b"a" * 16, b"a" * 16]

    packed = pack_fingerprints(values)

    assert unpack_fingerprints(packed) == (b"a" * 16, b"b" * 16)
    assert b"a" * 16 not in packed


def test_pack_rejects_invalid_fingerprint_size() -> None:
    with pytest.raises(ValueError, match="16 bytes"):
        pack_fingerprints([b"short"])


def test_unpack_rejects_corrupt_payload() -> None:
    with pytest.raises(ValueError, match="inválido"):
        unpack_fingerprints(b"not-zlib")


def test_history_rejects_unknown_fingerprint_version() -> None:
    with pytest.raises(ValueError, match="não suportada"):
        HistorySnapshot.from_dict({
            "schema_version": 99,
            "fingerprint_version": "future-v2",
            "snapshot_id": "snapshot-future",
            "run_id": "run-future",
            "period_id": "2026-07",
            "period_start_at": "2026-07-01T03:00:00Z",
            "period_end_at": "2026-08-01T03:00:00Z",
            "generated_at": "2026-08-01T12:00:00Z",
            "compatibility": {
                "client_id": "cliente",
                "tenant_id": "tenant",
                "execution_type": "AUTOMATIC_MONTHLY",
                "period_mode": "PREVIOUS_CALENDAR_MONTH",
                "timezone": "America/Fortaleza",
                "metric_definition_version": "v1",
                "scope_hash": "scope",
            },
            "summary": {"non_mitigated": 1},
            "open_finding_keys": ["future-value"],
            "fixed_finding_keys": [],
            "resurfaced_finding_keys": [],
            "network_tag_snapshots": [],
        })
