"""Build a reviewable branch comparison using a disposable sample Git repo.

Run: uv run python examples/preview_changes.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbml_viewer.source_compare import compare_sources
from dbml_viewer.parser import parse_dbml
from dbml_viewer.renderer import render_html


def main():
    original = (ROOT / "examples/layered.dbml").read_text()
    original = original.replace("layered_warehouse", "Branch_changes_demo")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        path = root / "warehouse.dbml"

        def git(*args):
            subprocess.run(["git", "-C", directory, *args], check=True, capture_output=True)

        git("init", "-b", "main")
        git("config", "user.name", "DBML Preview")
        git("config", "user.email", "preview@example.com")
        path.write_text(original + '\nTable legacy_report {\n id int\n}\n')
        (root / "models").mkdir()
        for table in parse_dbml(path)["tables"]:
            (root / "models" / (table["name"] + ".sql")).write_text("select 1 as id\n")
        git("add", ".")
        git("commit", "-m", "Sample baseline")
        git("switch", "-c", "feature/customer-segments")
        current = original.replace("lifetime_value decimal", "lifetime_value decimal(18,2)\n  customer_segment varchar")
        current += '\nTable fct_customer_segments {\n customer_id int [pk]\n segment varchar\n score decimal\n}\nRef: fct_customer_segments.customer_id > marts_customers.customer_id\n'
        path.write_text(current)
        # Design changes precede implementation: model SQL is left as it was.
        data = parse_dbml(path)
        data["comparison"] = compare_sources(root, data, dbml_path=path)
        output = ROOT / "examples/branch-changes.html"
        output.write_text(render_html(data))
        print(f"Preview: {output}")
        print("Sample changes: 1 new model, 1 modified model, 1 removed model")


if __name__ == "__main__":
    main()
