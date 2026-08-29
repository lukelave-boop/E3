from __future__ import annotations

import html
from pathlib import Path


def checkerboard_svg(columns: int, rows: int, square_size_mm: float, margin_mm: float = 10.0) -> str:
    """Create a checkerboard SVG from its inner-corner dimensions."""
    squares_x = columns + 1
    squares_y = rows + 1
    width = squares_x * square_size_mm + 2 * margin_mm
    height = squares_y * square_size_mm + 2 * margin_mm
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}mm" height="{height}mm" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
    ]
    for row in range(squares_y):
        for column in range(squares_x):
            if (row + column) % 2 == 0:
                x = margin_mm + column * square_size_mm
                y = margin_mm + row * square_size_mm
                parts.append(
                    f'<rect x="{x}" y="{y}" width="{square_size_mm}" height="{square_size_mm}" fill="black"/>'
                )
    parts.append(
        f'<text x="{margin_mm}" y="{height - 2.5}" font-family="sans-serif" font-size="3">'
        f'{columns}×{rows} inner corners; {square_size_mm:g} mm squares — print at 100%</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def bed_point_target_svg(
    width_mm: float,
    height_mm: float,
    points: list[tuple[float, float, str]],
    cross_size_mm: float = 5.0,
) -> str:
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width_mm}mm" height="{height_mm}mm" viewBox="0 0 {width_mm} {height_mm}">',
        '<rect width="100%" height="100%" fill="white" stroke="black" stroke-width="0.25"/>',
        '<g fill="none" stroke="black" stroke-width="0.35">',
    ]
    for x, y_machine, label in points:
        y = height_mm - y_machine
        half = cross_size_mm / 2
        parts.append(f'<line x1="{x-half}" y1="{y}" x2="{x+half}" y2="{y}"/>')
        parts.append(f'<line x1="{x}" y1="{y-half}" x2="{x}" y2="{y+half}"/>')
        parts.append(f'<circle cx="{x}" cy="{y}" r="1.25"/>')
        parts.append(
            f'<text x="{x + half + 1}" y="{y - 1}" fill="black" stroke="none" font-family="sans-serif" font-size="3">{html.escape(label)}</text>'
        )
    parts.extend(["</g>", "</svg>"])
    return "\n".join(parts)


def write_default_targets(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    checkerboard = directory / "checkerboard_9x6_20mm.svg"
    checkerboard.write_text(checkerboard_svg(9, 6, 20.0), encoding="utf-8")

    points = [
        (10.0, 10.0, "1: 10,10"),
        (210.0, 10.0, "2: 210,10"),
        (210.0, 210.0, "3: 210,210"),
        (10.0, 210.0, "4: 10,210"),
        (110.0, 110.0, "5: 110,110"),
        (50.0, 110.0, "6: 50,110"),
        (170.0, 110.0, "7: 170,110"),
        (110.0, 50.0, "8: 110,50"),
        (110.0, 170.0, "9: 110,170"),
    ]
    bed = directory / "bed_points_220x220.svg"
    bed.write_text(bed_point_target_svg(220.0, 220.0, points), encoding="utf-8")
    return [checkerboard, bed]
