"""CLI entry point for the DBML interactive diagram viewer."""

import argparse
import sys
import webbrowser
from pathlib import Path
from time import perf_counter

from dbml_viewer.parser import parse_dbml
from dbml_viewer.progress import Progress
from dbml_viewer.renderer import render_html
from dbml_viewer import review
from dbml_viewer.compare import compare_branch
from dbml_viewer.source_compare import compare_sources, find_project, find_repository, project_paths


def main():
    ap = argparse.ArgumentParser(
        description="Generate an interactive HTML diagram from a DBML file.",
        epilog="Model review uses charters in table notes, ref()/source() lineage from model SQL, "
               "and a local dbt manifest when present; no dbt build or database is required.",
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
        help="Highlight working-tree model and design changes against the merge base with REF (default: freshly fetched origin/main)",
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
        help="Model Git checkout, or a dbt project for SQL lineage without Git (default: input file's repository, then current directory)",
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

    started = perf_counter()
    with Progress() as progress:
        progress.stage(f"Parsing DBML file {args.input}")
        data = parse_dbml(args.input)
    print(
        f"  {len(data['tables'])} tables, {len(data['refs'])} refs, "
        f"{len(data['groups'])} groups, {len(data['enums'])} enums",
        file=sys.stderr,
    )
    if args.compare_dbml:
        try:
            with Progress() as progress:
                data["comparison"] = compare_branch(args.input, data, args.compare_dbml, progress=progress.stage)
        except ValueError as exc:
            data["comparison_error"] = str(exc)
            ap.error(str(exc))
    elif not args.no_compare:
        root = None
        try:
            with Progress() as progress:
                progress.stage("Locating the model Git repository")
                root = find_repository(args.input, args.project_dir)
                data["comparison"] = compare_sources(
                    root, data, args.compare, dbml_path=args.input,
                    baseline_dbml=args.baseline_dbml, progress=progress.stage,
                )
            if data["comparison"]["unmapped"]:
                print("  Model sources without a unique diagram card: " + ", ".join(data["comparison"]["unmapped"]), file=sys.stderr)
            for warning in data["comparison"]["warnings"]:
                print(f"  {warning}", file=sys.stderr)
            if data["comparison"]["design_baseline_missing"]:
                print("  No earlier DBML design: proposed new models can be inferred, but design-only edits to existing models need --baseline-dbml.", file=sys.stderr)
        except ValueError as exc:
            data["comparison_error"] = str(exc)
            # Without Git, a --project-dir dbt project still gives SQL lineage.
            project = find_project(args.input, args.project_dir) if root is None else None
            if args.compare or args.baseline_dbml or (args.project_dir and project is None):
                ap.error(str(exc))
            reason = str(exc).rstrip(".")
            where = f" Lineage built from SQL in {project}." if project else ""
            print(f"  Branch comparison unavailable: {reason}.{where}", file=sys.stderr)
    if "comparison" in data:
        comparison = data["comparison"]
        counts = comparison["counts"]
        print(
            f"  Compared with {comparison['revision']} (base {comparison['base']}): "
            f"{counts['new']} new, {counts['modified']} modified, "
            f"{counts['removed']} removed, {counts['unchanged']} unchanged",
            file=sys.stderr,
        )

    _build_review(data, args.input, args.project_dir)

    with Progress() as progress:
        progress.stage(f"Generating diagram HTML ({args.layout} layout)")
        html = render_html(data, layout=args.layout)

        progress.stage(f"Writing diagram to {output}")
        output.write_text(html, encoding="utf-8")
    print(f"Diagram ready in {perf_counter() - started:.2f}s: {output}", file=sys.stderr)

    if not args.no_open:
        url = output.resolve().as_uri()
        print(f"Opening diagram in browser: {url}", file=sys.stderr)
        webbrowser.open(url)


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 or word == "info" else "s")


def _build_review(data: dict, path: Path, project_dir: Path | None):
    """Add lineage and review findings; never block diagram generation."""
    comparison = data.get("comparison")
    try:
        root = find_repository(path, project_dir)
    except ValueError:
        # No Git: read SQL from the nearest dbt project, else lineage uses charter depends_on only.
        root = find_project(path, project_dir)
    if comparison and "model_dirs" in comparison:
        model_dirs, manifests = comparison["model_dirs"], comparison["manifests"]
    elif root is not None:
        model_dirs, manifests = project_paths(root)
    else:
        model_dirs = manifests = None
    try:
        with Progress() as progress:
            progress.stage("Building lineage and review findings")
            review.build(data, comparison=comparison, root=root, model_dirs=model_dirs, manifests=manifests)
        print("  " + review_summary(data), file=sys.stderr)
        edges = data["lineage"]["edges"]
        by_source = {source: sum(edge["source"] == source for edge in edges) for source in ("sql", "manifest", "declared")}
        print(
            f"  Lineage: {_plural(len(edges), 'edge')} (sql {by_source['sql']}, manifest {by_source['manifest']}, "
            f"declared {by_source['declared']}), {_plural(len(data['lineage']['stubs']), 'stub upstream')}",
            file=sys.stderr,
        )
    except Exception as exc:  # noqa: BLE001 - review data is optional
        print(f"  Review findings unavailable: {exc}", file=sys.stderr)


def review_summary(data: dict) -> str:
    """'Review: 22 findings on 12 models (3 errors, 15 warnings, 4 info); 4 on changed models'."""
    tables = data["review"]["tables"]
    counts = data["review"]["counts"]
    total = sum(counts.values())
    flagged = sum(bool(entry["findings"]) for entry in tables.values())
    line = (f"Review: {_plural(total, 'finding')} on {_plural(flagged, 'model')} "
            f"({_plural(counts['error'], 'error')}, {_plural(counts['warning'], 'warning')}, "
            f"{_plural(counts['info'], 'info')})")
    comparison = data.get("comparison")
    if comparison:
        statuses = comparison.get("tables") or {}
        changed = sum(len(entry["findings"]) for name, entry in tables.items()
                      if statuses.get(name, {}).get("status", "unchanged") != "unchanged")
        line += f"; {changed} on changed models"
    return line
