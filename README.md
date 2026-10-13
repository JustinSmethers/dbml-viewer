# DBML Viewer

A Python CLI tool that parses [DBML](https://dbml.dbdiagram.io/) files and generates interactive ERD diagrams in the browser.

## Installation

Requires Python 3.10+.

```bash
# From PyPI
uv tool install dbml-viewer

# From a local checkout while developing
cd dbml-viewer
uv tool install --from . dbml-viewer
```

## Development

```bash
uv run python -m unittest discover -s tests
node --test tests/test_layout.cjs
uv build
```

## Usage

```bash
# Generate and open diagram in browser
dbml-viewer path/to/model.dbml

# Specify output path
dbml-viewer model.dbml -o diagram.html

# Top-to-bottom layout (default is left-to-right)
dbml-viewer model.dbml --layout TB

# Try the sample warehouse
dbml-viewer examples/layered.dbml

# Generate without opening browser
dbml-viewer model.dbml --no-open

# Highlight this branch's DBML changes, including local edits
dbml-viewer model.dbml --compare main
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `input` | Path to `.dbml` file (required) | — |
| `-o`, `--output` | Output HTML file path | `<input>_diagram.html` |
| `--layout` | Layout direction: `TB` (top-to-bottom) or `LR` (left-to-right) | `LR` |
| `--no-open` | Skip auto-opening the browser | Off |
| `--compare REF` | Compare the input with its Git snapshot at the merge base of `HEAD` and `REF` | Off |

### Reviewing branch changes

Use `--compare main` (or `--compare origin/main`) inside a Git checkout. The viewer compares the current on-disk DBML, including uncommitted edits, with the same path at the branches' common ancestor. The chosen ref must exist locally; fetch it yourself if needed. A file absent at the baseline is treated as new. Renamed models or DBML files appear as removals/additions or an all-new file; renames are not inferred.

The **Highlight changes** toggle adds green outlines and `+ NEW` badges to new models, and amber outlines and `~ EDITED` badges to modified models. Layer colors stay visible. Unchanged models fade slightly. **Focus changes** zooms to changed models and their immediate relationship neighbors, fading unrelated cards and joins. Toggling highlighting preserves table positions.

Click a card's badge or **Review** to inspect its changes and emphasize that model and its immediate neighbors. **Previous** and **Next** cycle through changed models, including removals. Removed models appear in the review panel and counts; they do not occupy ghost cards in the diagram. Escape closes the panel and clears a pinned join.

Comparison ignores comments, whitespace, declaration order and equivalent `<`/`>` ref spelling. It detects added/removed columns, types, key/null/unique/increment/default constraints, notes, indexes, groups, group colors, enums used by columns, and declared relationships. A relationship change marks both endpoint models as modified. This reflects the schema represented in DBML; SQL-only changes and undeclared relationships are not detected. Changes are captured when HTML is generated; regenerate to refresh them.

For a demo with one new, one modified and one removed model:

```bash
uv run python examples/preview_changes.py
# Open examples/branch-changes.html
```

The demo creates a disposable Git repository and leaves your checkout untouched.

## Interacting with the Diagram

The output is an HTML file — no server needed. Open it in any modern browser. Dagre loads from a CDN; if it is unavailable, the viewer uses its built-in layer layout.

The default view arranges warehouse layers from left to right:

- **Sources:** source/src, raw, snapshots, external, and landing.
- **Seeds:** seed/seeds and reference tables.
- **Preparation:** prep/prepared and staging/stg.
- **Intermediate:** int/inter, intermediate, integration, and transformations.
- **Marts:** mart/marts, report/reporting/rpt, dim/dimension, fact/fct, analytics, and metrics.
- **Bridges:** bridge tables occupy a separate category and column to the right of marts.

Sources and seeds stack in the same leftmost column, with separate group labels and colors. Columns then proceed through preparation → intermediate → marts → bridges. The group headers label each category; there are no separate rank banners above the diagram. Sources and seeds represented as DBML tables are included with their columns and declared relationships. Names such as `source_orders`, `src_customers`, and `seed_order_status`, or groups named `sources` and `seeds`, identify their categories. Standalone seed CSVs and source YAML files are not read by the viewer.

Layer names are recognized as whole tokens in group names or table names, including camel case. Source, seed, and bridge names keep their own ranks even inside a broader group; otherwise, a recognized group takes precedence over the table name. Unrecognized tables inherit a processing layer from connected tables when possible, but are not inferred to be sources, seeds, or bridges; disconnected tables use intermediate. Empty ranks are omitted. Diagrams with no recognized layers, or only one layer, use a dependency layout instead.

Within each rank, tables stay together by group. The layout tries neighboring table and group swaps to reduce the total vertical distance between join columns. Lines compare left/right connection sides, detour around cards, and penalize sharing tracks with previously routed joins. Each join has its own color and endpoint dots; crowded join columns fan out to distinct attachment points. Small gaps distinguish crossings. Routing is a heuristic, so very dense graphs can still have crossings or short shared sections. Arrowheads retain the DBML relationship direction; they do not represent data lineage.

### Navigation

| Action | How |
|--------|-----|
| **Pan** | Click and drag on the background |
| **Zoom** | Scroll wheel (zooms toward cursor) |
| **Fit to screen** | Click the arrow button in the top-right controls |
| **Reset zoom** | Click the "1:1" button |

### Tables

| Action | How |
|--------|-----|
| **Drag a table** | Click and drag anywhere on the table |
| **Collapse/expand columns** | Click the table header |
| **Collapse all** | Click the up-triangle button in controls |
| **Expand all** | Click the down-triangle button in controls |
| **Keys only / All columns** | Toggle between all columns and primary keys, foreign keys, and columns used in relationships |
| **Re-layout** | Recompute placement using the current visible columns and fit to screen |
| **View table note** | Hover over the table header |
| **View column note** | Hover over a column row |

### Groups

| Action | How |
|--------|-----|
| **Drag a group** | Click and drag the group header bar — all member tables move together |

Groups spanning several categories get a separate background for each category. Source, seed, and bridge blocks receive their own labels even when their DBML group is broader. Tables without a DBML group receive an inferred category label when their layer is recognized. Dragging a visible group header moves only the tables in that block.

### Relationships

| Action | How |
|--------|-----|
| **View join columns** | Hover over an edge line — tooltip shows `source.column -> target.column` |
| **Highlight join** | Hovering an edge highlights both connected tables and their specific join columns |
| **Pin a join** | Click a line to keep its tables highlighted and show the exact table/column names while panning or zooming |
| **Clear pinned join** | Click it again, click the close button in the selected-join panel, or press Escape |

Unrelated tables and lines fade while a join is highlighted. Collapse-all, expand-all, and the column filter recompute placement automatically; individual header toggles preserve manual placement.

Edges connect at the specific join columns and route with 90-degree angles. Connection sides are chosen by comparing route length, bends, and table obstructions.

## DBML Features Supported

- Tables with columns, types, and constraints (`pk`, `not_null`)
- Column and table notes
- Relationships (`Ref:` with `>`, `<`, `-` types)
- Table groups (`TableGroup`) — rendered as colored clusters
- Enums
- Project metadata

## Color Scheme

Groups are colored by layer:

| Group | Color |
|-------|-------|
| Snapshot | Purple |
| Prep | Blue |
| Intermediate | Amber |
| Bridge | Indigo |
| Mart | Green |
| External | Gray |
| Ungrouped | Blue-gray |

## Architecture

```
src/
└── dbml_viewer/
    ├── __main__.py        # Package CLI entry point
    ├── main.py            # Console-script entry point
    ├── parser.py          # DBML parsing via pydbml -> normalized dict
    ├── renderer.py        # Jinja2 template rendering + color scheme
    └── templates/
        ├── diagram.html.j2
        └── layout.js      # Layer classification, placement, and edge routing
```

The tool works in three steps:
1. **Parse** — `pydbml` reads the DBML file; `parser.py` normalizes it into a dict of tables, columns, refs, groups, and enums
2. **Render** — `renderer.py` injects the data as JSON into a Jinja2 HTML template
3. **Layout** — [dagre.js](https://github.com/dagrejs/dagre) seeds the ordering or computes generic diagrams. The built-in layer layout constrains warehouse stages and shortens joins. The browser renders measured table cards, group backgrounds, and obstacle-aware SVG paths.
