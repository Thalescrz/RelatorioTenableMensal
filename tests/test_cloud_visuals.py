from __future__ import annotations

from tenable_reports.presentation.cloud_visuals import normalize_history_series


def test_history_series_preserves_unavailable_month_as_gap() -> None:
    rows = normalize_history_series(
        [
            {
                "period_id": "2026-05",
                "label": "Mai/26",
                "availability": "AVAILABLE",
                "overview": {"vulnerability_occurrences": 9},
            },
            {
                "period_id": "2026-06",
                "label": "Jun/26",
                "availability": "UNAVAILABLE",
            },
            {
                "period_id": "2026-07",
                "label": "Jul/26",
                "availability": "AVAILABLE",
                "overview": {"vulnerability_occurrences": 7},
            },
        ]
    )

    assert [row.label for row in rows] == ["Mai/26", "Jun/26", "Jul/26"]
    assert [row.value for row in rows] == [9, None, 7]