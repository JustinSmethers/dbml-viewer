import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbml_viewer import lineage, review
from dbml_viewer.parser import parse_dbml_text
from dbml_viewer.source_compare import compare_sources


def table(name, charter=None, columns=("id",), group="_default"):
    return {"name": name, "note": "", "prose": "", "charter": charter, "charter_error": None, "group": group,
            "indexes": [], "columns": [{"name": column} for column in columns]}


def model(*tables):
    return {"tables": list(tables), "refs": []}


LAYERS = {"source_customers": "source", "seed_status": "seed", "stg_orders": "prep",
          "int_orders": "intermediate", "fct_orders": "mart", "bridge_orders": "bridge", "dim_customers": "mart"}


class ParseRefsTests(unittest.TestCase):
    def test_jinja_variants_packages_and_sources(self):
        sql = """
        select * from {{ ref('a') }} join {{ref("b")}} join {{ ref( 'c' ) }}
        join {{ ref('pkg', 'd') }} join {{ ref("e", v=2) }} join {{ source('raw', 'orders') }}
        join {{ source( "crm" , "people" ) }} join {{ ref('a') }}
        {# {{ ref('commented') }} #}
        """
        self.assertEqual(lineage.parse_refs(sql), [
            ("ref", "a"), ("ref", "b"), ("ref", "c"), ("ref", "d"), ("ref", "e"),
            ("source", "raw.orders"), ("source", "crm.people"),
        ])

    def test_python_models_and_dynamic_refs(self):
        code = 'def model(dbt, session):\n    a = dbt.ref("orders")\n    b = dbt.source("s", "t")\n    c = dbt.ref(name)\n    obj.ref("x")\n'
        self.assertEqual(lineage.parse_refs(code), [("ref", "orders"), ("source", "s.t")])
        self.assertEqual(lineage.parse_refs("{{ ref(var('m')) }} {{ source('only_one') }}"), [])


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_declared_only_without_root(self):
        data = model(table("stg_orders"), table("fct_orders", {"depends_on": ["stg_orders", "ghost"]}))
        graph = lineage.build(data, LAYERS)
        self.assertEqual(graph["edges"], [
            {"from": "ghost", "to": "fct_orders", "source": "declared"},
            {"from": "stg_orders", "to": "fct_orders", "source": "declared"},
        ])
        self.assertEqual(graph["stubs"], ["ghost"])
        self.assertIn(lineage.SQL_NOTE, graph["notes"])
        self.assertTrue(any("depends_on only" in note for note in graph["notes"]))

    def test_sql_edges_preferred_and_deduplicated_with_stubs(self):
        self.write("models/staging/stg_orders.sql", "select * from {{ source('raw', 'orders') }}")
        self.write("models/marts/fct_orders.sql", "{{ config(alias='FCT_ORDERS') }}\nselect * from {{ ref('stg_orders') }} join {{ ref('stg_orders') }} join {{ ref('pkg', 'calendar') }}")
        self.write("models/other.sql", "select * from {{ ref('fct_orders') }}")  # not in the diagram
        data = model(table("stg_orders"), table("analytics.FCT_ORDERS", {"depends_on": ["stg_orders"]}))
        graph = lineage.build(data, {"stg_orders": "prep", "analytics.FCT_ORDERS": "mart"}, root=self.root, model_dirs=["models"])
        self.assertEqual(graph["edges"], [
            {"from": "calendar", "to": "analytics.FCT_ORDERS", "source": "sql"},
            {"from": "raw.orders", "to": "stg_orders", "source": "sql"},
            {"from": "stg_orders", "to": "analytics.FCT_ORDERS", "source": "sql", "sources": ["sql", "declared"]},
        ])
        self.assertEqual(graph["stubs"], ["calendar", "raw.orders"])
        self.assertEqual(graph["stub_kinds"], {"calendar": "model", "raw.orders": "source"})
        self.assertEqual(graph["order"], ["stg_orders", "analytics.FCT_ORDERS"])
        self.assertTrue(any("Scanned 3 model files" in note for note in graph["notes"]))
        self.assertTrue(any("No dbt manifest" in note for note in graph["notes"]))

    def test_source_short_name_matches_only_input_tables(self):
        self.write("models/int_customers.sql", "select * from {{ source('crm', 'source_customers') }} join {{ source('crm', 'fct_orders') }}")
        data = model(table("source_customers"), table("fct_orders"), table("int_customers"))
        graph = lineage.build(data, LAYERS | {"int_customers": "intermediate"}, root=self.root)
        self.assertEqual({(edge["from"], edge["to"]) for edge in graph["edges"]},
                         {("source_customers", "int_customers"), ("crm.fct_orders", "int_customers")})

    def test_manifest_merge_and_preference(self):
        self.write("models/fct_orders.sql", "select * from {{ ref('stg_orders') }}")
        manifest = {"metadata": {"project_name": "demo"}, "nodes": {
            "model.demo.fct_orders": {"name": "fct_orders", "resource_type": "model", "schema": "analytics",
                                      "depends_on": {"nodes": ["model.demo.stg_orders", "model.demo.int_orders", "source.demo.raw.payments", "macro.demo.x"]}},
            "model.demo.stg_orders": {"name": "stg_orders", "resource_type": "model"},
            "model.demo.int_orders": {"name": "int_orders", "resource_type": "model", "alias": "int_orders_v2"},
        }, "sources": {"source.demo.raw.payments": {"name": "payments", "source_name": "raw", "resource_type": "source"}}}
        self.write("target/manifest.json", json.dumps(manifest))
        data = model(table("stg_orders"), table("fct_orders"))
        graph = lineage.build(data, LAYERS, root=self.root, model_dirs=["models"], manifests=["target/manifest.json"])
        self.assertEqual(graph["edges"], [
            {"from": "int_orders", "to": "fct_orders", "source": "manifest"},  # stubs use model names, as ref() does
            {"from": "raw.payments", "to": "fct_orders", "source": "manifest"},
            {"from": "stg_orders", "to": "fct_orders", "source": "sql", "sources": ["sql", "manifest"]},
        ])
        self.assertTrue(any(note.startswith("Manifest used: target/manifest.json") for note in graph["notes"]))

    def test_missing_and_invalid_manifests_are_ignored_with_notes(self):
        self.write("models/fct_orders.sql", "select * from {{ ref('stg_orders') }}")
        self.write("target/manifest.json", "{not json")
        self.write("bad/manifest.json", json.dumps({"nodes": []}))
        data = model(table("stg_orders"), table("fct_orders"))
        graph = lineage.build(data, LAYERS, root=self.root, manifests=["target/manifest.json", "bad/manifest.json", "missing/manifest.json"])
        self.assertEqual([edge["source"] for edge in graph["edges"]], ["sql"])
        notes = " ".join(graph["notes"])
        self.assertIn("Ignored unreadable manifest target/manifest.json", notes)
        self.assertIn("Ignored unreadable manifest bad/manifest.json: invalid manifest structure", notes)
        self.assertIn("No dbt manifest at missing/manifest.json", notes)

    def test_topological_order_ties_by_layer_then_name_and_cycles(self):
        data = model(table("fct_orders"), table("stg_orders"), table("source_customers"), table("seed_status"),
                     table("int_orders", {"depends_on": ["stg_orders", "seed_status"]}),
                     table("bridge_orders", {"depends_on": ["fct_orders"]}))
        graph = lineage.build(data, LAYERS)
        self.assertEqual(graph["order"], ["source_customers", "seed_status", "stg_orders", "int_orders", "fct_orders", "bridge_orders"])
        cyclic = model(table("a", {"depends_on": ["b"]}), table("b", {"depends_on": ["a"]}), table("c", {"depends_on": ["b"]}))
        graph = lineage.build(cyclic, {})
        self.assertEqual(graph["order"], ["a", "b", "c"])
        self.assertTrue(any("cycle" in note for note in graph["notes"]))

    def test_impact_closure_matches_hand_computed(self):
        #   a -> b -> d -> f
        #   a -> c -> d ; c -> e ; g isolated
        data = model(table("a"), table("b", {"depends_on": ["a"]}), table("c", {"depends_on": ["a"], "consumers": ["Ops board"]}),
                     table("d", {"depends_on": ["b", "c"], "consumers": ["Finance", "Ops board"]}),
                     table("e", {"depends_on": ["c"]}), table("f", {"depends_on": ["d"], "consumers": ["Exec"]}), table("g"))
        graph = lineage.build(data, {})
        expected = {"a": ["b", "c", "d", "e", "f"], "b": ["d", "f"], "c": ["d", "e", "f"], "d": ["f"], "e": [], "f": [], "g": []}
        self.assertEqual({name: sorted(entry["impact"]) for name, entry in graph["tables"].items()}, expected)
        self.assertEqual(graph["tables"]["c"]["upstream"], ["a"])
        self.assertEqual(graph["tables"]["c"]["downstream"], ["d", "e"])
        self.assertEqual(graph["tables"]["c"]["consumers_affected"], ["Ops board", "Finance", "Exec"])
        self.assertEqual(lineage.build(data, {}), graph)  # deterministic


class FindingTests(unittest.TestCase):
    def rules(self, data, graph=None, layers=LAYERS):
        graph = graph or lineage.build(data, layers)
        return {name: [(item["rule"], item["severity"], item.get("inferred", False)) for item in items]
                for name, items in lineage.findings(data, layers, graph).items() if items}

    def test_layer_violations(self):
        data = model(table("source_customers"), table("seed_status"), table("stg_orders"), table("fct_orders"),
                     table("int_orders", {"depends_on": ["source_customers", "fct_orders"]}),
                     table("bridge_orders", {"depends_on": ["seed_status", "fct_orders"]}),
                     table("dim_customers", {"depends_on": ["source_customers"]}))
        self.assertEqual(self.rules(data), {
            "int_orders": [("layer-violation", "warning", False)],  # backwards from mart; skipping prep is fine
            "bridge_orders": [("layer-violation", "warning", False)],
            "dim_customers": [("layer-violation", "warning", False)],
        })

    def test_sql_only_edges_are_inferred_and_source_stubs_count_as_sources(self):
        data = model(table("fct_orders"))
        graph = {"edges": [{"from": "raw.orders", "to": "fct_orders", "source": "sql"}], "stub_kinds": {"raw.orders": "source"}}
        found = lineage.findings(data, LAYERS, graph)["fct_orders"]
        self.assertEqual(found[0]["rule"], "layer-violation")
        self.assertTrue(found[0]["inferred"])
        self.assertIn("reads source `raw.orders` directly", found[0]["message"])

    def test_declared_upstream_mismatch_both_directions(self):
        data = model(table("stg_orders"), table("int_orders"), table("fct_orders", {"depends_on": ["stg_orders", "int_orders"]}))
        graph = {"edges": [
            {"from": "int_orders", "to": "fct_orders", "source": "sql", "sources": ["sql", "declared"]},
            {"from": "stg_orders", "to": "fct_orders", "source": "declared"},
            {"from": "dim_customers", "to": "fct_orders", "source": "manifest"},
        ], "stub_kinds": {"dim_customers": "model"}}
        found = lineage.findings(data, LAYERS, graph)["fct_orders"]
        mismatch = [(item["severity"], item["message"]) for item in found if item["rule"] == "declared-upstream-mismatch"]
        self.assertEqual(mismatch, [
            ("warning", "Declared upstream `stg_orders` is not referenced in SQL or the manifest."),
            ("info", "`dim_customers` is referenced in SQL or the manifest but not declared in depends_on."),
        ])
        # Without a declared depends_on, or without SQL/manifest edges, there is nothing to compare.
        data["tables"][2]["charter"] = {}
        self.assertFalse([item for item in lineage.findings(data, LAYERS, graph)["fct_orders"] if item["rule"] == "declared-upstream-mismatch"])

    def test_grain_compatibility_only_when_both_declared_and_strictly_finer(self):
        data = model(table("int_orders", {"grain": ["customer_id", "order_id"]}),
                     table("stg_orders", {"grain": ["order_id"]}),
                     table("source_customers"),
                     table("fct_orders", {"grain": ["Customer_ID"], "depends_on": ["int_orders", "stg_orders", "source_customers"]},
                           columns=("customer_id", "order_id")),
                     table("bridge_orders", {"grain": ["customer_id", "order_id"], "depends_on": ["int_orders"]},
                           columns=("customer_id", "order_id")))
        found = lineage.findings(data, LAYERS, lineage.build(data, LAYERS))
        grain = [item for item in found["fct_orders"] if item["rule"] == "grain-compatibility"]
        self.assertEqual(len(grain), 1)
        self.assertIn(lineage.GRAIN_MESSAGE, grain[0]["message"])
        self.assertIn("`int_orders`", grain[0]["message"])
        self.assertFalse([item for item in found["bridge_orders"] if item["rule"] == "grain-compatibility"])

    def test_grain_compatibility_when_upstream_grain_is_not_carried(self):
        layers = {"int_subscription_periods": "intermediate", "dim_accounts": "mart", "fct_account_months": "mart"}
        data = model(table("int_subscription_periods", {"grain": ["subscription_id", "period_start"]}),
                     table("dim_accounts", {"grain": ["account_id"]}, columns=("account_id", "name")),
                     table("fct_account_months", {"grain": ["account_id", "month"],
                                                  "depends_on": ["int_subscription_periods", "dim_accounts"]},
                           columns=("account_id", "month", "mrr")))
        found = lineage.findings(data, layers, lineage.build(data, layers))
        grain = [item for item in found["fct_account_months"] if item["rule"] == "grain-compatibility"]
        # dim_accounts is a key lookup join (account_id is a column here), so only the rollup is flagged.
        self.assertEqual([item["message"] for item in grain], [
            "Upstream `int_subscription_periods` grain (subscription_id, period_start) is not carried by "
            "this model's columns: confirm the rollup or join."])
        self.assertEqual(grain[0]["severity"], "warning")

    def test_no_upstream_only_for_new_models(self):
        layers = {"fct_new": "mart", "fct_old": "mart", "seed_status": "seed", "fct_fed": "mart"}
        data = model(table("fct_new"), table("fct_old"), table("seed_status"), table("fct_fed", {"depends_on": ["fct_old"]}))
        graph = lineage.build(data, layers)
        statuses = {"fct_new": "new", "fct_old": "modified", "seed_status": "new", "fct_fed": "new"}
        found = lineage.findings(data, layers, graph, statuses)
        hits = {name: [item for item in items if item["rule"] == "no-upstream"] for name, items in found.items()}
        self.assertEqual(hits["fct_new"], [{"rule": "no-upstream", "severity": "info", "message":
                                            "No upstream known for fct_new. Add depends_on to its charter to place it in lineage."}])
        self.assertEqual([name for name, items in hits.items() if items], ["fct_new"])
        self.assertFalse([item for item in lineage.findings(data, layers, graph)["fct_new"] if item["rule"] == "no-upstream"])

    def test_upstream_unknown_only_for_unknown_declared_names(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "models").mkdir()
        (root / "models/stg_payments.sql").write_text("select 1")
        data = model(table("fct_orders", {"depends_on": ["stg_payments", "nowhere"]}))
        graph = lineage.build(data, LAYERS, root=root)
        found = lineage.findings(data, LAYERS, graph)["fct_orders"]
        self.assertEqual([(item["rule"], item["severity"]) for item in found], [("upstream-unknown", "info")])
        self.assertIn("`nowhere`", found[0]["message"])
        self.assertEqual(graph["stub_kinds"], {"nowhere": "unknown", "stg_payments": "model"})

    def test_declared_names_resolve_across_schema_qualification(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "models").mkdir()
        (root / "models/stg_payments.sql").write_text("select 1")
        data = model(table("stg_orders"), table("analytics.int_orders"),
                     table("fct_orders", {"depends_on": ["staging.stg_orders", "int_orders", "analytics.stg_payments"]}))
        graph = lineage.build(data, LAYERS, root=root)
        self.assertEqual(sorted(edge["from"] for edge in graph["edges"]),
                         ["analytics.int_orders", "analytics.stg_payments", "stg_orders"])
        self.assertEqual(graph["stub_kinds"], {"analytics.stg_payments": "model"})
        self.assertEqual(lineage.findings(data, LAYERS, graph)["fct_orders"], [])


    def test_replaces_accepts_project_models_known_to_lineage(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "models").mkdir()
        (root / "models/fct_new.sql").write_text("select * from {{ ref('legacy_model') }}")
        data = model(table("fct_new", {"replaces": ["legacy_model", "nowhere"]}))
        review.build(data, root=root)
        unknown = [item["message"] for item in data["review"]["tables"]["fct_new"]["findings"]
                   if item["rule"] == "replaces-unknown"]
        self.assertEqual(len(unknown), 1)
        self.assertIn("nowhere", unknown[0])


class ComparisonHandoffTests(unittest.TestCase):
    def test_compare_sources_reports_model_dirs_and_manifests_for_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                subprocess.run(["git", "-C", directory, *args], check=True, capture_output=True)

            git("init", "-b", "main")
            git("config", "user.name", "Test")
            git("config", "user.email", "test@example.com")
            (root / "dbt_project.yml").write_text("name: demo\nmodel-paths: [transform]\ntarget-path: build\n")
            (root / "transform").mkdir()
            (root / "transform/stg_orders.sql").write_text("select 1")
            (root / "transform/fct_orders.sql").write_text("select * from {{ ref('stg_orders') }}")
            git("add", ".")
            git("commit", "-m", "baseline")
            data = parse_dbml_text("Table stg_orders {\n id int\n}\nTable fct_orders {\n id int\n}")
            comparison = compare_sources(root, data, "main")
            self.assertEqual((comparison["model_dirs"], comparison["manifests"]), (["transform"], ["build/manifest.json"]))
            review.build(data, comparison=comparison, root=root,
                         model_dirs=comparison["model_dirs"], manifests=comparison["manifests"])
        self.assertEqual(data["lineage"]["edges"], [{"from": "stg_orders", "to": "fct_orders", "source": "sql"}])
        self.assertIn("No dbt manifest at build/manifest.json", " ".join(data["lineage"]["notes"]))


if __name__ == "__main__":
    unittest.main()
