import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbml_viewer import charter, review


def table(name, charter_data=None, columns=("id",), group="_default"):
    return {"name": name, "note": "", "prose": "", "charter": charter_data, "charter_error": None, "group": group,
            "indexes": [], "columns": [{"name": column} for column in columns]}


def ref(from_table, to_table, kind=">"):
    return {"from_table": from_table, "from_column": "id", "to_table": to_table, "to_column": "id", "type": kind}


def comparison(**statuses):
    return {"tables": {name: {"status": status, "details": []} for name, status in statuses.items()}}


class ReviewTests(unittest.TestCase):
    def setUp(self):
        # Charter rules are another module's contract; isolate review assembly from them.
        patches = [mock.patch.object(charter, "findings", side_effect=lambda data, layers, comparison=None, known_models=None: {
                       t["name"]: [] for t in data["tables"]}),
                   mock.patch.object(charter, "completeness", return_value=([], []))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def build(self, data, **kwargs):
        review.build(data, **kwargs)
        return data["review"]

    def test_sets_all_keys_and_merges_findings_by_severity(self):
        data = {"tables": [table("source_customers"), table("fct_orders", {"depends_on": ["source_customers"], "grain": ["id"]})],
                "refs": []}
        charter.findings.side_effect = lambda data, layers, comparison=None, known_models=None: {
            "source_customers": [], "fct_orders": [{"rule": "x-info", "severity": "info", "message": "i"},
                                                   {"rule": "x-error", "severity": "error", "message": "e"}]}
        charter.completeness.return_value = (["grain", "owner"], ["owner"])
        result = self.build(data)
        self.assertEqual(data["layers"], {"source_customers": "source", "fct_orders": "mart"})
        self.assertEqual(data["lineage"]["edges"], [{"from": "source_customers", "to": "fct_orders", "source": "declared"}])
        entry = result["tables"]["fct_orders"]
        self.assertEqual([item["rule"] for item in entry["findings"]], ["x-error", "layer-violation", "x-info"])
        self.assertEqual((entry["required"], entry["missing"]), (["grain", "owner"], ["owner"]))
        self.assertEqual(result["counts"], {"error": 1, "warning": 1, "info": 1})
        self.assertEqual(result["walkthrough"], [])
        self.assertEqual(result["tables"]["source_customers"]["overlap"], [])

    def test_replaced_by_index_and_overlap_ranking(self):
        data = {"tables": [
            table("stg_orders"), table("int_orders"), table("int_customers"),
            table("fct_orders_new", {"replaces": ["rpt_orders_legacy"], "grain": ["order_date", "customer_id"],
                                     "depends_on": ["int_orders", "int_customers"]},
                  columns=("order_date", "customer_id", "gross_revenue", "id", "created_at")),
            table("fct_orders_daily", {"grain": ["customer_id", "order_date"], "depends_on": ["int_orders", "int_customers"]},
                  columns=("order_date", "id", "created_at")),
            table("fct_customers", {"grain": ["customer_id"], "depends_on": ["int_customers"]},
                  columns=("customer_id", "gross_revenue", "order_date", "region", "updated_at")),
            table("fct_unrelated", {"grain": ["x"]}, columns=("x",)),
            table("fct_other_new", {"depends_on": ["int_orders"]}),
            table("int_like_mart", {"depends_on": ["int_orders"]}),
        ], "refs": []}
        result = self.build(data, comparison=comparison(fct_orders_new="new", fct_other_new="new", rpt_orders_legacy="removed"))
        overlap = result["tables"]["fct_orders_new"]["overlap"]
        # Replaced models are linked separately, never ranked as extension candidates.
        self.assertEqual([(item["model"], item["score"]) for item in overlap],
                         [("fct_orders_daily", 8), ("fct_customers", 6)])
        self.assertEqual(overlap[0]["reasons"], [
            "Shares upstream int_customers, int_orders (+4)",
            "Same grain (customer_id, order_date) (+3)",
            "Supporting evidence only: 1 shared column name(s): order_date (+1)",
        ])
        self.assertIn("Compatible grain", overlap[1]["reasons"][1])
        self.assertEqual(overlap[1]["reasons"][-1], "Supporting evidence only: 3 shared column name(s): customer_id, gross_revenue, order_date (+3)")
        self.assertEqual(result["tables"]["rpt_orders_legacy"]["replaced_by"], ["fct_orders_new"])
        self.assertEqual(result["tables"]["fct_orders_new"]["replaces"], ["rpt_orders_legacy"])
        self.assertEqual(result["tables"]["fct_orders_daily"]["overlap"], [])  # existing marts get no overlap
        self.assertEqual(result["tables"]["int_like_mart"]["overlap"], [])
        self.assertNotIn("fct_other_new", [item["model"] for item in overlap])  # only existing marts compete

    def test_overlap_excludes_replaced_and_breaks_ties_by_grain_then_upstreams(self):
        data = {"tables": [
            table("int_a"), table("int_b"), table("int_c"),
            table("fct_new", {"grain": ["k"], "replaces": ["fct_replaced"], "depends_on": ["int_a", "int_b", "int_c"]}),
            table("fct_replaced", {"grain": ["k"], "depends_on": ["int_a", "int_b", "int_c"]}),
            table("fct_same_grain", {"grain": ["k"], "depends_on": ["int_a"]}),                         # 2 + 3 = 5
            table("fct_upstreams", {"grain": ["z"], "depends_on": ["int_a", "int_b"]}, columns=("x",)),  # 4 + 1 = 5
            table("fct_one", {"grain": ["z"], "depends_on": ["int_c"]}, columns=("x", "y", "w")),        # 2 + 3 = 5
            table("bridge_b", {"grain": ["z"], "depends_on": ["int_b"]}, columns=("x", "y", "w")),       # 2 + 3 = 5
            table("fct_removed", {"grain": ["k"], "depends_on": ["int_a"]}),
            table("int_like", {"grain": ["k"], "depends_on": ["int_a"]}),
        ], "refs": []}
        data["tables"][3]["columns"] = [{"name": c} for c in ("k", "x", "y", "w")]
        result = self.build(data, comparison=comparison(fct_new="new", fct_removed="removed"))
        overlap = [item["model"] for item in result["tables"]["fct_new"]["overlap"]]
        self.assertEqual(overlap, ["fct_same_grain", "fct_upstreams", "bridge_b"])

    def test_overlap_candidates_are_the_same_kind_and_duplicated_metrics_rank_first(self):
        data = {"tables": [
            table("int_orders"),
            table("fct_revenue_new", {"grain": ["k"], "depends_on": ["int_orders"],
                                      "metrics": [{"name": "net_revenue"}, {"name": "orders"}]}, columns=("k",)),
            table("dim_revenue", {"grain": ["k"], "depends_on": ["int_orders"]}, columns=("k",)),      # a dimension: never
            table("fct_same", {"grain": ["k"], "depends_on": ["int_orders"]}, columns=("k",)),         # 2 + 3 + 1 = 6
            table("rpt_finance", {"grain": ["z"], "metrics": [{"name": "Net_Revenue"}]}, columns=("z",)),  # 3
            table("dim_customers_new", {"grain": ["customer_id"], "depends_on": ["int_orders"]}, columns=("customer_id",)),
            table("dim_customers", {"grain": ["customer_id"]}, columns=("customer_id",)),
            table("dimension_accounts", {"depends_on": ["int_orders"]}),
        ], "refs": []}
        result = self.build(data, comparison=comparison(fct_revenue_new="new", dim_customers_new="new"))
        overlap = result["tables"]["fct_revenue_new"]["overlap"]
        self.assertEqual([(item["model"], item["score"]) for item in overlap], [("fct_same", 6), ("rpt_finance", 3)])
        self.assertEqual(overlap[1]["reasons"], ["Owns the duplicated metric `net_revenue` (+3)"])
        dims = result["tables"]["dim_customers_new"]["overlap"]
        self.assertEqual([item["model"] for item in dims], ["dim_customers", "dim_revenue", "dimension_accounts"])

    def test_removed_models_get_a_layer_from_their_baseline(self):
        data = {"tables": [table("fct_new", {"replaces": ["analytics.legacy_report"]})], "refs": []}
        changes = comparison(fct_new="new", **{"analytics.legacy_report": "removed", "old_thing": "removed"})
        changes["tables"]["analytics.legacy_report"]["baseline"] = {"columns": [], "prose": "", "group": "reports"}
        result = self.build(data, comparison=changes)
        self.assertEqual(result["tables"]["analytics.legacy_report"]["layer"], "mart")
        self.assertEqual(result["tables"]["analytics.legacy_report"]["replaced_by"], ["fct_new"])
        self.assertEqual(result["tables"]["fct_new"]["replaces"], ["analytics.legacy_report"])
        self.assertIsNone(result["tables"]["old_thing"]["layer"])
        self.assertEqual(result["tables"]["old_thing"]["findings"], [])

    def test_replaced_by_resolves_diagram_names(self):
        data = {"tables": [table("rpt_legacy"), table("FCT_New", {"replaces": ["RPT_LEGACY"]})], "refs": []}
        result = self.build(data)
        self.assertEqual(result["tables"]["FCT_New"]["replaces"], ["rpt_legacy"])
        self.assertEqual(result["tables"]["rpt_legacy"]["replaced_by"], ["FCT_New"])

    def test_walkthrough_in_lineage_order_with_replaced_models(self):
        data = {"tables": [
            table("fct_segments", {"depends_on": ["int_scores"], "replaces": ["legacy_report", "rpt_old"]}),
            table("int_scores", {"depends_on": ["stg_customers"]}),
            table("stg_customers"),
            table("rpt_old"),
            table("marts_customers", {"depends_on": ["stg_customers"]}),
        ], "refs": []}
        result = self.build(data, comparison=comparison(
            fct_segments="new", stg_customers="modified", legacy_report="removed", marts_customers="unchanged",
            rpt_old="unchanged", orphan_removed="removed"))
        self.assertEqual(result["walkthrough"], ["stg_customers", "legacy_report", "rpt_old", "fct_segments", "orphan_removed"])

    def test_walkthrough_keeps_cardless_changes(self):
        data = {"tables": [table("stg_a"), table("fct_keep", {"replaces": ["rpt_gone"]}), table("fct_b", {"depends_on": ["stg_a"]})],
                "refs": []}
        changes = comparison(stg_a="modified", fct_b="new", rpt_gone="removed", zz_removed="removed",
                             **{"models/x.sql": "modified", "models/y.sql": "removed"})
        for path in ("models/x.sql", "models/y.sql"):
            changes["tables"][path]["unmapped"] = True
        result = self.build(data, comparison=changes)
        # fct_keep is unchanged, so its replaced model has no step to precede and goes at the end.
        # Unmapped, path-keyed sources are listed from the comparison, never as steps.
        self.assertEqual(result["walkthrough"], ["stg_a", "fct_b", "rpt_gone", "zz_removed"])
        self.assertNotIn("models/y.sql", result["tables"])
        data = {"tables": [table("stg_a"), table("fct_keep", {"replaces": ["rpt_gone"]})], "refs": []}
        result = self.build(data, comparison=comparison(fct_keep="modified", stg_a="modified", zz_removed="removed", rpt_gone="removed"))
        self.assertEqual(result["walkthrough"], ["stg_a", "rpt_gone", "fct_keep", "zz_removed"])

    def test_bus_matrix_uses_declared_refs_only(self):
        data = {"tables": [
            table("dim_customers", columns=("customer_id",)), table("dim_dates"), table("dimension_products"),
            table("stg_dim_raw"), table("stg_orders"),
            table("fct_orders", {"depends_on": ["dim_customers"]}, columns=("customer_id", "date_id")),
            table("fct_returns", columns=("customer_id",)),  # shares a column name, but no ref
            table("fct_sessions"),
        ], "refs": [ref("fct_orders", "dim_customers"), ref("fct_orders", "dim_dates"), ref("dim_dates", "dim_customers"),
                    ref("dimension_products", "fct_sessions", "-"), ref("fct_orders", "fct_sessions"),
                    ref("stg_orders", "dim_customers")]}  # a prep model is not a fact
        matrix = self.build(data)["bus_matrix"]
        self.assertEqual(matrix["dims"], ["dim_customers", "dim_dates", "dimension_products"])
        self.assertEqual(matrix["facts"], ["fct_orders", "fct_sessions"])
        self.assertEqual(matrix["cells"], {"fct_orders": ["dim_customers", "dim_dates"], "fct_sessions": ["dimension_products"]})

    def test_sql_lineage_from_project_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            (root / "models/fct_orders.sql").write_text("select * from {{ source('shop', 'orders') }}")
            data = {"tables": [table("fct_orders")], "refs": []}
            self.build(data, root=root, model_dirs=["models"], manifests=[])
        self.assertEqual(data["lineage"]["stubs"], ["shop.orders"])
        finding = data["review"]["tables"]["fct_orders"]["findings"][0]
        self.assertEqual((finding["rule"], finding.get("inferred")), ("layer-violation", True))

    def test_cli_summary_pluralises_and_scopes_findings(self):
        from dbml_viewer.main import review_summary
        finding = {"rule": "r", "severity": "error", "message": "m"}
        data = {"review": {"tables": {"a": {"findings": [finding, dict(finding, severity="info")]}, "b": {"findings": []}},
                           "counts": {"error": 1, "warning": 0, "info": 1}}}
        self.assertEqual(review_summary(data), "Review: 2 findings on 1 model (1 error, 0 warnings, 1 info)")
        data["comparison"] = comparison(a="new", b="modified")
        self.assertEqual(review_summary(data), "Review: 2 findings on 1 model (1 error, 0 warnings, 1 info); 2 on changed models")

    def test_failure_leaves_no_partial_review(self):
        data = {"tables": [table("a")], "refs": []}
        charter.findings.side_effect = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            review.build(data)
        self.assertFalse({"layers", "lineage", "review"} & data.keys())


if __name__ == "__main__":
    unittest.main()
