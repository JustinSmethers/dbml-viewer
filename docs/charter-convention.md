# Model charter convention

A **charter** records why a model exists: its grain, purpose, consumers, owner, and what it replaces. The viewer reads it from the DBML table note and checks it against the table design. Results are listed as findings in the model review.

## Where the block goes

Put a YAML mapping inside the table `Note`, between two lines that are exactly `---`. Text outside the block stays the normal description and is what the card tooltip shows.

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

A note with no block is valid and gets no findings, unless the model is new in the branch; then the missing required keys, naming prefix, and undocumented mart columns are reported. `examples/layered.dbml` has a charter on most tables.

## Keys

| Key | Type | Meaning |
|---|---|---|
| `grain` | list of column names | Columns that uniquely identify a row |
| `purpose` | string | Why the model exists |
| `questions` | list of strings | Business questions it answers |
| `consumers` | list of strings | Dashboards, teams, exposures |
| `owner` | string | Owning team or person: either works (`analytics-eng`, `jane.doe`) |
| `replaces` | list of model names, or `[]` | Models it supersedes. `[]` means "replaces nothing" and counts as answered. |
| `depends_on` | list of model names | Every model the SQL reads, including dimensions it only joins to (needed for proposals without SQL) |
| `metrics` | list of `{name, expression, description?}` | Metrics this model owns |
| `history` | `current` \| `scd2` \| `snapshot` \| `event` | Current state or history; use `snapshot` for periodic snapshots (one row per entity per period) |
| `materialization` | `{type, unique_key?, strategy?}` | dbt materialization and incremental config |
| `freshness` | string | Required refresh cadence or SLA |
| `pii` | list of column names | Sensitive columns |
| `validation` | `{reconciles_to?, evidence?, tests?}` | Validation evidence and test plan |

Two choices proposers often have to guess: a monthly or daily account snapshot is `history: snapshot` (its grain then needs a period column such as `snapshot_date`), and `depends_on` should list dimensions joined for lookups as well as the models that feed rows, so lineage and `declared-upstream-mismatch` agree with the SQL.

Parsing is lenient:

- A single value is accepted where a list is expected: `grain: customer_id` reads as `[customer_id]`. For `grain`, `replaces`, `depends_on`, `pii` and `unique_key`, a comma-separated string is split: `grain: order_date, customer_id`.
- `materialization: view` reads as `{type: view}`, and `metrics: [revenue]` reads as `[{name: revenue}]`.
- Unknown keys are kept and produce a warning (with a suggestion for likely typos). They never fail the build.
- The charter is the first YAML mapping between two consecutive `---` lines. Markdown rules around prose, a lone or unclosed `---`, and a block that isn't a mapping stay prose with no finding.
- A block whose first line looks like a key (`grain:`) but can't be parsed produces an `invalid-yaml` error. The whole note is then shown as prose, and `required-key-missing` is not reported on top of it.
- In name lists (`grain`, `replaces`, `depends_on`, `pii`, `unique_key`), nested mappings or lists are ignored.

## Required keys by layer

Layers come from the same table-name and group classification the diagram uses.

| Layer | Required |
|---|---|
| Marts / bridges | `grain`, `purpose`, `consumers`, `owner`, `replaces` |
| Intermediate | `grain`, `purpose` |
| Prep / staging | `grain` |
| Sources / seeds / unclassified | none |

## Rules

| Rule id | Checks | Severity |
|---|---|---|
| `invalid-yaml` | A block that starts like a YAML mapping can't be parsed | error |
| `unknown-key` | A key that isn't in the table above | warning |
| `required-key-missing` | A required key for the layer is missing or empty (tables with a block, or new tables) | error for **new** models, warning otherwise |
| `grain-column-unknown` | A `grain` column isn't a column of the table | error |
| `grain-not-unique` | `grain` doesn't match the PK, a `[unique]` column, or a `[pk]`/`[unique]` index | warning |
| `no-unique-key` | `grain` is declared but no PK or unique index exists (skipped for sources and seeds) | warning |
| `unique-key-mismatch` | `materialization.unique_key` differs from `grain` | error |
| `history-grain` | `history: scd2` or `snapshot`, but no grain column looks like a time or version (`valid_from`, `valid_to`, `effective`, `version`, `snapshot`, `_at`, `date`) | warning |
| `pii-column-unknown` | A `pii` column isn't a column of the table | error |
| `metric-duplicate` | The same metric name is defined in more than one model | warning on each |
| `replaces-unknown` | A `replaces` model is not a diagram table, a model in the branch comparison (such as a removed one, matched by file name or alias), a model or seed at the comparison baseline, or a project model that lineage found in SQL or the manifest. Names match with or without a schema (`analytics.dim_x` and `dim_x`) when the match is unique. | warning on new or edited models, info on unchanged ones |
| `naming-prefix` | The name (without schema) doesn't start with a layer prefix: marts `fct_ dim_ mart_ marts_ rpt_ report_ reports_`; intermediate `int_ inter_ intermediate_`; prep `stg_ prep_ prepared_` | info |
| `undocumented-columns` | Mart columns with no note | info |

Tables without a charter block get none of these findings unless they are new in the branch.

Grain-based rules (`grain-*`, `no-unique-key`, `unique-key-mismatch`, `history-grain`) run only when a charter declares a `grain`, so a DBML export without charters produces no key warnings. Prefixes live in `NAMING_PREFIXES` in `src/dbml_viewer/charter.py`.

## dbt projects

If your DBML is generated from dbt, the table note usually comes from the model `description`. Put the same block in the description so it survives regeneration:

```yaml
models:
  - name: fct_orders_daily
    description: |
      Daily order rollup per customer for revenue reporting.
      ---
      grain: [order_date, customer_id]
      purpose: Single source for daily revenue by customer.
      owner: analytics-eng
      replaces: []
      ---
```

dbt `meta` and exposures are not read. When a local dbt manifest is present, its `depends_on` adds lineage edges; if they differ from a charter's `depends_on`, the difference is reported as a `declared-upstream-mismatch` finding.
