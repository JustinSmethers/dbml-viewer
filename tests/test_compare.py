import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbml_viewer.compare import compare_branch, compare_models
from dbml_viewer.parser import parse_dbml_text
from dbml_viewer.renderer import render_html


class ComparisonTests(unittest.TestCase):
    def compare(self, before, after):
        return compare_models(parse_dbml_text(before), parse_dbml_text(after))

    def test_added_removed_and_modified_columns(self):
        changes = self.compare(
            'Table users {\n id int [pk]\n amount int\n email varchar\n}\nTable legacy {\n id int\n}',
            'Table users {\n id int [pk]\n amount decimal\n segment varchar\n}\nTable orders {\n id int\n}',
        )
        self.assertEqual(changes["counts"], {"new": 1, "modified": 1, "removed": 1, "unchanged": 0})
        self.assertEqual(changes["tables"]["users"]["details"], [
            "Changed type: amount: int → decimal", "Removed column: email (varchar)",
            "Added column: segment (varchar)",
        ])

    def test_formatting_order_and_ref_direction_are_not_changes(self):
        changes = self.compare(
            'Table users {\n id int [pk]\n name varchar\n}\nTable posts {\n user_id int\n}\nRef: posts.user_id > users.id',
            '// reformatted\nTable posts {\n user_id int\n}\nTable users {\n name varchar\n id int [pk]\n}\nRef: users.id < posts.user_id',
        )
        self.assertEqual(changes["counts"]["unchanged"], 2)

    def test_new_relationship_marks_existing_endpoints(self):
        before = 'Table users {\n id int [pk]\n}\nTable posts {\n user_id int\n}'
        changes = self.compare(before, before + '\nRef: posts.user_id > users.id')
        self.assertEqual(changes["counts"]["modified"], 2)
        self.assertIn("Added relationship", changes["tables"]["users"]["details"][0])

    def test_removed_relationship_and_model_are_reviewable(self):
        changes = self.compare(
            'Table users {\n id int\n}\nTable posts {\n user_id int\n}\nRef: posts.user_id > users.id',
            'Table users {\n id int\n}',
        )
        self.assertEqual(changes["tables"]["posts"]["status"], "removed")
        self.assertEqual(changes["tables"]["users"]["status"], "modified")

    def test_constraints_defaults_notes_and_indexes_are_compared(self):
        changes = self.compare(
            'Table users {\n id int\n name varchar [default: "before"]\n}',
            'Table users {\n id int [pk, increment, not null]\n name varchar [unique, default: "after", note: "label"]\n indexes {\n name [unique]\n }\n Note: "new note"\n}',
        )
        details = changes["tables"]["users"]["details"]
        self.assertIn("Changed primary key: id", details)
        self.assertIn("Changed increment: id", details)
        self.assertIn("Changed not null: id", details)
        self.assertIn("Changed unique: name", details)
        self.assertIn("Changed default: name: before → after", details)
        self.assertIn("Changed note: name", details)
        self.assertIn("Model note changed", details)
        self.assertIn("Indexes changed", details)

    def test_schema_qualified_names_do_not_collide(self):
        data = parse_dbml_text('Table a.users {\n id int\n}\nTable b.users {\n id int\n}\nRef: a.users.id > b.users.id\nTableGroup core {\n a.users\n b.users\n}')
        self.assertEqual([t["name"] for t in data["tables"]], ["a.users", "b.users"])
        self.assertEqual(data["groups"]["core"], ["a.users", "b.users"])
        self.assertTrue(data["tables"][0]["columns"][0]["fk"])
        changes = compare_models(data, parse_dbml_text('Table a.users {\n id bigint\n}\nTable b.users {\n id int\n}\nRef: a.users.id > b.users.id\nTableGroup core {\n a.users\n b.users\n}'))
        self.assertEqual(changes["counts"]["modified"], 1)

    def test_default_expression_is_distinct_from_string_literal(self):
        changes = self.compare(
            'Table users {\n created_at timestamp [default: "now()"]\n}',
            'Table users {\n created_at timestamp [default: `now()`]\n}',
        )
        self.assertEqual(changes["counts"]["modified"], 1)
        self.assertIn("Changed default kind: created_at", changes["tables"]["users"]["details"])

    def test_group_and_enum_changes(self):
        before = 'Enum status {\n active\n}\nTable users {\n state status\n}\nTableGroup core {\n users\n}'
        after = 'Enum status {\n active\n inactive\n}\nTable users {\n state status\n}\nTableGroup mart {\n users\n}'
        changes = self.compare(before, after)
        self.assertIn("Group changed", changes["tables"]["users"]["details"])
        self.assertIn("Enum changed: status", changes["tables"]["users"]["details"])

    def test_comparison_html_escapes_names_and_ref(self):
        data = parse_dbml_text('Table users {\n id int\n}')
        data["comparison"] = self.compare('', 'Table users {\n id int\n}')
        data["comparison"].update(revision='<script>alert(1)</script>', base='12345678')
        html = render_html(data)
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('id="branch-focus"', html)
        self.assertNotIn('id="branch-focus"', render_html(parse_dbml_text('Table users {\n id int\n}')))


class GitComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'schema.dbml'
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        self.path.write_text('Table users {\n id int\n}')
        self.git('add', '.')
        self.git('commit', '-m', 'base')

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.root), *args], check=True, capture_output=True, text=True).stdout.strip()

    def test_uses_merge_base_and_includes_local_edits(self):
        base = self.git('rev-parse', 'HEAD')
        self.git('switch', '-c', 'feature')
        self.path.write_text('Table users {\n id bigint\n}')
        self.git('commit', '-am', 'feature edit')
        self.git('switch', 'main')
        self.path.write_text('Table users {\n id varchar\n}')
        self.git('commit', '-am', 'main edit')
        self.git('switch', 'feature')
        self.path.write_text('Table users {\n id decimal\n}')
        changes = compare_branch(self.path, parse_dbml_text(self.path.read_text()), 'main')
        self.assertEqual(changes['base'], base[:8])
        self.assertEqual(changes['tables']['users']['details'], ['Changed type: id: int → decimal'])

    def test_new_file_is_all_new(self):
        path = self.root / 'new schema.dbml'
        path.write_text('Table posts {\n id int\n}')
        changes = compare_branch(path, parse_dbml_text(path.read_text()), 'main')
        self.assertEqual(changes['counts']['new'], 1)

    def test_missing_revision_and_outside_repo_have_clear_errors(self):
        with self.assertRaisesRegex(ValueError, 'Cannot compare'):
            compare_branch(self.path, parse_dbml_text(self.path.read_text()), 'missing')
        with tempfile.TemporaryDirectory() as other:
            path = Path(other) / 'schema.dbml'
            path.write_text('Table posts {\n id int\n}')
            with self.assertRaisesRegex(ValueError, 'Cannot compare'):
                compare_branch(path, parse_dbml_text(path.read_text()), 'main')
