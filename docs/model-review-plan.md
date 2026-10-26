# Model Review Features: Plan and Design

Status: proposal · Last updated: 2026-10-23

## Goal

The viewer shows *what changed* in a branch: new, edited, and removed models, laid out by warehouse layer. Group modeling reviews also need *why each model exists*. Reviewers ask:

- What is the grain, and does the design enforce it?
- Who consumes this, and which metrics does it own?
- Why a new mart instead of extending an existing one? What does it replace?
- Where does its data come from, and what does a change affect downstream?
- Has it been validated, and has the group signed off?

This plan adds a structured metadata convention, a model charter, lineage, and review sign-off. Together they turn the generated HTML into the review artifact for a modeling session and its pull request.

## Current state (what the data provides today)

| Available | Not available yet |
|---|---|
| Table and column notes (shown as hover tooltips only) | Structured fields inside notes |
| PK, unique, not-null, defaults, indexes (raw DBML strings; composite unique indexes are recoverable) | Data lineage. Diagram arrows are DBML FK refs, not lineage. |
| Ref direction and cardinality (`>`, `<`, `-`) | dbt `depends_on`, `exposures`, `meta`, materialization. The manifest is read only for names and aliases. |
| Groups, enums, project metadata | Downstream impact (README states it is not inferred) |
| Branch change status per model (new / edited / removed) | Review decisions or comments |

Model matching is name-based and heuristic. Features must say when a result is inferred rather than declared.

## Design principles

1. **Declared beats inferred.** Prefer what authors state (grain, `replaces`, `depends_on`), and label inferred results as inferred.
2. **Works before implementation.** Proposals with no SQL yet must be fully reviewable from the DBML alone.
3. **No build required.** dbt artifacts improve results when present, but the tool must not require `dbt parse`/`compile` or a database.
4. **Static HTML stays static.** No server. Review state lives in the browser and leaves via export.
5. **Rules, not tabs.** Checks become findings in the charter. A new tab is added only when it is a different *view*.

## Metadata convention

### Location

The primary source is a YAML block inside a DBML table `Note`, delimited by `---` lines. Prose outside the block stays a normal description.

- It works for unbuilt proposals.
- `--compare-dbml` and `--baseline-dbml` already diff notes, so charter edits appear as changes.
- `pyyaml` is already a dependency.
- `pydbml` dedents multi-line notes and keeps nested indentation, so YAML survives parsing (verified).

The secondary source is dbt `meta`, `exposures`, and `depends_on` from a local manifest, used for existing models when present. **DBML wins on conflict.** Disagreements are listed as findings. No sidecar YAML: a third file to keep in sync is not worth it.

### Example

```dbml
Table fct_orders_daily {
  order_date date
  customer_id int
  order_count int
  gross_revenue decimal
  indexes {
    (order_date, customer_id) [unique]
  }
  Note: '''
  Daily order rollup per customer for revenue reporting.
  ---
  grain: [order_date, customer_id]
  purpose: Single source for daily revenue by customer.
  questions:
    - What is daily gross revenue by customer segment?
  consumers:
    - Exec revenue dashboard (Looker)
    - Finance month-end close
  owner: analytics-eng
  replaces: [rpt_daily_orders]
  depends_on: [int_customer_orders, inter_order_lines]
  metrics:
    - name: gross_revenue
      expression: sum(net_amount)
  history: current
  materialization:
    type: incremental
    unique_key: [order_date, customer_id]
  freshness: daily by 06:00 ET
  pii: []
  validation:
    reconciles_to: rpt_daily_orders
    evidence: https://example.com/validation-notebook
    tests: [unique grain, not_null customer_id]
  ---
  '''
}
```

### Schema

| Key | Type | Meaning |
|---|---|---|
| `grain` | list of column names | Columns that uniquely identify a row |
| `purpose` | string | Why the model exists |
| `questions` | list of strings | Business questions it answers |
| `consumers` | list of strings | Dashboards, teams, exposures |
| `owner` | string | Owning team or person |
| `replaces` | list of model names, or `[]` | Models it supersedes. `[]` is an explicit "replaces nothing". |
| `depends_on` | list of model names | Upstream models (required for proposals without SQL) |
| `metrics` | list of `{name, expression, description?}` | Metrics this model owns |
| `history` | `current` \| `scd2` \| `snapshot` \| `event` | Current state or history |
| `materialization` | `{type, unique_key?, strategy?}` | dbt materialization and incremental config |
| `freshness` | string | Required refresh cadence or SLA |
| `pii` | list of column names | Sensitive columns |
| `validation` | `{reconciles_to?, evidence?, tests?}` | Validation evidence and test plan |

Parsing rules:

- Unknown keys produce a warning, never a failure.
- Invalid YAML produces a finding on that table, and the note is shown as prose.
- A note with no YAML block is valid. The charter then shows only completeness gaps.
- Notes in dbt-generated DBML usually come from model `description`. Authors can put the same block there, and it will flow through.

## Features

### 1. Model charter panel (Phase 1)

The charter combines the original charter, grain check, and lint ideas.

**What it is:** a side panel opened from a card or from the existing Review panel. It shows the structured fields, the free-text description, the change status, and a list of **findings**.

**Completeness:** the required keys depend on layer.

| Layer | Required |
|---|---|
| Marts / bridges | `grain`, `purpose`, `consumers`, `owner`, `replaces` |
| Intermediate | `grain`, `purpose` |
| Prep / staging | `grain` |
| Sources / seeds | none |

Cards show a small completeness indicator. A **New** mart missing required keys is flagged in the Review list.

**Rules (findings):**

| Rule | Severity |
|---|---|
| Required key missing for the layer | error for new models, warning for existing |
| `grain` column not in the table | error |
| `grain` doesn't match the PK or a unique index | warning |
| No PK or unique index declared | warning; common in dbt-exported DBML, so never an error |
| `materialization.unique_key` differs from `grain` | error |
| `history: scd2`/`snapshot` but `grain` has no time/version column | warning |
| `pii` column not in the table | error |
| Metric name defined in more than one model | warning on each |
| `replaces` names an unknown model | warning |
| Naming prefix doesn't match the layer (configurable: `fct_`/`dim_`/`int_`/`stg_`) | info |
| Columns with no description, on a mart | info |

**Replacement:** when a model declares `replaces`, the replaced model is linked in the charter and included in Previous/Next, even if it is unchanged. Superseded and new models are then reviewed together.

**Acceptance:**

- The parser returns a `charter` dict and a `charter_findings` list per table. Prose and YAML are separated.
- Rules are pure functions with unit tests.
- `examples/layered.dbml` gains charters that trigger each severity.
- Tables without a YAML block render exactly as today.

### 2. Lineage view (Phase 2)

**What it is:** a top-level **Lineage** tab beside **Diagram**. Nodes are models and edges are data flow, kept separate from the FK diagram. Selecting a model shows its upstream and downstream closure.

**Edge sources, in order of preference:**

1. `ref()` / `source()` calls parsed from model SQL. This needs no build and covers existing and branch models.
2. Manifest `depends_on`, when a manifest is present. It catches macros and generated refs that the SQL parse misses.
3. Charter `depends_on`, for proposals with no SQL.

Edges are labeled by source. When a model has both SQL and a declared `depends_on`, differences become findings ("declared upstream `x` not referenced in SQL").

Upstream models absent from the DBML appear as small stub nodes.

**Uses:**

- **Layer violations** (findings): a mart reading a source or raw table directly, an edge going backward in layer order, intermediate skipping prep.
- **Grain-compatibility check:** an upstream whose declared grain is finer than the model's grain gets "aggregation required: confirm rollup before join". This replaces the vaguer fan-out warning. It is only reliable when both grains are declared.
- **Impact radius:** for edited models, highlight the downstream closure and list affected `consumers` and exposures.
- Ordering for the walkthrough (Phase 4).

**Limits stated in the UI:** SQL parsing handles literal `ref('x')` / `ref('pkg', 'x')`; dynamic refs need the manifest.

**Acceptance:**

- Lineage builds from SQL alone in the example repository.
- Proposals connect through declared `depends_on`.
- Violations appear as charter findings.
- Impact radius matches the hand-computed closure in tests.

### 3. Review sign-off and Markdown export (Phase 3)

**What it is:** per-model review state in the charter panel:

- **Status:** Approved / Needs changes / Open question
- Comments
- Generated checklist, built from the charter rules plus manual items (validation evidence reviewed, consumers confirmed)

A **Review summary** tab lists every changed or replaced model with its status.

**Storage:** `localStorage`, keyed by project name and model name. Each entry also stores a hash of the model's columns and charter. If the model changed after it was reviewed, its status shows as **stale**.

**Export:** a **Copy as Markdown** button produces a PR-ready summary:

- counts by status
- per-model status, comments, and open findings
- unresolved questions
- the list of replaced models

JSON export is available for archiving.

**Gating:** a model can't be marked Approved while it has error-level findings or lacks `validation.evidence`, unless the reviewer records an override reason, which is included in the export.

**Not building:** re-importing review state. Export to the PR is the deliverable.

### 4. Second tier (Phase 4)

**Overlap / "extend vs. create":** for each new mart, rank existing marts using:

1. its declared `replaces`
2. shared upstreams (from lineage)
3. same or compatible grain
4. column overlap, shown as supporting evidence only and excluding generic names (`id`, `created_at`, `updated_at`, `_loaded_at`)

This appears as a section in the charter, not a separate tab, unless reviewers need a cross-mart view.

**Walkthrough mode:** a guided tour of changed and replaced models in lineage order (sources → marts). Each step focuses the model (existing Focus changes) and opens its charter. It is mostly UI on top of existing Previous/Next.

**Bus matrix (narrow):** a matrix of facts × dimensions, built **only from declared refs to `dim_*` tables**. It shows which conformed dimensions a new fact connects to and which existing facts share them. It does not try to detect re-derived dimensions by matching column names, which produces mostly false positives.

## Phasing

| Phase | Delivers | Depends on |
|---|---|---|
| 0 | Convention docs, YAML extraction in the parser, example charters | none |
| 1 | Charter panel, completeness, findings, `replaces` linking | 0 |
| 2 | Lineage tab, layer violations, grain compatibility, impact radius | 0 |
| 3 | Sign-off, review summary, Markdown export | 1 (gating uses findings) |
| 4 | Overlap section, walkthrough, narrow bus matrix | 1, 2 |

Phases 1 and 2 can proceed in parallel after Phase 0.

## Testing

- Python `unittest` covers note/YAML extraction, every charter rule, `ref()` parsing, lineage closure, and manifest merging (including a missing or invalid manifest).
- `node --test` covers lineage layout and the sign-off state, including stale detection and the Markdown export format.
- Each phase updates `examples/` and the README. A demo similar to `examples/preview_changes.py` shows a new mart replacing a legacy one, with charter findings and lineage.

## Deferred / out of scope

- Re-importing review state, and shared or multi-user review storage (would need a server)
- Sidecar metadata files
- Detecting re-derived dimensions by column name
- Cost estimates from warehouse metadata
- Column-level lineage

## Open questions

1. **Where do charters live after merge?** If the team's DBML is generated from dbt, the YAML block belongs in the dbt model `description` (or `meta`, which would need export support) so it survives regeneration. Confirm how DBML is produced today.
2. **Required keys per layer:** is the table above right for this team, and should `metrics` be required for marts?
3. **Gating strictness:** should missing validation evidence block Approved, or only warn?
4. **Naming conventions:** which prefixes does the project use, and should they be configured in `dbt_project.yml` vars or a CLI flag?
