"""
Turn a sectioned report (headlines, explanations, charts) into ONE self-contained HTML file.

WHAT THIS FILE DOES
-------------------
Some skills (the `eda-report` skill first) build a report as plain data:

    {"title": "...", "subtitle": "...",
     "sections": [{"id", "title", "explanation", "bullets", "charts", "table"}, ...]}

The web app draws that data with React components. For the DOWNLOAD, this file
renders the same report as a single .html file a client can open offline or
email: inline CSS, charts drawn as inline SVG, no scripts and no external files.

WHY SVG BUILT BY HAND
---------------------
No chart library is needed: each chart spec from `_core.py` (bar, line,
histogram, box, scatter, heatmap) maps to a handful of SVG shapes. Every color
is a CSS variable defined once at the top of the page, with a second set for
dark mode (`prefers-color-scheme: dark`), so the file follows the reader's
computer setting just like the web app does.

Every piece of text from the data (column names, values) goes through
`html.escape`, so a cell containing "<script>" is shown as text, never run.
"""

from __future__ import annotations

import html
import math
from typing import Any

from data_skills._core import fmt_num

# Drawing size of every chart. The SVG scales to the card width (width: 100%).
W, H = 480, 260
PAD = {"l": 56, "r": 14, "t": 12, "b": 40}

_CSS = """
:root {
  --canvas: #f0f2f5; --surface: #ffffff; --surface-2: #f6f7f9; --line: #d9dde3;
  --ink: #111827; --ink-muted: #5f6675; --brand: #a07c1e;
  --chart: #a07c1e; --chart-2: #4f6fb8; --div-neg: #2f6fd0; --div-pos: #c0462e;
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root {
    --canvas: #0e1117; --surface: #1a1d26; --surface-2: #222634; --line: #2e3244;
    --ink: #e8e8e8; --ink-muted: #9aa0ae; --brand: #c8ac44;
    --chart: #a68a2c; --chart-2: #6f8fd8; --div-neg: #6f9ff0; --div-pos: #f08a6f;
    color-scheme: dark;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--canvas); color: var(--ink);
  font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 24px 16px 48px; }
h1, h2, h3 { font-family: "Fira Code", ui-monospace, monospace; letter-spacing: -0.02em; }
h1 { font-size: 1.6rem; margin: 0 0 4px; }
.sub { color: var(--ink-muted); margin: 0 0 24px; }
section { background: var(--surface); border: 1px solid var(--line); border-radius: 16px;
  padding: 20px; margin: 0 0 16px; }
section h2 { font-size: 1.15rem; margin: 0 0 8px; color: var(--brand); }
section p { margin: 0 0 10px; }
ul { margin: 0 0 12px; padding-left: 20px; }
.grid { display: grid; gap: 16px; grid-template-columns: 1fr; }
@media (min-width: 760px) { .grid.multi { grid-template-columns: 1fr 1fr; } }
figure { margin: 0; border: 1px solid var(--line); border-radius: 12px; padding: 12px; }
figcaption { font-weight: 600; font-size: 0.9rem; margin-bottom: 6px; }
svg { width: 100%; height: auto; display: block; }
svg text { fill: var(--ink-muted); font-size: 11px; }
.na { color: var(--ink-muted); font-style: italic; }
.tbl { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
th, td { padding: 6px 10px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; }
th { background: var(--surface-2); color: var(--ink-muted); font-family: "Fira Code", ui-monospace, monospace; }
th:first-child, td:first-child { text-align: left; }
footer { color: var(--ink-muted); font-size: 0.8rem; text-align: center; }
"""


def _e(text: Any) -> str:
    """
    Escape any value for safe use inside HTML or SVG text and attributes.

    Args:
        text: any value (it is converted with str()).

    Returns:
        str: the text with &, <, >, " and ' escaped.

    Example:
        >>> _e('<b>"Midtown"</b>')
        '&lt;b&gt;&quot;Midtown&quot;&lt;/b&gt;'
    """
    return html.escape(str(text), quote=True)


def _short(x: float | None) -> str:
    """
    Format an axis number compactly: 1,234 -> "1.2K", 2,500,000 -> "2.5M", 0.25 -> "0.25".

    Args:
        x: the number (None -> "").

    Returns:
        str: a short label for an axis tick.

    Example:
        >>> _short(1234), _short(2_500_000), _short(0.25)
        ('1.2K', '2.5M', '0.25')
    """
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return ""
    a = abs(x)
    if a >= 1e9:
        return f"{x / 1e9:.1f}B"
    if a >= 1e6:
        return f"{x / 1e6:.1f}M"
    if a >= 1e4:
        return f"{x / 1e3:.0f}K"
    if a >= 1e3:
        return f"{x / 1e3:.1f}K"
    return fmt_num(round(x, 2))


def _scale(lo: float, hi: float, a: float, b: float):
    """
    Make a linear scale function that maps data values [lo, hi] onto pixels [a, b].

    A zero-width range (lo == hi) maps every value to the middle, so constant
    columns don't divide by zero.

    Args:
        lo: smallest data value.
        hi: largest data value.
        a: pixel position for `lo`.
        b: pixel position for `hi`.

    Returns:
        Callable[[float], float]: value -> pixel.

    Example:
        >>> _scale(0, 10, 0, 100)(5)
        50.0
    """
    if hi == lo:
        return lambda v: (a + b) / 2
    return lambda v: a + (v - lo) / (hi - lo) * (b - a)


def _svg_open(title: str) -> str:
    """
    Start an SVG element with an accessible title.

    Args:
        title: what the chart shows (read by screen readers).

    Returns:
        str: the opening <svg> tag plus its <title>.

    Example:
        >>> _svg_open("Sales").startswith('<svg viewBox="0 0 480 260"')
        True
    """
    return f'<svg viewBox="0 0 {W} {H}" role="img"><title>{_e(title)}</title>'


def _y_axis(lo: float, hi: float, ys) -> str:
    """
    Draw five horizontal grid lines with value labels on the left.

    Args:
        lo: smallest value on the axis.
        hi: largest value on the axis.
        ys: the value -> pixel scale for the vertical axis.

    Returns:
        str: SVG lines and labels.

    Example:
        >>> "<line" in _y_axis(0, 10, _scale(0, 10, 200, 10))
        True
    """
    out = []
    for g in range(5):
        v = lo + (hi - lo) * g / 4
        y = ys(v)
        out.append(f'<line x1="{PAD["l"]}" x2="{W - PAD["r"]}" y1="{y:.1f}" y2="{y:.1f}" '
                   f'style="stroke: var(--line)" stroke-width="1"/>'
                   f'<text x="{PAD["l"] - 6}" y="{y + 4:.1f}" text-anchor="end">{_e(_short(v))}</text>')
    return "".join(out)


def _axis_labels(x_label: str, y_label: str) -> str:
    """
    Write the x-axis label under the chart and the y-axis label rotated on the left.

    Args:
        x_label: label for the horizontal axis ("" to skip).
        y_label: label for the vertical axis ("" to skip).

    Returns:
        str: SVG text elements.

    Example:
        >>> "amount" in _axis_labels("amount", "rows")
        True
    """
    out = ""
    if x_label:
        out += f'<text x="{(PAD["l"] + W - PAD["r"]) / 2}" y="{H - 4}" text-anchor="middle">{_e(x_label)}</text>'
    if y_label:
        out += (f'<text transform="translate(12 {(PAD["t"] + H - PAD["b"]) / 2}) rotate(-90)" '
                f'text-anchor="middle">{_e(y_label)}</text>')
    return out


def svg_bar(spec: dict) -> str:
    """
    Draw a horizontal bar chart spec (from `bar_chart`) as SVG.

    Args:
        spec: {"categories", "values", "value_label", "title"}.

    Returns:
        str: an <svg> element; each bar has a <title> tooltip with its exact value.

    Example:
        >>> "<rect" in svg_bar({"title": "t", "categories": ["a"], "values": [3], "value_label": ""})
        True
    """
    cats, vals = spec["categories"], [v or 0 for v in spec["values"]]
    n = max(len(cats), 1)
    row_h = 22
    h = max(n * row_h + 16, 60)
    vmax = max([abs(v) for v in vals] + [1e-9])
    left, right = 130, W - 70
    out = [f'<svg viewBox="0 0 {W} {h}" role="img"><title>{_e(spec["title"])}</title>']
    for i, (c, v) in enumerate(zip(cats, vals)):
        y = 8 + i * row_h
        bw = abs(v) / vmax * (right - left)
        label = str(c) if len(str(c)) <= 20 else str(c)[:19] + "…"
        out.append(f'<text x="{left - 8}" y="{y + 13}" text-anchor="end">{_e(label)}</text>'
                   f'<rect x="{left}" y="{y + 4}" width="{bw:.1f}" height="10" rx="2" style="fill: var(--chart)">'
                   f'<title>{_e(c)}: {_e(fmt_num(v))} {_e(spec.get("value_label", ""))}</title></rect>'
                   f'<text x="{left + bw + 6:.1f}" y="{y + 13}">{_e(fmt_num(v))}</text>')
    out.append("</svg>")
    return "".join(out)


def svg_line(spec: dict) -> str:
    """
    Draw a line chart spec (from `line_chart`) as SVG; the first series is drawn on top.

    Args:
        spec: {"x", "series": [{"name", "values"}], "title", "x_label", "y_label"}.

    Returns:
        str: an <svg> element.

    Example:
        >>> "<polyline" in svg_line({"title": "t", "x": ["a", "b"], "series": [{"name": "s", "values": [1, 2]}]})
        True
    """
    xs_vals = spec["x"]
    allv = [v for s in spec["series"] for v in s["values"] if v is not None]
    if not xs_vals or not allv:
        return '<p class="na">No data to chart.</p>'
    lo, hi = min(allv + [0]), max(allv + [0])
    xs = _scale(0, max(len(xs_vals) - 1, 1), PAD["l"], W - PAD["r"])
    ys = _scale(lo, hi, H - PAD["b"], PAD["t"])
    out = [_svg_open(spec["title"]), _y_axis(lo, hi, ys)]
    colors = ["var(--chart)", "var(--chart-2)"]
    for j, s in reversed(list(enumerate(spec["series"]))):
        pts = " ".join(f"{xs(i):.1f},{ys(v):.1f}" for i, v in enumerate(s["values"]) if v is not None)
        out.append(f'<polyline points="{pts}" fill="none" style="stroke: {colors[min(j, 1)]}" stroke-width="2"/>')
    out.append(f'<text x="{PAD["l"]}" y="{H - PAD["b"] + 16}">{_e(xs_vals[0])}</text>'
               f'<text x="{W - PAD["r"]}" y="{H - PAD["b"] + 16}" text-anchor="end">{_e(xs_vals[-1])}</text>')
    out.append(_axis_labels("", spec.get("y_label", "")) + "</svg>")
    return "".join(out)


def svg_histogram(spec: dict) -> str:
    """
    Draw a histogram spec (from `histogram_chart`) as SVG: touching bars, one per bin.

    Args:
        spec: {"bin_edges", "counts", "title", "x_label", "y_label"}.

    Returns:
        str: an <svg> element; each bar's <title> gives its range and row count.

    Example:
        >>> svg_histogram({"title": "t", "bin_edges": [0, 1, 2], "counts": [3, 1]}).count("<rect")
        2
    """
    edges, counts = spec["bin_edges"], spec["counts"]
    if not counts:
        return '<p class="na">No data to chart.</p>'
    cmax = max(counts + [1])
    xs = _scale(edges[0], edges[-1], PAD["l"], W - PAD["r"])
    ys = _scale(0, cmax, H - PAD["b"], PAD["t"])
    out = [_svg_open(spec["title"]), _y_axis(0, cmax, ys)]
    for i, c in enumerate(counts):
        x0, x1 = xs(edges[i]), xs(edges[i + 1])
        out.append(f'<rect x="{x0 + 0.5:.1f}" y="{ys(c):.1f}" width="{max(x1 - x0 - 1, 1):.1f}" '
                   f'height="{ys(0) - ys(c):.1f}" style="fill: var(--chart)">'
                   f'<title>{_e(fmt_num(edges[i]))} to {_e(fmt_num(edges[i + 1]))}: {c:,} rows</title></rect>')
    for v in (edges[0], (edges[0] + edges[-1]) / 2, edges[-1]):
        out.append(f'<text x="{xs(v):.1f}" y="{H - PAD["b"] + 16}" text-anchor="middle">{_e(_short(v))}</text>')
    out.append(_axis_labels(spec.get("x_label", ""), spec.get("y_label", "")) + "</svg>")
    return "".join(out)


def svg_box(spec: dict) -> str:
    """
    Draw a box plot spec (from `box_chart`) as SVG: vertical boxes side by side.

    Whiskers end at the last values inside the 1.5 × IQR fences; dots beyond them
    are outliers.

    Args:
        spec: {"boxes": [box_stats dicts], "title", "value_label", "group_label"}.

    Returns:
        str: an <svg> element.

    Example:
        >>> from data_skills._core import box_stats
        >>> "<rect" in svg_box({"title": "t", "boxes": [box_stats("a", [1, 2, 3, 40])]})
        True
    """
    boxes = spec["boxes"]
    if not boxes:
        return '<p class="na">No data to chart.</p>'
    vals = [v for b in boxes for v in [b["whisker_low"], b["whisker_high"], *b["outliers"]] if v is not None]
    lo, hi = min(vals), max(vals)
    ys = _scale(lo, hi, H - PAD["b"], PAD["t"])
    slot = (W - PAD["l"] - PAD["r"]) / len(boxes)
    bw = min(slot * 0.5, 48)
    out = [_svg_open(spec["title"]), _y_axis(lo, hi, ys)]
    for i, b in enumerate(boxes):
        cx = PAD["l"] + slot * (i + 0.5)
        tip = (f'{b["name"]}: median {fmt_num(b["median"])}, middle half {fmt_num(b["q1"])} to '
               f'{fmt_num(b["q3"])}, {b["outlier_count"]:,} outliers')
        out.append(
            f'<g><title>{_e(tip)}</title>'
            f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{ys(b["whisker_low"]):.1f}" y2="{ys(b["whisker_high"]):.1f}" '
            f'style="stroke: var(--ink-muted)" stroke-width="1"/>'
            f'<rect x="{cx - bw / 2:.1f}" y="{ys(b["q3"]):.1f}" width="{bw:.1f}" '
            f'height="{max(ys(b["q1"]) - ys(b["q3"]), 1):.1f}" rx="3" '
            f'style="fill: color-mix(in srgb, var(--chart) 30%, var(--surface)); stroke: var(--chart)" stroke-width="1.5"/>'
            f'<line x1="{cx - bw / 2:.1f}" x2="{cx + bw / 2:.1f}" y1="{ys(b["median"]):.1f}" y2="{ys(b["median"]):.1f}" '
            f'style="stroke: var(--ink)" stroke-width="2"/></g>')
        for v in b["outliers"]:
            out.append(f'<circle cx="{cx:.1f}" cy="{ys(v):.1f}" r="2.5" fill="none" '
                       f'style="stroke: var(--chart-2)" stroke-width="1"><title>{_e(fmt_num(v))}</title></circle>')
        label = b["name"] if len(b["name"]) <= 12 else b["name"][:11] + "…"
        out.append(f'<text x="{cx:.1f}" y="{H - PAD["b"] + 16}" text-anchor="middle">{_e(label)}</text>')
    out.append(_axis_labels(spec.get("group_label", ""), spec.get("value_label", "")) + "</svg>")
    return "".join(out)


def svg_scatter(spec: dict) -> str:
    """
    Draw a scatter plot spec (from `scatter_chart`) as SVG: small see-through dots.

    Args:
        spec: {"points": [{"x", "y"}], "title", "x_label", "y_label"}.

    Returns:
        str: an <svg> element.

    Example:
        >>> svg_scatter({"title": "t", "points": [{"x": 1, "y": 2}, {"x": 2, "y": 3}]}).count("<circle")
        2
    """
    pts = spec["points"]
    if not pts:
        return '<p class="na">No data to chart.</p>'
    xlo, xhi = min(p["x"] for p in pts), max(p["x"] for p in pts)
    ylo, yhi = min(p["y"] for p in pts), max(p["y"] for p in pts)
    xs = _scale(xlo, xhi, PAD["l"] + 4, W - PAD["r"] - 4)
    ys = _scale(ylo, yhi, H - PAD["b"] - 4, PAD["t"] + 4)
    out = [_svg_open(spec["title"]), _y_axis(ylo, yhi, ys)]
    for p in pts:
        out.append(f'<circle cx="{xs(p["x"]):.1f}" cy="{ys(p["y"]):.1f}" r="3" '
                   f'style="fill: var(--chart)" fill-opacity="0.55">'
                   f'<title>{_e(fmt_num(p["x"]))}, {_e(fmt_num(p["y"]))}</title></circle>')
    for v in (xlo, (xlo + xhi) / 2, xhi):
        out.append(f'<text x="{xs(v):.1f}" y="{H - PAD["b"] + 16}" text-anchor="middle">{_e(_short(v))}</text>')
    out.append(_axis_labels(spec.get("x_label", ""), spec.get("y_label", "")) + "</svg>")
    return "".join(out)


def heat_color(v: float | None) -> str:
    """
    The CSS fill for one heatmap cell on a diverging scale.

    Negative values blend `--div-neg` into the card color, positive values blend
    `--div-pos`; 0 is the card color itself, so only strong values stand out.

    Args:
        v: a value in [-1, 1], or None for an empty cell.

    Returns:
        str: a CSS color expression using color-mix().

    Example:
        >>> heat_color(0.5)
        'color-mix(in srgb, var(--div-pos) 50%, var(--surface))'
    """
    if v is None:
        return "var(--surface-2)"
    pct = round(min(abs(v), 1) * 100)
    return f"color-mix(in srgb, var({'--div-pos' if v >= 0 else '--div-neg'}) {pct}%, var(--surface))"


def svg_heatmap(spec: dict) -> str:
    """
    Draw a heatmap spec (from `heatmap_chart`) as SVG, with each value written in its cell.

    Args:
        spec: {"rows", "columns", "values", "title"}.

    Returns:
        str: an <svg> element sized to the number of rows and columns.

    Example:
        >>> svg_heatmap({"title": "t", "rows": ["a"], "columns": ["a"], "values": [[1]]}).count("<rect")
        1
    """
    rows, cols, vals = spec["rows"], spec["columns"], spec["values"]
    if not rows or not cols:
        return '<p class="na">No data to chart.</p>'
    left, top = 110, 70
    cell = min((W - left - 8) / len(cols), 44)
    h = top + cell * len(rows) + 8
    w = left + cell * len(cols) + 8
    out = [f'<svg viewBox="0 0 {w:.0f} {h:.0f}" role="img"><title>{_e(spec["title"])}</title>']
    for j, c in enumerate(cols):
        x = left + cell * (j + 0.5)
        label = c if len(c) <= 14 else c[:13] + "…"
        out.append(f'<text transform="translate({x:.1f} {top - 6}) rotate(-45)">{_e(label)}</text>')
    for i, r in enumerate(rows):
        y = top + cell * i
        label = r if len(r) <= 16 else r[:15] + "…"
        out.append(f'<text x="{left - 6}" y="{y + cell / 2 + 4:.1f}" text-anchor="end">{_e(label)}</text>')
        for j, c in enumerate(cols):
            v = vals[i][j]
            out.append(f'<rect x="{left + cell * j:.1f}" y="{y:.1f}" width="{cell - 2:.1f}" height="{cell - 2:.1f}" '
                       f'rx="3" style="fill: {heat_color(v)}"><title>{_e(r)} × {_e(c)}: '
                       f'{"n/a" if v is None else f"{v:+.2f}"}</title></rect>')
            if v is not None and cell >= 28:
                out.append(f'<text x="{left + cell * j + cell / 2 - 1:.1f}" y="{y + cell / 2 + 3:.1f}" '
                           f'text-anchor="middle" style="fill: var(--ink)" font-size="10">{v:.2f}</text>')
    out.append("</svg>")
    return "".join(out)


_DRAW = {"bar": svg_bar, "line": svg_line, "histogram": svg_histogram, "box": svg_box,
         "scatter": svg_scatter, "heatmap": svg_heatmap}


def chart_svg(spec: dict) -> str:
    """
    Draw any chart spec from `_core.py` as SVG, wrapped in a captioned <figure>.

    Args:
        spec: a chart spec with a "type" of bar, line, histogram, box, scatter or heatmap.

    Returns:
        str: a <figure> holding the chart (or a short note for an unknown type).

    Example:
        >>> chart_svg({"type": "bar", "title": "t", "categories": ["a"], "values": [1]}).startswith("<figure>")
        True
    """
    draw = _DRAW.get(spec.get("type", ""))
    body = draw(spec) if draw else '<p class="na">This chart type cannot be drawn here.</p>'
    return f'<figure><figcaption>{_e(spec.get("title", ""))}</figcaption>{body}</figure>'


def table_html(rows: list[dict], limit: int = 50) -> str:
    """
    Render a list of row dicts as an HTML table (numbers in US format).

    Args:
        rows: the table, one dict per row (as in `SkillResult.tables`).
        limit: most rows to show (default 50).

    Returns:
        str: a scrollable <table>, or "" when there are no rows.

    Example:
        >>> "<td>1,234</td>" in table_html([{"col": "a", "n": 1234}])
        True
    """
    if not rows:
        return ""
    cols = list(rows[0].keys())
    show = lambda v: "—" if v is None else fmt_num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)  # noqa: E731
    head = "".join(f"<th>{_e(c)}</th>" for c in cols)
    body = "".join("<tr>" + "".join(f"<td>{_e(show(r.get(c)))}</td>" for c in cols) + "</tr>" for r in rows[:limit])
    return f'<div class="tbl"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def render_html_report(report: dict, tables: dict[str, list[dict]] | None = None) -> str:
    """
    Render a sectioned report as one self-contained HTML page (no scripts, no external files).

    Each section becomes a card: its headline (<h2>), the explanation paragraph,
    the bullet points, its charts (two per row on wide screens when there are
    several) and its table, if it names one from `tables`.

    Args:
        report: {"title", "subtitle", "sections": [{"id", "title", "explanation",
            "bullets", "charts", "table"}]}.
        tables: the skill's named tables, so a section's "table" name can be shown.

    Returns:
        str: the full HTML document.

    Example:
        >>> page = render_html_report({"title": "EDA", "sections": [
        ...     {"id": "x", "title": "Data Overview", "explanation": "Hi", "bullets": [], "charts": []}]})
        >>> "<h2>Data Overview</h2>" in page
        True
    """
    tables = tables or {}
    parts = []
    for s in report.get("sections", []):
        charts = s.get("charts") or []
        grid_cls = "grid multi" if len(charts) > 1 else "grid"
        bullets = "".join(f"<li>{_e(b)}</li>" for b in s.get("bullets") or [])
        parts.append(
            f'<section id="{_e(s.get("id", ""))}"><h2>{_e(s["title"])}</h2>'
            f'<p>{_e(s.get("explanation", ""))}</p>'
            + (f"<ul>{bullets}</ul>" if bullets else "")
            + (f'<div class="{grid_cls}">' + "".join(chart_svg(c) for c in charts) + "</div>" if charts else "")
            + (table_html(tables.get(s["table"], [])) if s.get("table") else "")
            + "</section>")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        f"<title>{_e(report.get('title', 'Report'))}</title><style>{_CSS}</style></head><body><main>"
        f"<h1>{_e(report.get('title', 'Report'))}</h1><p class=\"sub\">{_e(report.get('subtitle', ''))}</p>"
        + "".join(parts)
        + "<footer>Generated by the Data Consulting Platform. Charts are drawn from your data; "
          "hover over a mark to see its exact value.</footer></main></body></html>"
    )
