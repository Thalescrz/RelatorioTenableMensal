from __future__ import annotations

from PIL import Image, ImageColor

from tenable_reports.presentation.cloud_visuals import (
    BLUE,
    normalize_history_series,
    render_monthly_history_chart,
)

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

def test_single_month_chart_centers_the_real_point(tmp_path) -> None:
    output = tmp_path / "single-month.png"
    rendered = render_monthly_history_chart(
        [
            {
                "period_id": "2026-07",
                "label": "Jul/26",
                "availability": "AVAILABLE",
                "overview": {"vulnerability_occurrences": 7},
            }
        ],
        output,
    )

    assert rendered == output
    with Image.open(output) as image:
        blue = ImageColor.getrgb(BLUE)
        point_pixels = [
            x
            for y in range(150, 611)
            for x in range(image.width)
            if image.getpixel((x, y))[:3] == blue
        ]
    assert point_pixels
    assert min(point_pixels) > 600
    assert max(point_pixels) < 850
