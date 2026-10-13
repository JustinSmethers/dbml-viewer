"""CLI entry point for the DBML interactive diagram viewer."""

import argparse
import sys
import webbrowser
from pathlib import Path

from dbml_viewer.parser import parse_dbml
from dbml_viewer.renderer import render_html


def main():
    ap = argparse.ArgumentParser(
        description="Generate an interactive HTML diagram from a DBML file."
    )
    ap.add_argument("input", type=Path, help="Path to the .dbml file")
    ap.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output HTML path (default: <input_stem>_diagram.html)",
    )
    ap.add_argument(
        "--layout", choices=["TB", "LR"], default="LR",
        help="Layout direction: LR (left-to-right) or TB (top-to-bottom). Default: LR",
    )
    ap.add_argument(
        "--no-open", action="store_true",
        help="Don't automatically open the diagram in the browser",
    )
    args = ap.parse_args()

    if not args.input.exists():
        print(f"Error: file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    output = args.output or args.input.with_name(args.input.stem + "_diagram.html")

    print(f"Parsing {args.input}...", file=sys.stderr)
    data = parse_dbml(args.input)
    print(
        f"  {len(data['tables'])} tables, {len(data['refs'])} refs, "
        f"{len(data['groups'])} groups, {len(data['enums'])} enums",
        file=sys.stderr,
    )

    print(f"Rendering diagram ({args.layout} layout)...", file=sys.stderr)
    html = render_html(data, layout=args.layout)

    output.write_text(html, encoding="utf-8")
    print(f"Written to {output}", file=sys.stderr)

    if not args.no_open:
        url = output.resolve().as_uri()
        print(f"Opening {url}", file=sys.stderr)
        webbrowser.open(url)
