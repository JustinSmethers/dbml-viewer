import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbml_viewer.layers import LAYERS, classify, layer_of
from dbml_viewer.parser import parse_dbml


class LayerParityTests(unittest.TestCase):
    def test_example_layers(self):
        layers = classify(parse_dbml(ROOT / "examples/layered.dbml"))
        self.assertEqual(layers["source_customers"], "source")
        self.assertEqual(layers["seed_order_status"], "seed")
        self.assertEqual(layers["raw_products"], "source")
        self.assertEqual(layers["stg_order_items"], "prep")
        self.assertEqual(layers["int_customer_orders"], "intermediate")
        self.assertEqual(layers["bridge_customer_products"], "bridge")
        self.assertEqual(layers["reports_revenue"], "mart")

    def test_inference_from_neighbours_and_roles_inside_groups(self):
        data = {
            "tables": [
                {"name": "customers", "group": "_default"},
                {"name": "stg_orders", "group": "_default"},
                {"name": "orders_fact", "group": "intermediate_layer"},
                {"name": "mart_bridge_x", "group": "mart_layer"},
                {"name": "island", "group": "_default"},
            ],
            "refs": [{"from_table": "stg_orders", "to_table": "customers", "from_column": "a", "to_column": "b", "type": ">"}],
        }
        layers = classify(data)
        self.assertEqual(layers["customers"], "prep")
        self.assertEqual(layers["orders_fact"], "intermediate")  # group beats table name
        self.assertEqual(layers["mart_bridge_x"], "bridge")  # bridge role beats group
        self.assertIsNone(layers["island"])

    def test_schema_qualified_names_classify_the_bare_name_first(self):
        cases = {"analytics.stg_orders": "prep", "analytics.int_orders": "intermediate",
                 "reporting.source_orders": "source", "staging.dim_customers": "mart",
                 "analytics.customers": "mart", "staging.customers": "prep", "warehouse.customers": None}
        data = {"tables": [{"name": name, "group": "_default"} for name in cases], "refs": []}
        self.assertEqual(classify(data), cases)
        self.assertEqual(layer_of("analytics.stg_orders", "mart_layer"), "mart")  # group still beats the name
        self.assertEqual(layer_of("analytics.bridge_x", "mart_layer"), "bridge")

    @unittest.skipUnless(shutil.which("node"), "node is required for the parity check")
    def test_matches_browser_classification(self):
        data = parse_dbml(ROOT / "examples/layered.dbml")
        data["tables"].append({"name": "orphan_rollup", "group": "_default", "columns": []})
        data["refs"].append({"from_table": "orphan_rollup", "to_table": "marts_customers", "from_column": "x", "to_column": "customer_id", "type": ">"})
        for name in ("analytics.stg_orders", "analytics.customers", "staging.dim_customers", "warehouse.customers"):
            data["tables"].append({"name": name, "group": "_default", "columns": []})
        script = (
            "const layout = require(process.argv[1]);"
            "const model = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            "process.stdout.write(JSON.stringify(layout.stages(model)));"
        )
        result = subprocess.run(
            ["node", "-e", script, str(ROOT / "src/dbml_viewer/templates/layout.js")],
            input=json.dumps(data), capture_output=True, text=True, check=True,
        )
        browser = {name: (LAYERS[stage] if stage is not None else None) for name, stage in json.loads(result.stdout).items()}
        self.assertEqual(classify(data), browser)


if __name__ == "__main__":
    unittest.main()
