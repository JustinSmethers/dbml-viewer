import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dbml_viewer import charter
from dbml_viewer.layers import classify
from dbml_viewer.parser import parse_dbml


def table(name, note="", columns=None, indexes=None):
    cols = []
    for spec in columns or ["id pk"]:
        col_name, *flags = spec.split()
        cols.append({"name": col_name, "pk": "pk" in flags, "unique": "unique" in flags,
                     "note": "documented" if "doc" in flags else ""})
    prose, parsed, error = charter.extract(note)
    return {"name": name, "note": note, "prose": prose, "charter": parsed,
            "charter_error": error, "columns": cols, "indexes": indexes or []}


def block(yaml_text):
    return "Prose.\n---\n" + yaml_text.strip("\n") + "\n---"


def run(*tables, layers=None, comparison=None):
    data = {"tables": list(tables)}
    layers = layers or {t["name"]: "intermediate" for t in tables}
    return charter.findings(data, layers, comparison)


def rules(found, name, rule=None):
    return [f for f in found[name] if rule is None or f["rule"] == rule]


class ExtractTests(unittest.TestCase):
    def test_block_splits_prose_and_yaml(self):
        prose, parsed, error = charter.extract("Daily rollup.\n---\ngrain: [a, b]\nowner: ae\n---\nMore prose.")
        self.assertEqual(prose, "Daily rollup.\nMore prose.")
        self.assertEqual(parsed, {"grain": ["a", "b"], "owner": "ae"})
        self.assertIsNone(error)

    def test_no_block_is_plain_prose(self):
        self.assertEqual(charter.extract("Just a note."), ("Just a note.", None, None))
        self.assertEqual(charter.extract(""), ("", None, None))

    def test_lone_horizontal_rule_is_prose(self):
        self.assertEqual(charter.extract("Above\n---\nBelow, with words."), ("Above\n---\nBelow, with words.", None, None))

    def test_unclosed_block_is_prose(self):
        note = "Prose\n---\ngrain: [a]"
        self.assertEqual(charter.extract(note), (note, None, None))

    def test_invalid_yaml_keeps_whole_note_as_prose(self):
        note = "Prose\n---\ngrain: [a\nowner: x\n---"
        prose, parsed, error = charter.extract(note)
        self.assertEqual((prose, parsed), (note, None))
        self.assertIn("could not be parsed", error)

    def test_non_mapping_block_is_prose(self):
        for note in ("---\n- a\n- b\n---", "Intro\n---\nA paragraph between two markdown rules.\n---\nOutro",
                     "---\nDaily rollup: one row per customer.\n---", "---\nWhy? Because [it, works\n---"):
            self.assertEqual(charter.extract(note), (note, None, None), note)

    def test_charter_after_a_prose_section(self):
        prose, parsed, error = charter.extract("Intro\n---\nSome prose.\n---\ngrain: [id]\n---")
        self.assertEqual((parsed, error), ({"grain": ["id"]}, None))
        self.assertEqual(prose, "Intro\n---\nSome prose.")

    def test_recursive_anchor_is_a_parse_error_not_a_crash(self):
        note = "---\ngrain: &a [*a]\n---"
        prose, parsed, error = charter.extract(note)
        self.assertEqual((prose, parsed), (note, None))
        self.assertIn("could not be parsed", error)

    def test_name_lists_keep_scalar_items_only(self):
        _, parsed, error = charter.extract(block("grain: [id, {a: 1}, [b], 7]\npii: [{x: y}]\n"
                                                 "replaces: [[old]]\ndepends_on: [up, null]\n"
                                                 "materialization:\n  unique_key: [id, {k: v}]"))
        self.assertIsNone(error)
        self.assertEqual(parsed["grain"], ["id", "7"])
        self.assertEqual((parsed["pii"], parsed["replaces"], parsed["depends_on"]), ([], [], ["up"]))
        self.assertEqual(parsed["materialization"]["unique_key"], ["id"])
        t = {"name": "int_x", "note": "", "charter": parsed, "charter_error": None, "indexes": [],
             "columns": [{"name": "id", "pk": True}]}
        found = run(t)  # no TypeError from unhashable items
        self.assertEqual({f["rule"] for f in found["int_x"]}, {"required-key-missing", "grain-column-unknown", "unique-key-mismatch"})

    def test_scalars_are_coerced_to_lists(self):
        _, parsed, _ = charter.extract(block("""
grain: customer_id
replaces: rpt_old
depends_on: a, b
pii: email
consumers: Finance
questions: Who buys, and when?
validation:
  tests: unique grain
materialization: incremental
metrics: [revenue]
"""))
        self.assertEqual(parsed["grain"], ["customer_id"])
        self.assertEqual(parsed["replaces"], ["rpt_old"])
        self.assertEqual(parsed["depends_on"], ["a", "b"])
        self.assertEqual(parsed["pii"], ["email"])
        self.assertEqual(parsed["consumers"], ["Finance"])
        self.assertEqual(parsed["questions"], ["Who buys, and when?"])
        self.assertEqual(parsed["validation"]["tests"], ["unique grain"])
        self.assertEqual(parsed["materialization"], {"type": "incremental"})
        self.assertEqual(parsed["metrics"], [{"name": "revenue"}])

    def test_explicit_empty_replaces_is_kept(self):
        _, parsed, _ = charter.extract(block("replaces: []"))
        self.assertEqual(parsed["replaces"], [])
        _, parsed, _ = charter.extract(block("owner: x"))
        self.assertNotIn("replaces", parsed)

    def test_dates_are_json_safe(self):
        _, parsed, _ = charter.extract(block("reviewed: 2026-10-01"))
        self.assertEqual(parsed["reviewed"], "2026-10-01")


class CompletenessTests(unittest.TestCase):
    def test_required_keys_by_layer(self):
        empty = table("t")
        mart_keys = ["grain", "purpose", "consumers", "owner", "replaces"]
        self.assertEqual(charter.completeness(empty, "mart"), (mart_keys, mart_keys))
        self.assertEqual(charter.completeness(empty, "bridge")[0], mart_keys)
        self.assertEqual(charter.completeness(empty, "intermediate"), (["grain", "purpose"], ["grain", "purpose"]))
        self.assertEqual(charter.completeness(empty, "prep"), (["grain"], ["grain"]))
        for layer in ("source", "seed", None):
            self.assertEqual(charter.completeness(empty, layer), ([], []))

    def test_empty_replaces_counts_as_present(self):
        t = table("fct_x", block("grain: [id]\npurpose: p\nconsumers: [c]\nowner: o\nreplaces: []"))
        self.assertEqual(charter.completeness(t, "mart")[1], [])
        t = table("fct_x", block("grain: []\npurpose: ''"))
        self.assertEqual(charter.completeness(t, "intermediate")[1], ["grain", "purpose"])


class RuleTests(unittest.TestCase):
    def test_invalid_yaml(self):
        found = run(table("int_x", "---\ngrain: [a\n---"))
        self.assertEqual([f["severity"] for f in rules(found, "int_x", "invalid-yaml")], ["error"])

    def test_unknown_key_warns_with_suggestion(self):
        found = run(table("int_x", block("grain: [id]\npurpose: p\nonwer: me")))
        [finding] = rules(found, "int_x", "unknown-key")
        self.assertEqual(finding["severity"], "warning")
        self.assertIn("onwer", finding["message"])
        self.assertIn("owner", finding["message"])

    def test_required_key_missing_is_error_for_new_and_warning_for_existing(self):
        t = table("fct_x", block("grain: [id]\npurpose: p"))
        layers = {"fct_x": "mart"}
        [existing] = rules(run(t, layers=layers), "fct_x", "required-key-missing")
        self.assertEqual(existing["severity"], "warning")
        self.assertIn("consumers, owner, replaces", existing["message"])
        modified = {"tables": {"fct_x": {"status": "modified"}}}
        self.assertEqual(rules(run(t, layers=layers, comparison=modified), "fct_x", "required-key-missing")[0]["severity"], "warning")
        new = {"tables": {"fct_x": {"status": "new"}}}
        self.assertEqual(rules(run(t, layers=layers, comparison=new), "fct_x", "required-key-missing")[0]["severity"], "error")

    def test_tables_without_a_block_get_no_findings_unless_new(self):
        plain = table("customers", "Plain prose.", ["id", "name"])
        self.assertEqual(run(plain, layers={"customers": "mart"})["customers"], [])
        new = run(plain, layers={"customers": "mart"}, comparison={"tables": {"customers": {"status": "new"}}})
        self.assertEqual({f["rule"]: f["severity"] for f in new["customers"]},
                         {"required-key-missing": "error", "naming-prefix": "info", "undocumented-columns": "info"})

    def test_invalid_yaml_replaces_required_key_missing(self):
        found = run(table("customers", "---\ngrain: [a\n---"), layers={"customers": "mart"})
        self.assertEqual([f["rule"] for f in found["customers"]], ["invalid-yaml", "naming-prefix", "undocumented-columns"])

    def test_grain_column_unknown(self):
        found = run(table("int_x", block("grain: [id, line_number]")))
        [finding] = rules(found, "int_x", "grain-column-unknown")
        self.assertEqual(finding["severity"], "error")
        self.assertIn("line_number", finding["message"])

    def test_grain_not_unique(self):
        t = table("int_x", block("grain: [order_id]"), ["customer_id pk", "order_id"])
        self.assertEqual(rules(run(t), "int_x", "grain-not-unique")[0]["severity"], "warning")

    def test_grain_matches_composite_unique_index_or_unique_column(self):
        t = table("int_x", block("grain: [customer_id, order_date]"), ["order_date", "customer_id", "id pk"],
                  ["(order_date, customer_id) [unique]"])
        self.assertEqual(rules(run(t), "int_x", "grain-not-unique"), [])
        t = table("int_x", block("grain: [code]"), ["id pk", "code unique"])
        self.assertEqual(rules(run(t), "int_x", "grain-not-unique"), [])
        t = table("int_x", block("grain: [a, b]"), ["a", "b"], ["(a, b) [pk]"])
        self.assertEqual(rules(run(t), "int_x", "grain-not-unique") + rules(run(t), "int_x", "no-unique-key"), [])

    def test_no_unique_key_skips_sources_and_seeds(self):
        t = table("bridge_x", block("grain: [a, b]"), ["a", "b"])
        self.assertEqual(rules(run(t, layers={"bridge_x": "bridge"}), "bridge_x", "no-unique-key")[0]["severity"], "warning")
        self.assertEqual(rules(run(t, layers={"bridge_x": "seed"}), "bridge_x", "no-unique-key"), [])
        self.assertEqual(rules(run(table("int_y", "", ["a"])), "int_y", "no-unique-key"), [])

    def test_unique_key_mismatch(self):
        t = table("int_x", block("grain: [id]\nmaterialization:\n  type: incremental\n  unique_key: [id, day]"))
        self.assertEqual(rules(run(t), "int_x", "unique-key-mismatch")[0]["severity"], "error")
        t = table("int_x", block("grain: [id]\nmaterialization:\n  unique_key: id"))
        self.assertEqual(rules(run(t), "int_x", "unique-key-mismatch"), [])

    def test_history_grain(self):
        t = table("dim_x", block("grain: [id]\nhistory: scd2"))
        self.assertEqual(rules(run(t), "dim_x", "history-grain")[0]["severity"], "warning")
        t = table("dim_x", block("grain: [id, valid_from]\nhistory: snapshot"), ["id", "valid_from"], ["(id, valid_from) [unique]"])
        self.assertEqual(rules(run(t), "dim_x", "history-grain"), [])
        t = table("dim_x", block("grain: [id]\nhistory: current"))
        self.assertEqual(rules(run(t), "dim_x", "history-grain"), [])

    def test_pii_column_unknown(self):
        found = run(table("int_x", block("grain: [id]\npii: [email]")))
        [finding] = rules(found, "int_x", "pii-column-unknown")
        self.assertEqual(finding["severity"], "error")
        self.assertIn("email", finding["message"])

    def test_metric_duplicate_warns_on_each_model(self):
        found = run(table("a", block("metrics:\n  - name: revenue\n    expression: sum(x)")),
                    table("b", block("metrics: [{name: revenue}]")),
                    table("c", block("metrics: [{name: orders}]")))
        self.assertEqual(len(rules(found, "a", "metric-duplicate")), 1)
        self.assertIn("b", rules(found, "a", "metric-duplicate")[0]["message"])
        self.assertEqual(rules(found, "b", "metric-duplicate")[0]["severity"], "warning")
        self.assertEqual(rules(found, "c", "metric-duplicate"), [])

    def test_replaces_unknown_uses_diagram_and_comparison(self):
        t = table("fct_new", block("replaces: [rpt_old, fct_other]"))
        found = run(t, table("fct_other"))
        [finding] = rules(found, "fct_new", "replaces-unknown")
        self.assertEqual(finding["severity"], "warning")
        self.assertIn("rpt_old", finding["message"])
        removed = {"tables": {"rpt_old": {"status": "removed"}}}
        self.assertEqual(rules(run(t, table("fct_other"), comparison=removed), "fct_new", "replaces-unknown"), [])

    def test_replaces_accepts_baseline_inventory_and_removed_sources(self):
        t = table("fct_new", block("replaces: [rpt_churn_legacy, old_daily, nowhere]"))
        changes = {"baseline_models": ["rpt_churn_legacy"], "tables": {
            "fct_new": {"status": "new"},
            "models/marts/old_daily.sql": {"status": "removed", "unmapped": True}}}
        unknown = rules(run(t, comparison=changes), "fct_new", "replaces-unknown")
        self.assertEqual([(f["message"].split()[2].rstrip(","), f["severity"]) for f in unknown], [("nowhere", "warning")])
        for status, severity in (("unchanged", "info"), ("modified", "warning")):
            changes["tables"]["fct_new"]["status"] = status
            [finding] = rules(run(t, comparison=changes), "fct_new", "replaces-unknown")
            self.assertEqual(finding["severity"], severity)

    def test_replaces_resolves_schema_qualification_and_project_models(self):
        t = table("fct_new", block("replaces: [analytics.dim_x, dim_y, legacy_model, nowhere]"))
        found = charter.findings({"tables": [t, table("dim_x"), table("analytics.dim_y")]},
                                 {"fct_new": "intermediate"}, None, {"legacy_model"})
        unknown = rules(found, "fct_new", "replaces-unknown")
        self.assertEqual([f["message"].split()[2].rstrip(",") for f in unknown], ["nowhere"])
        self.assertEqual(charter.resolve("analytics.dim_x", {"dim_x"}), "dim_x")
        self.assertEqual(charter.resolve("dim_x", {"analytics.dim_x"}), "analytics.dim_x")
        self.assertIsNone(charter.resolve("dim_x", {"a.dim_x", "b.dim_x"}))

    def test_naming_prefix(self):
        found = run(table("customers", block("owner: x")), table("analytics.fct_orders", block("owner: x")),
                    table("orders_clean", block("owner: x")),
                    layers={"customers": "mart", "analytics.fct_orders": "mart", "orders_clean": "source"})
        [finding] = rules(found, "customers", "naming-prefix")
        self.assertEqual(finding["severity"], "info")
        self.assertEqual(rules(found, "analytics.fct_orders", "naming-prefix"), [])
        self.assertEqual(rules(found, "orders_clean", "naming-prefix"), [])

    def test_undocumented_columns_on_marts_only(self):
        t = table("fct_x", block("owner: x"), ["id pk doc", "a", "b", "c", "d", "e", "f", "g"])
        [finding] = rules(run(t, layers={"fct_x": "mart"}), "fct_x", "undocumented-columns")
        self.assertEqual(finding["severity"], "info")
        self.assertIn("a, b, c, d, e and 2 more", finding["message"])
        self.assertEqual(rules(run(t, layers={"fct_x": "intermediate"}), "fct_x", "undocumented-columns"), [])

    def test_grain_rules_need_a_charter(self):
        found = run(table("int_x", "Plain prose.", ["a"]))
        grain_rules = {"grain-column-unknown", "grain-not-unique", "no-unique-key", "unique-key-mismatch", "history-grain"}
        self.assertFalse(grain_rules & {f["rule"] for f in found["int_x"]})


class ExampleTests(unittest.TestCase):
    def test_example_triggers_every_severity(self):
        data = parse_dbml(ROOT / "examples/layered.dbml")
        found = charter.findings(data, classify(data))
        severities = {f["severity"] for fs in found.values() for f in fs}
        self.assertEqual(severities, {"error", "warning", "info"})
        self.assertTrue(any(t["charter"] is None and t["charter_error"] is None for t in data["tables"]))
        for f in (f for fs in found.values() for f in fs):
            self.assertEqual(set(f), {"rule", "severity", "message"})


if __name__ == "__main__":
    unittest.main()
