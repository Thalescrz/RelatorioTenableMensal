from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image, ImageColor, ImageFont

from tenable_reports.presentation.font_profiles import (
    FontFace,
    FontProfileError,
    load_image_font,
)
from tenable_reports.presentation.cloud_visuals import (
    BLUE,
    normalize_history_series,
    render_monthly_history_chart,
)
from tests.font_profile_helpers import linux_font_profile

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
        font_profile=linux_font_profile(),
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


@pytest.mark.parametrize("create_invalid_file", [False, True])
def test_image_font_rejects_missing_or_unreadable_font(
    tmp_path: Path,
    create_invalid_file: bool,
) -> None:
    font_path = tmp_path / "invalid-font.ttf"
    if create_invalid_file:
        font_path.write_text("not a TrueType font", encoding="utf-8")
    face = FontFace(
        family="Invalid",
        regular=font_path,
        bold=font_path,
        italic=font_path,
    )

    with pytest.raises(FontProfileError) as error:
        load_image_font(face, size=18)

    assert error.value.code == "FONT_PROFILE_INVALID"
    assert error.value.path == "invalid-font.ttf"
    assert str(tmp_path) not in str(error.value)


def test_parallel_image_fonts_do_not_share_profile_state() -> None:
    profile = linux_font_profile()

    def load_family(face: FontFace) -> str:
        return load_image_font(face, size=20).getname()[0]

    work = [profile.document, profile.alternate] * 12
    with ThreadPoolExecutor(max_workers=6) as executor:
        families = list(executor.map(load_family, work))

    assert families[0::2] == ["Carlito"] * 12
    assert families[1::2] == ["Liberation Sans"] * 12


def test_cloud_chart_uses_injected_profile_without_bitmap_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_default_font():
        raise AssertionError("ImageFont.load_default() não pode ser usado")

    monkeypatch.setattr(ImageFont, "load_default", fail_default_font)
    output = tmp_path / "linux-cloud.png"

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
        font_profile=linux_font_profile(),
    )

    assert rendered == output
