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
        self.data = parse_dbml_text("Table users {\n id int\n}\nTable orders {\n id int\n}")

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
        self.assertEqual(changes["counts"], {"new": 0, "modified": 1, "removed": 0, "unchanged": 1})
        self.assertEqual(changes["tables"]["users"]["details"], ["Modified model source: models/users.sql"])
        self.assertIn("Model source changes", render_html(dict(self.data, comparison=changes)))

    def test_new_staged_unstaged_and_untracked_models(self):
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
        self.assertIn("Model source changes", output.read_text())
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


if __name__ == "__main__":
    unittest.main()
