"""Build a reviewable branch comparison using a disposable sample Git repo.

The branch proposes `fct_customer_segments`, a new mart with a charter that
replaces `legacy_report`. Existing models have SQL with literal `ref()` and
`source()` calls, so the review shows SQL lineage, declared lineage for the
proposal, impact of the edited `marts_customers`, layer violations, a
grain-compatibility check, and overlap with existing marts. The legacy model's
SQL is deleted with its card, so it is one removed entry with a source diff.

Run: uv run python examples/preview_changes.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbml_viewer import review
from dbml_viewer.main import review_summary
from dbml_viewer.layers import classify
from dbml_viewer.parser import parse_dbml
from dbml_viewer.renderer import render_html
from dbml_viewer.source_compare import compare_sources

# Unchanged in the branch. Its SQL reads a mart (a backward layer edge) and
# omits a declared upstream, so both lineage findings appear.
SCORES = """
Table int_customer_scores {
  customer_id int
  scored_at date
  score decimal
  Note: '''
  Daily propensity scores per customer from the scoring job.
  ---
  grain: [customer_id, scored_at]
  purpose: Score history used to assign lifecycle segments.
  owner: data-science
  depends_on: [int_customer_orders, prepared_orders]
  ---
  '''
}
Ref: int_customer_scores.customer_id > int_customer_orders.customer_id
"""
SCORES_SQL = """select o.customer_id, current_date as scored_at, c.lifetime_value / 100 as score
from {{ ref('int_customer_orders') }} as o
join {{ ref( "marts_customers" ) }} as c using (customer_id)
"""
LEGACY = """
Table legacy_report {
  id int
  customer_id int
  segment varchar
}
"""
# The proposal has no SQL yet; lineage comes from its declared depends_on.
SEGMENTS = """
Table fct_customer_segments {
  customer_id int [pk]
  segment varchar
  score decimal
  Note: '''
  Lifecycle segment and propensity score per customer.
  ---
  grain: [customer_id]
  purpose: One governed customer segmentation for lifecycle marketing.
  questions:
    - How many customers are in each lifecycle segment this week?
  consumers:
    - Lifecycle marketing audience sync
    - Customer health dashboard (Looker)
  owner: analytics-eng
  replaces: [legacy_report]
  depends_on: [marts_customers, int_customer_scores]
  materialization:
    type: table
  validation:
    reconciles_to: legacy_report
    evidence: https://example.com/validation/fct_customer_segments
    tests: [unique customer_id, not_null segment]
  ---
  '''
}
Ref: fct_customer_segments.customer_id > marts_customers.customer_id
"""


def model_sql(data: dict) -> dict[str, str]:
    """Write SQL that follows each model's declared upstreams (or DBML refs when undeclared)."""
    layers = classify(data)
    files = {}
    for table in data["tables"]:
        name = table["name"]
        if layers.get(name) == "seed":
            files[f"seeds/{name}.csv"] = "code,name\n"
            continue
        upstream = (table.get("charter") or {}).get("depends_on")
        if upstream is None:
            upstream = sorted({ref["to_table"] for ref in data["refs"] if ref["from_table"] == name})
        if upstream:
            sources = [f"{{{{ ref('{item}') }}}}" for item in upstream]
        else:
            short = name.removeprefix("source_").removeprefix("raw_")
            sources = [f"{{{{ source('{'crm' if layers.get(name) == 'source' else 'shop'}', '{short}') }}}}"]
        files[f"models/{name}.sql"] = "select *\nfrom " + "\ncross join ".join(sources) + "\n"
    files["models/int_customer_scores.sql"] = SCORES_SQL
    files["models/legacy_report.sql"] = "select * from {{ ref('marts_customers') }}\n"
    return files


def main():
    original = (ROOT / "examples/layered.dbml").read_text()
    original = original.replace("layered_warehouse", "Branch_changes_demo") + SCORES
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "warehouse.dbml"

        def git(*args):
            subprocess.run(["git", "-C", directory, *args], check=True, capture_output=True)

        git("init", "-b", "main")
        git("config", "user.name", "DBML Preview")
        git("config", "user.email", "preview@example.com")
        path.write_text(original + LEGACY)
        for name, text in model_sql(parse_dbml(path)).items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        git("add", ".")
        git("commit", "-m", "Sample baseline")
        git("switch", "-c", "feature/customer-segments")
        current = original.replace("lifetime_value decimal", "lifetime_value decimal(18,2)\n  customer_segment varchar", 1)
        path.write_text(current + SEGMENTS)
        # Design changes precede implementation: model SQL is left as it was, except
        # that the replaced legacy model's SQL is deleted along with its card.
        (root / "models/legacy_report.sql").unlink()
        data = parse_dbml(path)
        data["comparison"] = compare_sources(root, data, dbml_path=path)
        review.build(data, comparison=data["comparison"], root=root,
                     model_dirs=data["comparison"]["model_dirs"], manifests=data["comparison"]["manifests"])
        output = ROOT / "examples/branch-changes.html"
        output.write_text(render_html(data))
        print(f"Preview: {output}")
        counts = data["comparison"]["counts"]
        print(f"Sample changes: {counts['new']} new, {counts['modified']} modified, {counts['removed']} removed")
        modified = [name for name, entry in data["comparison"]["tables"].items() if entry["status"] == "modified"]
        print(f"Modified: {', '.join(modified)}")
        print(f"{review_summary(data)}; {len(data['lineage']['edges'])} lineage edges")
        print(f"Walkthrough: {' -> '.join(data['review']['walkthrough'])}")


if __name__ == "__main__":
    main()
