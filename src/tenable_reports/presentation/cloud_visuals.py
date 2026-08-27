from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont


BLUE = "#2E59FC"
NAVY = "#101326"
GRID = "#D9E0F2"
TEXT = "#101326"
MUTED = "#68728A"
SEVERITY_COLORS = {
    "CRITICAL": "#C00000",
    "HIGH": "#F26B00",
    "MEDIUM": "#E5D900",
    "LOW": "#00B050",
}


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    period_id: str
    label: str
    value: int | None


def _font(size: int, *, bold: bool = False) -> Any:
    candidates = (
        Path(r"C:\Windows\Fonts\arialbd.ttf")
        if bold
        else Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\calibrib.ttf")
        if bold
        else Path(r"C:\Windows\Fonts\calibri.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _number(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_number(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def normalize_history_series(
    history: Sequence[Mapping[str, Any]],
) -> tuple[HistoryPoint, ...]:
    """Return chronological values while preserving unavailable months as gaps."""

    rows: list[HistoryPoint] = []
    for index, item in enumerate(history):
        period_id = str(item.get("period_id") or "").strip()
        label = str(item.get("label") or period_id or f"Mês {index + 1}")
        availability = str(item.get("availability") or "AVAILABLE").upper()
        overview = item.get("overview")
        overview = overview if isinstance(overview, Mapping) else {}
        value = _number(
            overview.get("vulnerability_occurrences")
            if "vulnerability_occurrences" in overview
            else item.get("vulnerability_occurrences")
        )
        if availability != "AVAILABLE":
            value = None
        rows.append(HistoryPoint(period_id=period_id, label=label, value=value))
    return tuple(
        sorted(
            rows,
            key=lambda row: row.period_id or row.label.casefold(),
        )
    )


def _base_canvas(title: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (1400, 720), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1400, 82), fill=NAVY)
    draw.rectangle((0, 82, 1400, 90), fill=BLUE)
    draw.text(
        (700, 42),
        title,
        font=_font(32, bold=True),
        fill="white",
        anchor="mm",
    )
    return image, draw


def _bar_chart(
    path: Path,
    *,
    title: str,
    rows: Sequence[tuple[str, int, str]],
) -> Path:
    image, draw = _base_canvas(title)
    left, top, right, bottom = 265, 145, 1325, 640
    max_value = max((value for _, value, _ in rows), default=1) or 1
    label_font = _font(22, bold=True)
    value_font = _font(20, bold=True)
    tick_font = _font(17)
    for tick in range(6):
        value = round(max_value * tick / 5)
        x = left + int((right - left) * tick / 5)
        draw.line((x, top, x, bottom), fill=GRID, width=2)
        draw.text(
            (x, bottom + 18),
            _format_number(value),
            font=tick_font,
            fill=MUTED,
            anchor="ma",
        )
    row_height = (bottom - top) / max(len(rows), 1)
    for index, (label, value, color) in enumerate(rows):
        center = top + row_height * (index + 0.5)
        draw.text(
            (left - 18, center),
            label,
            font=label_font,
            fill=TEXT,
            anchor="rm",
        )
        width = int((right - left) * value / max_value)
        if value:
            draw.rounded_rectangle(
                (left, center - 19, left + width, center + 19),
                radius=8,
                fill=color,
            )
        draw.text(
            (min(right - 6, left + width + 12), center),
            _format_number(value),
            font=value_font,
            fill=TEXT,
            anchor="lm" if left + width + 85 < right else "rm",
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


def render_severity_chart(
    severity_counts: Mapping[str, Any],
    path: str | Path,
) -> Path:
    labels = {
        "CRITICAL": "Crítica",
        "HIGH": "Alta",
        "MEDIUM": "Média",
        "LOW": "Baixa",
    }
    rows = [
        (
            labels[key],
            _number(severity_counts.get(key)) or 0,
            SEVERITY_COLORS[key],
        )
        for key in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
    ]
    return _bar_chart(
        Path(path),
        title="Vulnerabilidades por severidade",
        rows=rows,
    )


def render_aging_chart(
    aging: Mapping[str, Any],
    path: str | Path,
) -> Path:
    rows = [
        (label, _number(aging.get(key)) or 0, color)
        for key, label, color in (
            ("0-30", "0 a 30 dias", "#69A7FF"),
            ("31-60", "31 a 60 dias", "#2E59FC"),
            ("61-90", "61 a 90 dias", "#F2B134"),
            ("91-180", "91 a 180 dias", "#F26B00"),
            (">180", "Mais de 180 dias", "#C00000"),
        )
    ]
    return _bar_chart(
        Path(path),
        title="Envelhecimento das vulnerabilidades abertas",
        rows=rows,
    )


def render_monthly_history_chart(
    history: Sequence[Mapping[str, Any]],
    path: str | Path,
) -> Path | None:
    points = normalize_history_series(history)
    if sum(point.value is not None for point in points) < 2:
        return None
    image, draw = _base_canvas("Evolução mensal das ocorrências")
    left, top, right, bottom = 120, 150, 1330, 610
    values = [point.value for point in points]
    max_value = max((value for value in values if value is not None), default=1) or 1
    tick_font = _font(17)
    label_font = _font(18, bold=True)
    for tick in range(6):
        value = round(max_value * tick / 5)
        y = bottom - int((bottom - top) * tick / 5)
        draw.line((left, y, right, y), fill=GRID, width=2)
        draw.text(
            (left - 14, y),
            _format_number(value),
            font=tick_font,
            fill=MUTED,
            anchor="rm",
        )
    coordinates: list[tuple[int, int] | None] = []
    for index, point in enumerate(points):
        x = left + int((right - left) * index / max(len(points) - 1, 1))
        draw.text(
            (x, bottom + 26),
            point.label,
            font=tick_font,
            fill=TEXT,
            anchor="ma",
        )
        if point.value is None:
            coordinates.append(None)
            draw.text(
                (x, bottom - 12),
                "N/D",
                font=label_font,
                fill=MUTED,
                anchor="ms",
            )
            continue
        y = bottom - int((bottom - top) * point.value / max_value)
        coordinates.append((x, y))
    segment: list[tuple[int, int]] = []
    for coordinate in (*coordinates, None):
        if coordinate is None:
            if len(segment) > 1:
                draw.line(segment, fill=BLUE, width=6, joint="curve")
            segment = []
        else:
            segment.append(coordinate)
    for coordinate, point in zip(coordinates, points):
        if coordinate is None or point.value is None:
            continue
        x, y = coordinate
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=BLUE)
        draw.text(
            (x, y - 18),
            _format_number(point.value),
            font=label_font,
            fill=TEXT,
            anchor="ms",
        )
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)
    return output


__all__ = [
    "HistoryPoint",
    "normalize_history_series",
    "render_aging_chart",
    "render_monthly_history_chart",
    "render_severity_chart",
]