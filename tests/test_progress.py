import io
import sys
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dbml_viewer.progress import Progress


class TerminalOutput(io.StringIO):
    def __init__(self):
        super().__init__()
        self.animated = Event()

    def isatty(self):
        return True

    def write(self, value):
        result = super().write(value)
        if value.startswith("\r/ "):
            self.animated.set()
        return result


class ProgressTests(unittest.TestCase):
    def test_redirected_output_is_plain_and_flushed_before_work(self):
        stream = io.StringIO()
        with Progress(stream) as progress:
            progress.stage("Parsing DBML")
            self.assertIn("Parsing DBML...\n", stream.getvalue())
            progress.stage("Generating HTML")
        output = stream.getvalue()
        self.assertNotIn("\r", output)
        self.assertNotIn("\x1b", output)
        self.assertEqual(output.count("Done ("), 2)
        self.assertLess(output.index("Parsing DBML"), output.index("Generating HTML"))

    @patch.dict("os.environ", {"TERM": "xterm"})
    def test_spinner_updates_while_work_runs_and_stops_on_failure(self):
        stream = TerminalOutput()
        with self.assertRaisesRegex(ValueError, "fetch failed"):
            with Progress(stream) as progress:
                progress.stage("Fetching origin/main")
                self.assertTrue(stream.animated.wait(2), "Spinner did not update during work")
                raise ValueError("fetch failed")
        self.assertFalse(progress._thread.is_alive())
        self.assertIn("Fetching origin/main: failed (", stream.getvalue())
        self.assertTrue(stream.getvalue().endswith("s)\n"))
        self.assertNotIn(": done", stream.getvalue())

    @patch.dict("os.environ", {"TERM": "dumb"})
    def test_dumb_terminal_uses_plain_output(self):
        stream = TerminalOutput()
        with Progress(stream) as progress:
            progress.stage("Parsing DBML")
        self.assertNotIn("\r", stream.getvalue())
        self.assertIsNone(progress._thread)


if __name__ == "__main__":
    unittest.main()
