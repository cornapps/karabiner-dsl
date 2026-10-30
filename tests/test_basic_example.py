"""The starter config compiles, lints clean, and round-trips without helpers."""

import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

from karabiner_dsl.keys import HELPERS_ROOT_VARIABLE
from karabiner_dsl.lint import lint_file
from karabiner_dsl.pipeline import compile_file, roundtrip_file


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "basic_example.kb"


class BasicExampleTests(unittest.TestCase):
    def setUp(self):
        environment = {name: value for name, value in os.environ.items() if name != HELPERS_ROOT_VARIABLE}
        patcher = mock.patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_compiles_and_lints_clean(self):
        rules = compile_file(EXAMPLE)["profiles"][0]["complex_modifications"]["rules"]
        self.assertGreater(len(rules), 0)
        self.assertEqual(lint_file(EXAMPLE), [])

    def test_round_trips(self):
        scratch = ROOT / ".build" / "test-basic-example"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            copy = Path(directory) / EXAMPLE.name
            shutil.copy(EXAMPLE, copy)
            self.assertTrue(roundtrip_file(copy)["semantic_matches"])


if __name__ == "__main__":
    unittest.main()
