"""The README's detectability chart, as a light and a dark SVG (main venv).

    uv run python benchmarks/detectability/chart.py

The numbers are the README's table: keep the two in step. Each row's bars run from the
highest to the lowest, and each tool keeps its colour wherever its bar falls. Writes
``assets/charts/detectability-{light,dark}.svg``, which the README picks between with
``<picture>`` by the reader's theme.
"""

from typing import NamedTuple

from common import ROOT

CHARTS = ROOT.parents[1] / "assets" / "charts"
TITLE = "How often a trained detector is wrong"
SUBTITLE = "Higher is harder to detect, and 0.5 is a coin toss. Each tool carries the same payload."
NAMES = {"cipherstitch": "CipherStitch", "hstego": "HStego", "other": "OpenStego or steghide"}


class Row(NamedTuple):
    label: str
    cipherstitch: float
    other: float
    hstego: float

    def bars(self) -> list[tuple[str, float]]:
        return [("cipherstitch", self.cipherstitch), ("hstego", self.hstego), ("other", self.other)]


GROUPS = (
    (
        "Image LSB on a PNG, against OpenStego and HStego",
        (
            Row("5% of the capacity", 0.474, 0.273, 0.465),
            Row("20% of the capacity", 0.369, 0.135, 0.338),
        ),
    ),
    (
        "DCT on a JPEG at quality 75, against steghide and HStego",
        (
            Row("5% of the capacity", 0.462, 0.109, 0.367),
            Row("20% of the capacity", 0.394, 0.009, 0.341),
        ),
    ),
)

# CipherStitch takes the app's accent (gui/resources/cipherstitch.qss: #9184d9 on the dark page,
# a darker step of the same violet on the light one), HStego a warm coral beside it, and the
# popular tools a recessive cool grey like the app's neutrals, against GitHub's light
# (#ffffff) and dark (#0d1117) page. validate_palette.js passes the two hues all-pairs on
# both, and the grey against each of them; every bar is labelled, so the grey's low contrast
# needs no more relief.
THEMES = {
    "light": {
        "page": "#ffffff",
        "primary": "#0b0b0b",
        "secondary": "#4f525c",
        "muted": "#8a8b93",
        "grid": "#e4e5ea",
        "baseline": "#c4c6ce",
        "cipherstitch": "#7466c9",
        "cipherstitch_ink": "#ffffff",
        "hstego": "#df6a3a",
        "other": "#a9acb8",
    },
    "dark": {
        "page": "#0d1117",
        "primary": "#e9e9ed",
        "secondary": "#aaaab1",
        "muted": "#8a8b93",
        "grid": "#262833",
        "baseline": "#3f424d",
        "cipherstitch": "#9184d9",
        "cipherstitch_ink": "#0d1117",
        "hstego": "#d9693a",
        "other": "#4b4f5d",
    },
}

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"
WIDTH = 720
PLOT_LEFT = 168
PLOT_RIGHT = 672
MAX_VALUE = 0.5
TICKS = 5
BAR = 18
BARS = 3  # per row: CipherStitch, HStego and the popular tool
PAIR_GAP = 2
ROW_GAP = 14
GROUP_GAP = 34
RADIUS = 4


def x_of(value: float) -> float:
    return PLOT_LEFT + value / MAX_VALUE * (PLOT_RIGHT - PLOT_LEFT)


def bar(y: float, value: float, fill: str) -> str:
    """A bar from the baseline, rounded at its data end only."""
    straight = x_of(value) - PLOT_LEFT - RADIUS

    return (
        f'<path d="M{PLOT_LEFT},{y} h{straight:.1f} a{RADIUS},{RADIUS} 0 0 1 {RADIUS},{RADIUS} '
        f'v{BAR - 2 * RADIUS} a{RADIUS},{RADIUS} 0 0 1 -{RADIUS},{RADIUS} h-{straight:.1f} z" '
        f'fill="{fill}"/>'
    )


def text(
    x: float, y: float, content: str, size: int, fill: str, anchor: str = "start", weight: int = 400
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
        f'fill="{fill}" text-anchor="{anchor}">{content}</text>'
    )


def line(x: float, top: float, bottom: float, stroke: str) -> str:
    return f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" stroke="{stroke}"/>'


def group(
    colours: dict[str, str], title: str, rows: tuple[Row, ...], y: float
) -> tuple[list[str], float]:
    """A group's title, grid and bars from ``y``, and the bottom of its plot."""
    parts = [text(0, y, title, 13, colours["primary"], weight=600)]
    top = y + 12
    row_height = BARS * BAR + (BARS - 1) * PAIR_GAP
    bottom = top + len(rows) * (row_height + ROW_GAP) - ROW_GAP + 6

    # The last grid line is the coin toss, drawn like the baseline.
    for tick in range(1, TICKS + 1):
        stroke = colours["grid"] if tick < TICKS else colours["baseline"]
        parts.append(line(x_of(tick / 10), top - 6, bottom, stroke))

    parts.append(line(PLOT_LEFT, top - 6, bottom, colours["baseline"]))
    y = top

    for row in rows:
        middle = y + BAR + PAIR_GAP
        parts.append(text(PLOT_LEFT - 12, middle + 13, row.label, 12, colours["secondary"], "end"))

        for tool, value in sorted(row.bars(), key=lambda item: -item[1]):
            parts += [bar(y, value, colours[tool]), value_label(colours, tool, value, y)]
            y += BAR + PAIR_GAP

        y += ROW_GAP - PAIR_GAP

    return parts, bottom


def value_label(colours: dict[str, str], tool: str, value: float, y: float) -> str:
    """CipherStitch's value inside its bar, in bold; the others' after theirs."""
    if tool == "cipherstitch":
        return text(
            x_of(value) - 8, y + 13, f"{value:.3f}", 12, colours["cipherstitch_ink"], "end", 600
        )

    label = text(x_of(value) + 6, y + 13, f"{value:.3f}", 12, colours["secondary"])

    # A halo of the page's colour keeps the grid lines out of the digits.
    return label.replace(
        "<text ", f'<text stroke="{colours["page"]}" stroke-width="4" paint-order="stroke" ', 1
    )


def chart(theme: str) -> str:
    colours = THEMES[theme]
    parts = [
        text(0, 20, TITLE, 16, colours["primary"], weight=600),
        text(0, 42, SUBTITLE, 13, colours["secondary"]),
    ]

    for x, tool in ((0, "cipherstitch"), (100, "hstego"), (176, "other")):
        parts += [
            f'<rect x="{x}" y="62" width="10" height="10" rx="2" fill="{colours[tool]}"/>',
            text(x + 16, 71, NAMES[tool], 12, colours["secondary"]),
        ]

    y = 110.0

    for title, rows in GROUPS:
        group_parts, bottom = group(colours, title, rows, y)
        parts += group_parts
        y = bottom + GROUP_GAP

    axis_y = y - GROUP_GAP + 18

    for tick in range(TICKS + 1):
        label = "0" if tick == 0 else f"0.{tick}"
        parts.append(text(x_of(tick / 10), axis_y, label, 12, colours["muted"], anchor="middle"))

    parts.append(text(x_of(MAX_VALUE), axis_y + 16, "coin toss", 12, colours["muted"], "middle"))
    height = int(axis_y + 24)
    description = " ".join(
        f"{title}: CipherStitch {rows[0].cipherstitch:.3f} and {rows[1].cipherstitch:.3f}, "
        f"HStego {rows[0].hstego:.3f} and {rows[1].hstego:.3f}, the popular tool "
        f"{rows[0].other:.3f} and {rows[1].other:.3f}, at 5% and 20% of the capacity."
        for title, rows in GROUPS
    )
    body = "\n  ".join(parts)

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
        f'viewBox="0 0 {WIDTH} {height}" role="img" font-family="{FONT}">\n'
        f"  <title>{TITLE}</title>\n"
        f"  <desc>{description}</desc>\n"
        f"  {body}\n</svg>\n"
    )


def main() -> None:
    CHARTS.mkdir(parents=True, exist_ok=True)

    for theme in THEMES:
        (CHARTS / f"detectability-{theme}.svg").write_text(chart(theme))


if __name__ == "__main__":
    main()
