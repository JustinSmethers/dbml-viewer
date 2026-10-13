"""Render parsed DBML data into a self-contained interactive HTML file."""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader


COLOR_SCHEME = {
    "source_layer":         {"header": "#506C91", "bg": "#EEF2F8", "border": "#A7B8D0"},
    "seed_layer":           {"header": "#8863A5", "bg": "#F4EFF8", "border": "#C3AED5"},
    "snapshot_layer":       {"header": "#6C5B7B", "bg": "#F0EBF4", "border": "#B8A9C9"},
    "prep_layer":           {"header": "#247E9C", "bg": "#E8F6FA", "border": "#9DD7E8"},
    "intermediate_layer":   {"header": "#A36B18", "bg": "#FEF5E7", "border": "#FAD28C"},
    "bridge_layer":         {"header": "#5268B5", "bg": "#EEF1FA", "border": "#B0BCE5"},
    "mart_layer":           {"header": "#238264", "bg": "#E8F8EF", "border": "#8FE0B0"},
    "external":             {"header": "#95A5A6", "bg": "#F2F3F3", "border": "#C5CACA"},
    "_default":             {"header": "#607D8B", "bg": "#ECEFF1", "border": "#B0BEC5"},
}


def _mix_with_white(hex_color: str, amount: float) -> str:
    """Blend a #RRGGBB (or #RGB) color toward white by `amount` (0-1)."""
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    channels = [int(value[i:i + 2], 16) for i in (0, 2, 4)]
    mixed = [round(c + (255 - c) * amount) for c in channels]
    return "#" + "".join(f"{c:02X}" for c in mixed)


def _scheme_from_color(hex_color: str) -> dict:
    """Derive header/bg/border colors for a group from its DBML [color: ...]."""
    return {
        "header": hex_color,
        "bg": _mix_with_white(hex_color, 0.88),
        "border": _mix_with_white(hex_color, 0.5),
    }


def render_html(data: dict, layout: str = "LR") -> str:
    """Render the parsed DBML data model into a self-contained HTML string.

    Args:
        data: Normalized DBML dict from dbml_viewer.parser.parse_dbml().
        layout: Dagre rankdir — "TB" (top-to-bottom) or "LR" (left-to-right).

    Returns:
        Complete HTML string ready to write to a file.
    """
    template_dir = Path(__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    template = env.get_template("diagram.html.j2")

    # TableGroup [color: ...] in the DBML overrides the built-in scheme.
    colors = dict(COLOR_SCHEME)
    for group_name, hex_color in data.get("group_colors", {}).items():
        colors[group_name] = _scheme_from_color(hex_color)

    return template.render(
        data=data,
        colors=colors,
        layout=layout,
    )
