import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbml_viewer.parser import parse_dbml_text
from dbml_viewer.renderer import render_html
from dbml_viewer.source_compare import compare_sources


class SourceComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.write("dbt_project.yml", "name: demo\nmodel-paths: [models]\n")
        self.write(".gitignore", "*.dbml\ntarget/\nmodels/ignored.sql\n")
        self.write("models/users.sql", "select 1 as id\n")
        self.git("add", ".")
        self.git("commit", "-m", "baseline")
        self.git("switch", "-c", "feature")
        self.data = parse_dbml_text("Table users {\n id int\n}")

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def compare(self, revision=None):
        return compare_sources(self.root, self.data, revision)

    def test_clean_branch_committed_logic_change_and_generated_dbml(self):
        self.write("models/users.sql", "select 2 as id\n")
        self.git("commit", "-am", "logic only")
        self.write("generated.dbml", "Table users {\n id int\n}")
        changes = self.compare()
        self.assertEqual(changes["revision"], "main")
        self.assertEqual(changes["counts"], {"new": 0, "modified": 1, "removed": 0, "unchanged": 0})
        self.assertEqual(changes["tables"]["users"]["details"], ["Modified model source: models/users.sql"])
        self.assertIn("Model and design changes", render_html(dict(self.data, comparison=changes)))

    def test_new_staged_unstaged_and_untracked_models(self):
        self.data = parse_dbml_text("Table users {\n id int\n}\nTable orders {\n id int\n}")
        self.write("models/users.sql", "select 2 as id\n")
        self.git("add", "models/users.sql")
        self.write("models/users.sql", "select 3 as id\n")
        self.write("models/orders.sql", "select 1 as id\n")
        self.write("models/ignored.sql", "select 1 as id\n")
        changes = self.compare()
        self.assertEqual(changes["counts"]["modified"], 1)
        self.assertEqual(changes["tables"]["orders"]["status"], "new")
        self.assertNotIn("models/ignored.sql", changes["unmapped"])

    def test_uses_merge_base_and_ignores_main_only_changes(self):
        base = self.git("rev-parse", "HEAD")
        self.git("switch", "main")
        self.write("models/users.sql", "select 2 as id\n")
        self.git("commit", "-am", "main logic")
        self.git("switch", "feature")
        changes = self.compare()
        self.assertEqual(changes["base"], base[:8])
        self.assertEqual(changes["counts"]["modified"], 0)

    def test_origin_main_fallback_and_explicit_ref(self):
        self.git("update-ref", "refs/remotes/origin/main", "main")
        self.git("branch", "-D", "main")
        self.assertEqual(self.compare()["revision"], "origin/main")
        self.assertEqual(self.compare("HEAD")["revision"], "HEAD")
        with self.assertRaisesRegex(ValueError, "Cannot compare model sources"):
            self.compare("missing")

    def test_refreshes_remote_main_even_when_local_main_is_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run(["git", "init", "--bare", directory], check=True, capture_output=True)
            self.git("remote", "add", "origin", directory)
            old = self.git("rev-parse", "main")
            self.git("switch", "main")
            self.write("models/users.sql", "select 2 as id\n")
            self.git("commit", "-am", "advance remote main")
            fresh = self.git("rev-parse", "main")
            self.git("push", "origin", "main")
            self.git("switch", "feature")
            self.git("merge", "--ff-only", "main")
            self.git("branch", "-f", "main", old)
            for revision in (None, "main", "origin/main"):
                self.git("update-ref", "refs/remotes/origin/main", old)
                changes = self.compare(revision)
                self.assertEqual(changes["revision"], "origin/main")
                self.assertEqual(changes["base"], fresh[:8])
                self.assertEqual(changes["counts"]["modified"], 0)
                self.assertEqual(self.git("rev-parse", "origin/main"), fresh)
                self.assertEqual(self.git("rev-parse", "main"), old)

    def test_failed_fetch_never_uses_stale_local_main(self):
        self.git("remote", "add", "origin", str(self.root / "missing-remote"))
        with self.assertRaisesRegex(ValueError, "refusing to use a stale main baseline"):
            self.compare()
        self.write("generated.dbml", "Table users {\n id int\n}")
        result = self.cli(self.root / "generated.dbml")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("refusing to use a stale main baseline", result.stderr)
        self.assertNotIn('id="branch-toggle"', (self.root / "generated_diagram.html").read_text())
        self.assertNotEqual(self.cli(self.root / "generated.dbml", "--compare", "main").returncode, 0)

    def test_changes_outside_models_are_excluded(self):
        for name in ("macros/users.sql", "tests/users.sql", "analysis/users.sql", "target/users.sql"):
            self.write(name, "select 2 as id\n")
        self.assertEqual(self.compare()["counts"]["modified"], 0)

    def test_custom_paths_python_models_and_spaces(self):
        self.data = parse_dbml_text("Table users {\n id int\n}\nTable orders {\n id int\n}")
        self.write("dbt_project.yml", 'name: demo\nmodel-paths: ["custom models"]\n')
        self.write("custom models/orders.py", "def model(dbt, session): pass\n")
        self.write("models/users.sql", "select 2 as id\n")
        changes = self.compare()
        self.assertEqual(changes["tables"]["orders"]["status"], "new")
        self.assertEqual(changes["tables"]["users"]["status"], "modified")

    def test_manifest_alias_matches_qualified_card(self):
        self.data = parse_dbml_text("Table analytics.customers {\n id int\n}\nTable raw.customers {\n id int\n}")
        self.write("target/manifest.json", json.dumps({"nodes": {
            "model.demo.users": {"resource_type": "model", "name": "users", "alias": "customers",
                                 "schema": "analytics", "original_file_path": "models/users.sql"},
        }}))
        self.write("models/users.sql", "select 2 as id\n")
        changes = self.compare()
        self.assertEqual(changes["tables"]["analytics.customers"]["status"], "modified")
        self.assertEqual(changes["tables"]["raw.customers"]["status"], "unchanged")

    def test_deleted_model_is_reviewable_without_card(self):
        (self.root / "models/users.sql").unlink()
        self.data = parse_dbml_text("Table orders {\n id int\n}")
        changes = self.compare()
        self.assertEqual(changes["counts"]["removed"], 1)
        self.assertEqual(changes["unmapped"], ["models/users.sql"])
        self.assertIn("No unique diagram card", changes["tables"]["models/users.sql"]["details"][-1])

    def test_move_with_same_model_name_is_modified(self):
        self.git("mv", "models/users.sql", "models/renamed.sql")
        # A renamed model is a removed model plus a new model.
        changes = self.compare()
        self.assertEqual(changes["counts"]["removed"], 1)
        self.assertEqual(changes["counts"]["new"], 1)
        self.git("mv", "models/renamed.sql", "models/users.sql")
        (self.root / "models/subdir").mkdir()
        self.git("mv", "models/users.sql", "models/subdir/users.sql")
        changes = self.compare()
        self.assertEqual(changes["tables"]["users"]["status"], "modified")
        self.assertEqual(changes["counts"]["new"], 0)
        self.assertEqual(changes["counts"]["removed"], 0)

    def test_ambiguous_names_are_reviewable_without_guessing(self):
        self.write("models/other/users.sql", "select 2 as id\n")
        self.write("models/users.sql", "select 2 as id\n")
        changes = self.compare()
        self.assertEqual(changes["tables"]["users"]["status"], "unchanged")
        self.assertEqual(set(changes["unmapped"]), {"models/users.sql", "models/other/users.sql"})

    def test_nested_project_config_and_manifest(self):
        self.data = parse_dbml_text("Table users {\n id int\n}\nTable orders {\n id int\n}")
        self.write("dbt/target/manifest.json", json.dumps({"nodes": {
            "model.nested.orders": {"resource_type": "model", "name": "renamed", "alias": "orders",
                                    "schema": "public", "original_file_path": "transform/renamed.sql"},
        }}))
        self.write("dbt/dbt_project.yml", "name: nested\nmodel-paths: [transform]\n")
        self.write("dbt/transform/renamed.sql", "select 1 as id\n")
        self.assertEqual(self.compare()["tables"]["orders"]["status"], "new")

    def cli(self, *args, cwd=None):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
        return subprocess.run([sys.executable, "-m", "dbml_viewer", *map(str, args), "--no-open"],
                              cwd=cwd or self.root, env=env, capture_output=True, text=True)

    def test_cli_defaults_to_sources_and_supports_opt_out(self):
        self.write("generated.dbml", "Table users {\n id int\n}")
        self.write("models/users.sql", "select 2 as id\n")
        path = self.root / "generated.dbml"
        result = self.cli(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        output = path.with_name("generated_diagram.html")
        self.assertIn("Modified model source: models/users.sql", output.read_text())
        self.assertIn("Model and design changes", output.read_text())
        result = self.cli(path, "--no-compare")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('id="branch-toggle"', output.read_text())
        result = self.cli(path, "--compare-dbml", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DBML schema changes", output.read_text())

    def test_cli_external_generated_file_and_no_git_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "generated.dbml"
            path.write_text("Table users {\n id int\n}")
            self.write("models/users.sql", "select 2 as id\n")
            result = self.cli(path)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Modified model source", path.with_name("generated_diagram.html").read_text())
            result = self.cli(path, cwd=directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Branch highlighting unavailable", result.stderr)
            result = self.cli(path, "--project-dir", self.root, cwd=directory)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = self.cli(path, "--compare", "missing")
            self.assertNotEqual(result.returncode, 0)

    def test_design_only_new_model_needs_no_sql_or_build(self):
        self.data = parse_dbml_text("Table users {\n id int\n}\nTable planned_orders {\n id int\n}")
        changes = self.compare()
        self.assertEqual(changes["tables"]["users"]["status"], "unchanged")
        self.assertEqual(changes["tables"]["planned_orders"]["status"], "new")
        self.assertIn("Proposed model", changes["tables"]["planned_orders"]["details"][0])
        self.assertTrue(changes["design_baseline_missing"])

    def test_existing_source_name_matches_case_insensitively(self):
        self.data = parse_dbml_text("Table analytics.USERS {\n id int\n}")
        self.assertEqual(self.compare()["tables"]["analytics.USERS"]["status"], "unchanged")
        self.write("models/users.sql", "select 2 as id\n")
        self.assertEqual(self.compare()["tables"]["analytics.USERS"]["status"], "modified")

    def test_no_manifest_literal_alias_and_yaml_declarations(self):
        self.write("models/users.sql", "{{ config(alias='customers') }}\nselect 1 as id\n")
        self.write("models/schema.yml", """models:
  - name: users
    config:
      alias: customers
sources:
  - name: crm
    schema: landing_crm
    tables:
      - name: events
        identifier: click_events
""")
        self.write("seeds/countries.csv", "id\n1\n")
        self.write("snapshots/history.sql", "select 1 as id\n")
        self.data = parse_dbml_text("Table customers {\n id int\n}\nTable landing_crm.click_events {\n id int\n}\nTable countries {\n id int\n}\nTable history {\n id int\n}\nTable src_external {\n id int\n}")
        changes = self.compare()
        self.assertEqual(changes["counts"]["new"], 1)
        self.assertEqual(changes["tables"]["countries"]["status"], "new")
        self.assertEqual(changes["tables"]["customers"]["status"], "modified")

    def test_broken_manifest_falls_back_to_files(self):
        self.write("models/users.sql", "select 2 as id\n")
        for contents in ("broken JSON", "[]"):
            self.write("target/manifest.json", contents)
            changes = self.compare()
            self.assertEqual(changes["tables"]["users"]["status"], "modified")
            self.assertEqual(changes["counts"]["new"], 0)
            self.assertIn("falling back to project files", changes["warnings"][0])

    def test_baseline_aliases_are_preserved_without_manifest(self):
        self.git("switch", "main")
        self.write("models/users.sql", "{{ config(alias='customers') }}\nselect 1 as id\n")
        self.write("models/schema.yml", "sources:\n  - name: crm\n    tables:\n      - name: events\n")
        self.git("add", ".")
        self.git("commit", "-m", "original aliases")
        self.git("switch", "feature")
        self.git("merge", "--ff-only", "main")
        self.write("models/users.sql", "{{ config(alias='accounts') }}\nselect 1 as id\n")
        self.write("models/schema.yml", "sources:\n  - name: crm\n    tables:\n      - name: renamed_events\n")
        self.data = parse_dbml_text("Table customers {\n id int\n}\nTable crm.events {\n id int\n}")
        changes = self.compare()
        self.assertEqual(changes["tables"]["customers"]["status"], "modified")
        self.assertEqual(changes["counts"]["new"], 0)

    def test_unimplemented_existing_design_changes_need_baseline(self):
        self.write("before.dbml", "Table users {\n id int\n}\nTable planned {\n id int\n}\nTable retired {\n id int\n}")
        self.data = parse_dbml_text("Table users {\n id bigint\n}\nTable planned {\n id int\n description varchar\n}\nTable next {\n id int\n}")
        changes = compare_sources(self.root, self.data, baseline_dbml=self.root / "before.dbml")
        self.assertEqual(changes["counts"], {"new": 1, "modified": 2, "removed": 1, "unchanged": 0})
        self.assertFalse(changes["design_baseline_missing"])
        self.assertIn("DBML design: Changed type: id: int → bigint", changes["tables"]["users"]["details"])
        self.assertEqual(changes["tables"]["planned"]["status"], "modified")
        self.assertEqual(changes["tables"]["retired"]["status"], "removed")

    def test_unchanged_design_only_model_is_not_inferred_new_with_baseline(self):
        self.write("before.dbml", "Table planned {\n id int\n}")
        self.data = parse_dbml_text("Table planned {\n id int\n}")
        changes = compare_sources(self.root, self.data, baseline_dbml=self.root / "before.dbml")
        self.assertEqual(changes["tables"]["planned"]["status"], "unchanged")

    def test_tracked_design_baseline_is_used_automatically(self):
        path = self.root / "design.dbml"
        self.git("switch", "main")
        self.write("design.dbml", "Table users {\n id int\n}\nTable planned {\n id int\n}")
        self.git("add", "-f", "design.dbml")
        self.git("commit", "-m", "original design")
        self.git("switch", "feature")
        self.git("merge", "--ff-only", "main")
        self.write("design.dbml", "Table users {\n id bigint\n}\nTable planned {\n id int\n}\nTable next {\n id int\n}")
        result = self.cli(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        html = path.with_name("design_diagram.html").read_text()
        self.assertIn("DBML design: Changed type: id: int", html)
        self.assertIn("Design baseline:", html)
        self.assertNotIn("No earlier DBML:", html)

    def test_source_and_schema_changes_are_combined(self):
        self.write("before.dbml", "Table users {\n id int\n}")
        self.write("models/users.sql", "select 2 as id\n")
        self.data = parse_dbml_text("Table users {\n id bigint\n}")
        changes = compare_sources(self.root, self.data, baseline_dbml=self.root / "before.dbml")
        self.assertEqual(changes["counts"]["modified"], 1)
        details = changes["tables"]["users"]["details"]
        self.assertEqual(len(details), 2)
        self.assertTrue(details[0].startswith("Modified model source:"))
        self.assertTrue(details[1].startswith("DBML design:"))

    def test_cli_baseline_for_ignored_dbml_and_missing_baseline_error(self):
        self.write("before.dbml", "Table users {\n id int\n}")
        self.write("design.dbml", "Table users {\n id bigint\n}")
        path = self.root / "design.dbml"
        result = self.cli(path, "--baseline-dbml", self.root / "before.dbml")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DBML design: Changed type", path.with_name("design_diagram.html").read_text())
        result = self.cli(path, "--baseline-dbml", self.root / "missing.dbml")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)

    def test_proposed_seed_is_new_without_csv_or_build(self):
        for dbml in (
            "Table seed_new_status {\n id int\n}",
            "Table new_status__seed {\n id int\n}",
            "Table new_status {\n id int\n}\nTableGroup seeds {\n new_status\n}",
            "Table new_status {\n id int\n}\nTableGroup reference {\n new_status\n}",
            "Table new_status__seed {\n id int\n}\nTableGroup sources {\n new_status__seed\n}",
        ):
            with self.subTest(dbml=dbml):
                self.data = parse_dbml_text(dbml)
                changes = self.compare()
                name = self.data["tables"][0]["name"]
                self.assertEqual(changes["tables"][name]["status"], "new")
                self.assertIn("Proposed model", changes["tables"][name]["details"][0])

    def test_existing_seed_stays_unchanged_but_csv_edits_are_modified(self):
        self.git("switch", "main")
        self.write("seeds/status__seed.csv", "id,label\n1,active\n")
        self.git("add", ".")
        self.git("commit", "-m", "existing seed")
        self.git("switch", "feature")
        self.git("merge", "--ff-only", "main")
        self.data = parse_dbml_text("Table status__seed {\n id int\n label varchar\n}")
        self.assertEqual(self.compare()["tables"]["status__seed"]["status"], "unchanged")
        self.write("seeds/status__seed.csv", "id,label\n1,active\n2,inactive\n")
        changes = self.compare()
        self.assertEqual(changes["tables"]["status__seed"]["status"], "modified")
        self.assertEqual(changes["tables"]["status__seed"]["details"], ["Modified model source: seeds/status__seed.csv"])

    def test_new_seed_csv_matches_yaml_alias_in_custom_seed_path(self):
        self.write("dbt_project.yml", "name: demo\nmodel-paths: [models]\nseed-paths: [lookups]\n")
        self.write("lookups/status.csv", "id\n1\n")
        self.write("lookups/schema.yml", "seeds:\n  - name: status\n    config:\n      alias: seed_status\n")
        self.data = parse_dbml_text("Table seed_status {\n id int\n}")
        changes = self.compare()
        self.assertEqual(changes["tables"]["seed_status"]["status"], "new")
        self.assertEqual(changes["unmapped"], [])

    def test_seed_manifest_alias_and_csv_removal(self):
        self.git("switch", "main")
        self.write("seeds/status.csv", "id\n1\n")
        self.git("add", ".")
        self.git("commit", "-m", "seed")
        self.git("switch", "feature")
        self.git("merge", "--ff-only", "main")
        self.write("target/manifest.json", json.dumps({"nodes": {
            "seed.demo.status": {"resource_type": "seed", "name": "status", "alias": "seed_status",
                                 "schema": "public", "original_file_path": "seeds/status.csv"},
        }}))
        self.data = parse_dbml_text("Table seed_status {\n id int\n}")
        self.assertEqual(self.compare()["tables"]["seed_status"]["status"], "unchanged")
        (self.root / "seeds/status.csv").unlink()
        changes = self.compare()
        self.assertEqual(changes["tables"]["seed_status"]["status"], "removed")
        self.assertEqual(changes["unmapped"], [])


if __name__ == "__main__":
    unittest.main()
