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

# Branch model source changes are highlighted by default against main
dbml-viewer model.dbml

# Use a different comparison branch
dbml-viewer model.dbml --compare main

# Skip branch comparison
dbml-viewer model.dbml --no-compare

# Explicitly compare the DBML schema instead of model source files
dbml-viewer model.dbml --compare-dbml main

# Include design-only edits when the DBML isn't tracked in Git
dbml-viewer design.dbml --baseline-dbml before.dbml
```

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `input` | Path to `.dbml` file (required) | — |
| `-o`, `--output` | Output HTML file path | `<input>_diagram.html` |
| `--layout` | Layout direction: `TB` (top-to-bottom) or `LR` (left-to-right) | `LR` |
| `--no-open` | Skip auto-opening the browser | Off |
| `--compare REF` | Compare model source files against the merge base of `HEAD` and `REF`, including local edits | Freshly fetched `origin/main`; local `main` without an origin remote |
| `--no-compare` | Disable branch change highlighting | Off |
| `--compare-dbml REF` | Compare the DBML schema itself with its Git snapshot at the merge base | Off |
| `--project-dir PATH` | Select the model Git checkout when the generated DBML is elsewhere | Input file's repository, then current directory |
| `--baseline-dbml PATH` | Earlier DBML design for detecting changes before implementation | Same input path at the Git merge base, if tracked |

### Reviewing branch changes

Branch highlighting is automatic and combines code changes with pipeline design changes. The viewer fetches `origin/main` and compares SQL and Python model source files and seed CSV files with its common ancestor with `HEAD`. This avoids stale local `main` branches. It includes committed branch changes, staged and unstaged edits, and non-ignored untracked model and seed files. Logic-only changes and seed data edits highlight a model even when its columns and relationships are unchanged.

For dbt projects, the viewer reads `model-paths` from `dbt_project.yml`; otherwise it looks under directories named `models`. It matches diagram names using a local manifest, literal SQL `config(alias=...)` values, YAML declarations, and model filenames. Unique matches also allow case differences and schema qualification. A missing or unreadable manifest falls back to project files, so no dbt build or database connection is needed. Changed sources with missing or ambiguous cards are still listed in **Review** with their paths.

When no earlier DBML is available, a diagram model absent from the project name inventory is highlighted as **new**, with a review detail explaining that it is a proposed model without an implementation. This includes proposed seeds: a seed name or group does not suppress the new badge, and no CSV or database build is required. Existing source/seed/snapshot declarations and files, external input layer names, and ambiguous matches are excluded from this inference. Names and aliases found in the branch baseline are also considered. This is a name-based inference: undeclared or computed aliases can still need a DBML baseline to avoid incorrect matches. New and edited seed CSVs are matched using seed filenames, YAML/manifest aliases, and configured `seed-paths`.

To detect **modified models before changing their SQL**, the viewer also compares the DBML design with its merge-base snapshot when that same DBML path is tracked. For generated or ignored DBML, save an earlier export before making design edits and pass it with `--baseline-dbml before.dbml`. Neither DBML file needs to be tracked or built. Column, constraint, note, group, enum, index, and relationship differences are merged with source changes; additions and removals are reviewable too. An unchanged model in the earlier design stays unchanged even if its SQL does not exist yet. Without an earlier design, names alone cannot detect schema edits to an existing model; the CLI and diagram state this limitation.

Macros, tests, model YAML, and snapshots do not mark models as edited solely from their file diffs, and indirect downstream impact is not inferred. YAML and snapshots are used to recognize existing project names.

Use `--compare REF` to choose another baseline, `--no-compare` to disable the overlay, or `--project-dir /path/to/project` when the generated DBML is outside your model repository and you are running elsewhere. Both `--compare main` and `--compare origin/main` refresh and use `origin/main` when an origin remote is configured. Other comparison refs must exist locally. Fetching updates the remote tracking ref without switching branches or moving local `main`. If the fetch fails, the viewer reports that comparison is unavailable and generates the diagram without highlighting; explicitly requested comparisons exit with an error. It never silently falls back to a stale local main after a failed fetch. Repositories without an origin remote can use local `main` (or an existing `origin/main`). Renaming a model source appears as removal/addition; moving a file while preserving a uniquely matched model name appears as a modification.

The **Highlight changes** toggle adds green outlines and `+ NEW` badges to new models, and amber outlines and `~ EDITED` badges to modified models. Layer colors stay visible. Unchanged models fade slightly. **Focus changes** zooms to changed models and their immediate relationship neighbors, fading unrelated cards and joins. Toggling highlighting preserves table positions.

Click a card's badge or **Review** to inspect its changes and emphasize that model and its immediate neighbors. **Previous** and **Next** cycle through changed models, including removals. Removed models appear in the review panel and counts; they do not occupy ghost cards in the diagram. Escape closes the panel and clears a pinned join.

Source comparison detects file changes, including comments and formatting. Changes are captured when HTML is generated; regenerate to refresh them.

For semantic schema review, use `--compare-dbml REF`. This opt-in mode compares the current on-disk DBML with the same path at the merge base. It ignores comments, whitespace, declaration order and equivalent `<`/`>` ref spelling. It detects added/removed columns, types, key/null/unique/increment/default constraints, notes, indexes, groups, group colors, enums used by columns, and declared relationships. A relationship change marks both endpoint models as modified. A DBML file absent at the baseline is treated as all-new; renames are not inferred.

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

Sources and seeds stack in the same leftmost column, with separate group labels and colors. Columns then proceed through preparation → intermediate → marts → bridges. The group headers label each category; there are no separate rank banners above the diagram. Sources and seeds represented as DBML tables are included with their columns and declared relationships. Names such as `source_orders`, `src_customers`, and `seed_order_status`, or groups named `sources` and `seeds`, identify their categories. Seed CSVs and source YAML files inform comparison and name matching; only DBML tables become diagram cards.

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
    ├── source_compare.py  # Model source changes against the branch merge base
    ├── compare.py         # Opt-in semantic DBML comparison
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
