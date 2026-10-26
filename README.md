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
node --test tests/*.cjs
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

The CLI reports each stage separately, including parsing, fetching `origin/main`,
scanning model sources, comparing the earlier DBML design, and generating HTML.
Interactive terminals show a spinner with elapsed time while a stage runs, then
its completion time. Redirected output uses plain status lines. A final summary
reports the total generation time; browser layout runs after the HTML opens.
Model review uses charters in table notes, `ref()`/`source()` lineage from model
SQL, and a local dbt manifest when present; no dbt build or database is needed.

| Flag | Description | Default |
|------|-------------|---------|
| `input` | Path to `.dbml` file (required) | — |
| `-o`, `--output` | Output HTML file path | `<input>_diagram.html` |
| `--layout` | Layout direction: `TB` (top-to-bottom) or `LR` (left-to-right) | `LR` |
| `--no-open` | Skip auto-opening the browser | Off |
| `--compare REF` | Compare model source files (and the DBML design, when an earlier one is found) in the working tree against the merge base of `HEAD` and `REF` | Freshly fetched `origin/main`; local `main` without an origin remote |
| `--no-compare` | Disable branch change highlighting (lineage and findings are still built) | Off |
| `--compare-dbml REF` | Compare the DBML schema itself with its Git snapshot at the merge base | Off |
| `--project-dir PATH` | Select the model Git checkout (or, without Git, the dbt project read for SQL lineage) when the generated DBML is elsewhere | Input file's repository, then current directory |
| `--baseline-dbml PATH` | Earlier DBML design for detecting changes before implementation | Same input path at the Git merge base, if tracked |

### Reviewing branch changes

Branch highlighting is automatic and combines code changes with pipeline design changes. The viewer fetches `origin/main` and compares SQL and Python model source files and seed CSV files with its common ancestor with `HEAD`. This avoids stale local `main` branches. It includes committed branch changes, staged and unstaged edits, and non-ignored untracked model and seed files. Logic-only changes and seed data edits highlight a model even when its columns and relationships are unchanged.

For dbt projects, the viewer reads `model-paths` from `dbt_project.yml`; otherwise it looks under directories named `models`. It matches diagram names using a local manifest, literal SQL `config(alias=...)` values, YAML declarations, and model filenames. Unique matches also allow case differences and schema qualification. A missing or unreadable manifest falls back to project files, so no dbt build or database connection is needed. Changed sources with missing or ambiguous cards are still listed in **Review** with their paths, flagged `unmapped`, and counted, but they are not walkthrough steps. A deleted model file whose name or alias matches a card removed from the earlier DBML design is part of that removed model's entry, not a second one. Each changed model or seed source carries its unified diff against the merge base (untracked files against an empty file; binary files skipped; at most 300 lines per file).

When no earlier DBML is available, a diagram model absent from the project name inventory is highlighted as **new**, with a review detail explaining that it is a proposed model without an implementation. This includes proposed seeds: a seed name or group does not suppress the new badge, and no CSV or database build is required. Existing source/seed/snapshot declarations and files, external input layer names, and ambiguous matches are excluded from this inference. Names and aliases found in the branch baseline are also considered. This is a name-based inference: undeclared or computed aliases can still need a DBML baseline to avoid incorrect matches. New and edited seed CSVs are matched using seed filenames, YAML/manifest aliases, and configured `seed-paths`.

To detect **modified models before changing their SQL**, the viewer also compares the DBML design with its merge-base snapshot when that same DBML path is tracked. For generated or ignored DBML, save an earlier export before making design edits and pass it with `--baseline-dbml before.dbml`. Neither DBML file needs to be tracked or built. Column, constraint, note, group, enum, index, and relationship differences are merged with source changes; additions and removals are reviewable too. A relationship to a new or removed model is a detail of that model only, so a dimension is not edited because a new fact points a foreign key at it; a relationship change between two existing models marks both. An unchanged model in the earlier design stays unchanged even if its SQL does not exist yet. Without an earlier design, names alone cannot detect schema edits to an existing model; the CLI and diagram state this limitation.

Macros, tests, model YAML, and snapshots do not mark models as edited solely from their file diffs, and indirect downstream impact is not inferred. YAML and snapshots are used to recognize existing project names.

Use `--compare REF` to choose another baseline, `--no-compare` to disable the overlay, or `--project-dir /path/to/project` when the generated DBML is outside your model repository and you are running elsewhere. Both `--compare main` and `--compare origin/main` refresh and use `origin/main` when an origin remote is configured. Other comparison refs must exist locally. Fetching updates the remote tracking ref without switching branches or moving local `main`. If the fetch fails, or there is no Git checkout, the viewer prints `Branch comparison unavailable: <reason>` and generates the diagram without highlighting; the reason is also stored for the diagram header. Explicitly requested comparisons exit with an error. It never silently falls back to a stale local main after a failed fetch. Repositories without an origin remote can use local `main` (or an existing `origin/main`). Renaming a model source appears as removal/addition; moving a file while preserving a uniquely matched model name appears as a modification.

The **Highlight changes** toggle adds green outlines and `+ NEW` badges to new models, and amber outlines and `~ EDITED` badges to modified models. Layer colors stay visible. Unchanged models fade slightly. **Focus changes** zooms to changed models and their immediate relationship neighbors, fading unrelated cards and joins. Toggling highlighting preserves table positions.

Click a card's badge or **Review** to open the model review panel (see [Review panel and sign-off](#review-panel-and-sign-off)) and emphasize that model and its immediate neighbors. **Previous** and **Next** follow the walkthrough first (changed models from sources to marts, each preceded by the models it replaces, including removals), then every other model. Removed models appear in the review panel and counts; they do not occupy ghost cards in the diagram. Escape closes the panel and clears a pinned join.

Source comparison detects file changes, including comments and formatting. Changes are captured when HTML is generated; regenerate to refresh them.

For semantic schema review, use `--compare-dbml REF`. This opt-in mode compares the current on-disk DBML with the same path at the merge base. It ignores comments, whitespace, declaration order and equivalent `<`/`>` ref spelling. It detects added/removed columns, types, key/null/unique/increment/default constraints, notes, indexes, groups, group colors, enums used by columns, and declared relationships. Relationship changes follow the same rule as above: only a new or removed endpoint gets the detail when there is one. A DBML file absent at the baseline is treated as all-new; renames are not inferred.

For a demo with one new, one modified and one removed model:

```bash
uv run python examples/preview_changes.py
# Open examples/branch-changes.html
```

The demo creates a disposable Git repository and leaves your checkout untouched.

### Model charters

A table note can carry a **charter**: a YAML block between two `---` lines that states the model's grain, purpose, consumers, owner, what it replaces, its metrics, and its validation. Text outside the block stays the normal description and tooltip. The viewer checks each charter against the table design and the model's layer. It reports findings such as a grain column that doesn't exist, a grain no key enforces, an incremental `unique_key` that differs from the grain, or a new mart missing required keys. Tables without a block get no findings unless they are new, so a DBML export without charters renders as before.

```dbml
Table fct_orders_daily {
  order_date date
  customer_id int
  indexes {
    (order_date, customer_id) [unique]
  }
  Note: '''
  Daily order rollup per customer.
  ---
  grain: [order_date, customer_id]
  purpose: Single source for daily revenue by customer.
  consumers: [Exec revenue dashboard]
  owner: analytics-eng
  replaces: []
  ---
  '''
}
```

See [docs/charter-convention.md](docs/charter-convention.md) for every key, the required keys by layer, the rules and their severities, and how to keep charters in dbt model descriptions. `examples/layered.dbml` has a charter on most tables.

### Lineage and review findings

The viewer builds **lineage**, the data flow between models, separately from DBML refs (which describe joins). Edges come from, in order of preference:

1. Literal `ref('x')`, `ref('pkg', 'x')` and `source('s', 't')` calls in model SQL, and `dbt.ref(...)` / `dbt.source(...)` in Python models. Nothing is compiled or executed, so dynamic refs need a manifest.
2. `depends_on.nodes` from a local dbt manifest (`target/manifest.json` or the configured `target-path`). A missing or unreadable manifest is ignored with a note.
3. A charter's `depends_on`, so proposals without SQL still connect.

SQL is read from the `model-paths` in `dbt_project.yml` (default `models`) of the model repository: the input file's Git checkout, `--project-dir`, or the current directory. This also works with `--no-compare` and `--compare-dbml`. Without Git, the nearest directory containing `dbt_project.yml` (walking up from `--project-dir`, the DBML's directory, then the current directory) is used and the CLI prints `Lineage built from SQL in <root>`; with no dbt project either, lineage uses charter `depends_on` only.

Declared names match diagram tables with or without a schema when the match is unique. Upstreams that are not diagram tables, such as `source()` tables or package models, appear as stubs. Each model lists its direct upstreams and downstreams, its full downstream impact, and the charter `consumers` affected.

Lineage findings (those from SQL-only edges are marked inferred):

- `layer-violation` (warning): a mart or bridge reads a source or seed directly, or an edge goes backward in layer order (source, seed, prep, intermediate, mart, bridge). An intermediate reading a source is allowed.
- `declared-upstream-mismatch`: a declared `depends_on` is missing from SQL or the manifest (warning), or SQL/the manifest reads a model `depends_on` omits (info).
- `grain-compatibility` (warning), only when both grains are declared: an upstream grain is strictly finer than this model's ("aggregation required: confirm rollup before join"), or an upstream grain column is not a column of this model ("Upstream `int_x` grain (...) is not carried by this model's columns: confirm the rollup or join"). A join whose upstream grain columns all exist here is a key lookup and is not flagged.
- `upstream-unknown` (info): a declared `depends_on` is neither a diagram table nor a known project model.
- `no-upstream` (info): a new model (not a source or seed) has no upstream at all; add `depends_on` to its charter.

For each new mart or bridge, **Extend instead of create?** ranks up to three existing marts or bridges of the same kind: a new dimension (a `dim`/`dimension` name token) is compared with dimensions only, anything else with non-dimensions only. Scores count a duplicated charter metric (+3, listed first), shared upstreams, matching or compatible grain, and shared non-generic column names (supporting evidence only). Ties go to the same grain, then more shared upstreams. Models it declares in `replaces` are linked separately.

The walkthrough orders changed models, and the models they replace, from sources to marts. Removed models are included and keep their earlier columns and description; unmapped source paths are not. A facts × dimensions bus matrix uses declared DBML refs from marts or bridges to `dim_*` tables only. The CLI prints a summary such as `Review: 22 findings on 9 models (3 errors, 15 warnings, 4 info); 5 on changed models`, then lineage edge counts by source. If review data can't be built, the diagram is still generated.

### Lineage view

The **Lineage** tab beside **Diagram** draws data flow, not joins. Columns follow layer order: models outside the DBML (stubs, shown as grey pills labelled source, model, or unknown), then sources and seeds, prep, intermediate, marts, and bridges. Each column starts at the top. Edges are solid for `ref()` in SQL, dashed for the dbt manifest, and dotted for a declared `depends_on`. The parsing limits are listed under the legend. Cards widen to fit the model name (180 to 340 px; longer names wrap), and a column's cards share its width. Cards show the change badge and the number of charter findings, colored by the worst severity.

The graph scrolls in its own area, below the Lineage bar and to the right of the project header and review panel. When the view first opens with changed models, it scrolls so the first changed model in walkthrough order and its column header are in view, with that column (or the furthest one that fits) at the left edge. With no changes, it fits the graph to the width when that keeps at least 50% scale, and otherwise shows it at actual size from the left.

- **Click a model** to highlight its upstream closure (blue) and downstream closure (pink) and fade the rest. A strip above the graph shows its direct upstream and downstream counts, its impact (all downstream models), and the consumers affected. The model panel opens for it. Click the background or press Esc to clear.
- While Lineage is active, **Show in lineage**, **Previous** and **Next** in the model panel select the model in the graph and scroll it into view.
- **Impact of changes** appears when the branch has new or edited models. It highlights them and everything downstream of them, and lists the affected consumers.
- **Fit** scales the graph to the window width (down to 40%); **1:1** restores actual size. Otherwise, scroll.
- When marts or bridges have declared refs to `dim_*` tables, a collapsed **Bus matrix: N facts × M dims** section in the Lineage bar expands to a model × dimensions table.

### Review panel and sign-off

The review machinery appears only when something is under review: a branch comparison that found new, modified, or removed models. Otherwise the branch toolbar is one muted line (`No changes since main · merge base …`, nothing without a comparison, or an amber `Branch comparison unavailable: …` when the comparison failed), and the panel and the third tab describe models without sign-off.

Every card header has a small dot colored by its worst finding: red for an error, amber for a warning, grey otherwise. Hover it for the counts (`Review model · 1 error, 2 warnings`); click it (or a change badge, **Review**, a Summary or Catalog row, or a lineage model) to open the model panel. Opening it from the diagram keeps your zoom when the card is already fully in view; otherwise the diagram fits the model and its neighbours, at no more than actual size.

The panel puts the verdict first. From the top: the model name; its layer and change status; the status buttons, **Stale** badge and approval-gate note; a one-line `Purpose … · Grain …` (from the charter, else the description's first sentence and the primary key, tagged **PK**); **Findings**; **Changes** (the change details and, for edited or removed sources, a collapsed **Source diff (N files)**); the full **Charter** with the description and linked `replaces`, replaced-by and `depends_on` models; **Lineage** (upstream, downstream, impact and affected consumers for edited models, **Joined with (N)** from DBML refs, **Show in lineage** and **Show in diagram**); "Extend instead of create?" candidates for new marts; and **Sign-off**. Empty sections are left out. For a removed model the panel shows its layer, description, and columns from the comparison baseline, and **Show replacements in diagram** fits the diagram to the models that replace it. Without anything under review, the status buttons, gate note, **Acknowledge** boxes and Sign-off are hidden, and the charter's **Missing** keys show only for models with a charter block.

**Previous** and **Next** step through the walkthrough (changed models from sources to marts, each preceded by the models it replaces, including removals), then every other model in lineage order, and work from any view. The counter reads `2 of 3 in walkthrough` inside the walkthrough and `5 of 15 models` after it. Opening any model from a dot, row, or the lineage continues from its place in that order. With nothing under review, the order is every model (`n of N models`).

Keyboard: with the panel open and focus outside a text field, **←** and **→** are Previous and Next. **Esc** closes the panel; inside a comment or override field it only leaves the field.

Sign-off records a status (**Approved**, **Needs changes**, **Open question**), shown with a **Stale** badge when the model changed since. Each error or warning finding has an **Acknowledge** checkbox: it records that the finding was discussed, and the finding stays open in the export. The Sign-off section at the bottom holds a comment, the manual checks (grain confirmed, validation evidence reviewed, consumers confirmed), and the override reason. **Approved** is refused while the model has error findings, or when it lacks `validation.evidence` and is new or a mart or bridge. Removed models, and models with no charter and no required keys, never need evidence. An override reason lets you approve anyway and is exported with what it overrode. Clearing the reason withdraws the approval (the comment stays). Click an active status again to clear it.

State is saved in the browser's `localStorage` under `dbml-viewer:review:<project name>`, or `dbml-viewer:review:path-<hash of the page path>` when the DBML has no project name, one entry per model. Each entry stores a hash of the model's columns, charter, and the refs it is an endpoint of. If any of those change after a status was set (regenerate the HTML), the entry keeps its status and shows **Stale: model changed since review**; click the status again to confirm it. An approval that is blocked and has no override reason (for example from older saved state) is shown and exported as **Approved (invalid: blocked without override)**. Nothing is sent anywhere, and review state is not re-imported.

While under review, the **Summary** tab lists the walkthrough models with layer, change, finding counts, sign-off status (with an **override** tag when an override reason allowed the approval) and replacements, plus counts by status and severity. Changed source files with no unique diagram card follow under **Unmatched source changes**, and a collapsed **Catalog (all models)** lists every model. **Copy as Markdown** produces a PR-ready summary:

```markdown
## Model review: shop · 2026-10-23

Baseline: main (merge base 1a2b3c4d)

**2 models:** 1 approved · 1 needs changes · 0 open question · 0 not reviewed

- `marts_customers` (modified mart): **Approved**
  - Purpose: Single customer record · Grain: customer_id
  - Affected consumers: Customer health dashboard
  - Override: PII fix tracked in JIRA-12 (1 error finding)
  - Warnings:
    - warning: Metric net_revenue is also defined in reports_revenue.
  - Acknowledged (still open):
    - error: PII column email is not a column of marts_customers.
  - Checks: grain confirmed, consumers confirmed
- `fct_customer_segments` (new mart): **Needs changes**
  - Comment: Document the score columns
  - Warnings:
    - warning: aggregation required: confirm rollup before join.

### Action items

- `fct_customer_segments`: Document the score columns
```

Unticked error findings are listed under **Blocking**; ticked ones stay under **Acknowledged (still open)**. A needs-changes model lists every error and warning, ticked or not. **Unresolved questions** collects open-question comments, then come replaced models, and **Action items** collects needs-changes comments at the end. If the clipboard is unavailable, the Markdown appears in a text box to copy by hand. **Download JSON** saves the full state for archiving, and **Clear review state** removes it for this project.

With nothing under review, the third tab is the **Catalog**: every model in layer order with its layer, purpose (or the description's first sentence), grain (or the primary key, tagged **PK**), owner, consumers, and error, warning, and info counts. Click a row to open the model.

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
        ├── layout.js      # Layer classification, placement, and edge routing
        └── lineage-layout.js  # Lineage view columns, ordering, and edge paths
```

The tool works in three steps:
1. **Parse** — `pydbml` reads the DBML file; `parser.py` normalizes it into a dict of tables, columns, refs, groups, and enums
2. **Render** — `renderer.py` injects the data as JSON into a Jinja2 HTML template
3. **Layout** — [dagre.js](https://github.com/dagrejs/dagre) seeds the ordering or computes generic diagrams. The built-in layer layout constrains warehouse stages and shortens joins. The browser renders measured table cards, group backgrounds, and obstacle-aware SVG paths.
