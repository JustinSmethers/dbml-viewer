import tempfile
import unittest
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from dbml_viewer.parser import parse_dbml


class ParseDbmlTests(unittest.TestCase):
    def parse_text(self, dbml_text: str) -> dict:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "schema.dbml"
            path.write_text(dbml_text, encoding="utf-8")
            return parse_dbml(path)

    def test_table_group_color_is_captured(self):
        data = self.parse_text(
            """
            Table users {
              id int [pk]
            }

            Table posts {
              id int [pk]
            }

            TableGroup core [color: #6C8EBF] {
              users
            }

            TableGroup content {
              posts
            }
            """
        )

        self.assertEqual(data["group_colors"], {"core": "#6C8EBF"})
        self.assertEqual(data["groups"], {"core": ["users"], "content": ["posts"]})

    def test_greater_than_ref_marks_left_side_as_fk(self):
        data = self.parse_text(
            """
            Table users {
              id int [pk]
            }

            Table posts {
              id int [pk]
              user_id int
            }

            Ref: posts.user_id > users.id
            """
        )

        self.assertEqual(
            data["refs"],
            [
                {
                    "from_table": "posts",
                    "from_column": "user_id",
                    "to_table": "users",
                    "to_column": "id",
                    "type": ">",
                }
            ],
        )
        posts = next(table for table in data["tables"] if table["name"] == "posts")
        users = next(table for table in data["tables"] if table["name"] == "users")
        self.assertTrue(next(col for col in posts["columns"] if col["name"] == "user_id")["fk"])
        self.assertFalse(next(col for col in users["columns"] if col["name"] == "id")["fk"])

    def test_less_than_ref_marks_right_side_as_fk(self):
        data = self.parse_text(
            """
            Table users {
              id int [pk]
            }

            Table posts {
              id int [pk]
              user_id int
            }

            Ref: users.id < posts.user_id
            """
        )

        self.assertEqual(
            data["refs"],
            [
                {
                    "from_table": "posts",
                    "from_column": "user_id",
                    "to_table": "users",
                    "to_column": "id",
                    "type": "<",
                }
            ],
        )
        posts = next(table for table in data["tables"] if table["name"] == "posts")
        users = next(table for table in data["tables"] if table["name"] == "users")
        self.assertTrue(next(col for col in posts["columns"] if col["name"] == "user_id")["fk"])
        self.assertFalse(next(col for col in users["columns"] if col["name"] == "id")["fk"])

    def test_composite_ref_is_flattened_and_marks_all_fk_columns(self):
        data = self.parse_text(
            """
            Table parent {
              a int [pk]
              b int [pk]
            }

            Table child {
              a int
              b int
            }

            Ref: child.(a, b) > parent.(a, b)
            """
        )

        self.assertEqual(
            data["refs"],
            [
                {
                    "from_table": "child",
                    "from_column": "a",
                    "to_table": "parent",
                    "to_column": "a",
                    "type": ">",
                },
                {
                    "from_table": "child",
                    "from_column": "b",
                    "to_table": "parent",
                    "to_column": "b",
                    "type": ">",
                },
            ],
        )
        child = next(table for table in data["tables"] if table["name"] == "child")
        child_columns = {col["name"]: col for col in child["columns"]}
        self.assertTrue(child_columns["a"]["fk"])
        self.assertTrue(child_columns["b"]["fk"])

    def test_dash_ref_does_not_mark_fk_columns(self):
        data = self.parse_text(
            """
            Table profiles {
              id int [pk]
            }

            Table users {
              id int [pk]
              profile_id int
            }

            Ref: users.profile_id - profiles.id
            """
        )

        self.assertEqual(
            data["refs"],
            [
                {
                    "from_table": "users",
                    "from_column": "profile_id",
                    "to_table": "profiles",
                    "to_column": "id",
                    "type": "-",
                }
            ],
        )
        users = next(table for table in data["tables"] if table["name"] == "users")
        self.assertFalse(next(col for col in users["columns"] if col["name"] == "profile_id")["fk"])


if __name__ == "__main__":
    unittest.main()
