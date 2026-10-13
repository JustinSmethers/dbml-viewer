"""CLI entry point for the DBML interactive diagram viewer."""

import argparse
import sys
import webbrowser
from pathlib import Path

from dbml_viewer.parser import parse_dbml
from dbml_viewer.renderer import render_html
from dbml_viewer.compare import compare_branch
from dbml_viewer.source_compare import compare_sources, find_repository


def main():
    ap = argparse.ArgumentParser(
        description="Generate an interactive HTML diagram from a DBML file."
    )
    ap.add_argument("input", type=Path, help="Path to the .dbml file")
    ap.add_argument(
        "--baseline-dbml", type=Path,
        help="Earlier DBML design to compare with when the input has no Git baseline",
    )
    ap.add_argument(
        "-o", "--output", type=Path, default=None,
        help="Output HTML path (default: <input_stem>_diagram.html)",
    )
    ap.add_argument(
        "--layout", choices=["TB", "LR"], default="LR",
        help="Layout direction: LR (left-to-right) or TB (top-to-bottom). Default: LR",
    )
    comparison = ap.add_mutually_exclusive_group()
    comparison.add_argument(
        "--compare", metavar="REF", default=None,
        help="Highlight model and design changes against REF (default: freshly fetched origin/main; includes local edits)",
    )
    comparison.add_argument(
        "--compare-dbml", metavar="REF",
        help="Compare the DBML schema itself against REF instead of model source files",
    )
    comparison.add_argument(
        "--no-compare", action="store_true",
        help="Generate a diagram without branch change highlighting",
    )
    ap.add_argument(
        "--project-dir", type=Path,
        help="Model Git checkout (default: input file's repository, then current directory)",
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
    if args.compare_dbml:
        try:
            data["comparison"] = compare_branch(args.input, data, args.compare_dbml)
        except ValueError as exc:
            ap.error(str(exc))
    elif not args.no_compare:
        try:
            root = find_repository(args.input, args.project_dir)
            data["comparison"] = compare_sources(root, data, args.compare, dbml_path=args.input, baseline_dbml=args.baseline_dbml)
            if data["comparison"]["unmapped"]:
                print("  Model sources without a unique diagram card: " + ", ".join(data["comparison"]["unmapped"]), file=sys.stderr)
            for warning in data["comparison"]["warnings"]:
                print(f"  {warning}", file=sys.stderr)
            if data["comparison"]["design_baseline_missing"]:
                print("  No earlier DBML design: proposed new models can be inferred, but design-only edits to existing models need --baseline-dbml.", file=sys.stderr)
        except ValueError as exc:
            if args.compare or args.project_dir or args.baseline_dbml:
                ap.error(str(exc))
            print(f"  Branch highlighting unavailable: {exc}", file=sys.stderr)
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
