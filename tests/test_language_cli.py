import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import karabiner_dsl
from karabiner_dsl import cli
from karabiner_dsl.keys import HELPERS_ROOT_VARIABLE, MissingHelpersError
from karabiner_dsl.language import LanguageError, read_language
from karabiner_dsl.pipeline import compile_text, roundtrip_file


ROOT = Path(__file__).resolve().parents[1]
HELPER_SOURCES = {
    "`hints on`": "hints on\nhyper is $\n$ is caps_lock\n$:\n  h sends left_arrow\n",
    "A numpad block": "devices:\n  pad:\n    id 1:2\nnumpad pad:\n  mode t9:\n    keypad_1 types t9 1\n",
    "`tracks pointer` or `clicks current`": "f1 tracks pointer\nf2 right-clicks current\n",
}


def without_helpers():
    environment = {name: value for name, value in os.environ.items() if name != HELPERS_ROOT_VARIABLE}
    return mock.patch.dict(os.environ, environment, clear=True)


class LanguageTests(unittest.TestCase):
    def test_unversioned_and_explicit_legacy_preserve_output_and_lines(self):
        self.assertFalse(read_language("a sends b").explicit)
        mode = read_language("# comment\n\nlanguage 1 # explicit\na sends b")
        self.assertEqual((mode.version, mode.explicit, mode.header_line), (1, True, 3))
        source = "a sends b\na:\n  sends c\n"
        self.assertEqual(compile_text(source), compile_text("language 1\n" + source))
        self.assertEqual(compile_text(source)["profiles"][0]["complex_modifications"]["rules"][0]["manipulators"][0]["to"], [{"key_code": "c"}])

    def test_invalid_language_requests_never_reach_ordinary_parsing(self):
        sources = [
            "language 2\na sends b", "language 99", "language zero", "language",
            "language 0", "language -1", "language 1 extra", "language: 1",
            "language=2", "Language 2", "LANGUAGE 1",
            " language 1\na sends b", "a sends b\nlanguage 1",
            "language 1\nlanguage 1", "when app Terminal:\n  language 1",
            "actions:\n  sample:\n    language 1",
        ]
        for source in sources:
            with self.subTest(source=source), self.assertRaises(LanguageError):
                compile_text(source)

    def test_version_scan_does_not_confuse_payloads_or_action_names(self):
        source = "actions:\n  language:\n    runs echo language 2\nf1 triggers language"
        self.assertFalse(read_language(source).explicit)
        self.assertEqual(compile_text(source)["profiles"][0]["complex_modifications"]["rules"][0]["manipulators"][0]["to"], [{"shell_command": "echo language 2"}])


class CliTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".build" / "test-cli"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / "config.kb"

    def invoke(self, entry, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                entry(args)
                code = 0
            except SystemExit as error:
                code = error.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_all_function_entry_points_share_lint_version_and_failures(self):
        self.source.write_text("language 1\na sends b\n")
        for args in (["--version"], ["--lint", str(self.source)], [str(self.source)], []):
            results = [self.invoke(entry, args) for entry in (cli.main, cli.legacy_main, karabiner_dsl.main)]
            with self.subTest(args=args):
                self.assertEqual(results, [results[0]] * 3)
        self.assertIn("(languages: 1)", self.invoke(cli.main, ["--version"])[1])
        self.source.write_text("mystery words\n")
        result = self.invoke(cli.main, ["--lint", str(self.source)])
        self.assertEqual(result[0], 1)
        self.assertIn("unrecognized", result[2])

    def test_version_failures_preserve_output_roundtrip_and_apply_destinations(self):
        self.source.write_text("language 2\na sends b\n")
        output = self.directory / "output.json"
        config = self.directory / "karabiner.json"
        artifacts = [output, config, self.directory / "config.test.json", self.directory / "config.test.kb"]
        for path in artifacts:
            path.write_text("keep this file")
        for entry in (cli.main, cli.legacy_main):
            for args in ([str(self.source), "-o", str(output)], ["--roundtrip", str(self.source)], ["--lint", str(self.source)], ["--apply", str(self.source)], ["--install", str(self.source)]):
                with self.subTest(entry=entry, args=args), mock.patch.object(cli, "KARABINER_CONFIG_FILE", config), mock.patch.object(cli.subprocess, "run") as launch:
                    result = self.invoke(entry, args)
                    self.assertEqual(result[0], 2)
                    self.assertEqual(result[1], "")
                    self.assertIn("unsupported language version 2", result[2])
                    self.assertNotIn("Traceback", result[2])
                    self.assertEqual([p.read_text() for p in artifacts], ["keep this file"] * 4)
                    self.assertFalse(config.with_suffix(".json.bak").exists())
                    launch.assert_not_called()
        with self.assertRaises(LanguageError):
            roundtrip_file(self.source)
        self.assertEqual(artifacts[2].read_text(), "keep this file")

    def test_helper_features_require_an_explicit_helper_install(self):
        with without_helpers():
            self.assertEqual(compile_text("hints on\na sends b")["profiles"][0]["complex_modifications"]["rules"][0]["manipulators"][0]["to"], [{"key_code": "b"}])
            for feature, source in HELPER_SOURCES.items():
                with self.subTest(feature=feature), self.assertRaises(MissingHelpersError) as raised:
                    compile_text(source)
                self.assertEqual(str(raised.exception), f"{feature} needs the companion helpers. Set "
                                 "KARABINER_HELPERS_ROOT to the directory where they are installed.")
        with mock.patch.dict(os.environ, {HELPERS_ROOT_VARIABLE: "/example/helpers"}):
            for feature, source in HELPER_SOURCES.items():
                with self.subTest(feature=feature):
                    self.assertIn("/example/helpers/", json.dumps(compile_text(source)))

    def test_missing_helpers_preserve_output_roundtrip_and_apply_destinations(self):
        self.source.write_text(HELPER_SOURCES["A numpad block"])
        output = self.directory / "output.json"
        config = self.directory / "karabiner.json"
        artifacts = [output, config, self.directory / "config.test.json", self.directory / "config.test.kb"]
        for path in artifacts:
            path.write_text("keep this file")
        for args in ([str(self.source), "-o", str(output)], ["--roundtrip", str(self.source)], ["--lint", str(self.source)], ["--explain", str(self.source)], ["--apply", str(self.source)]):
            with self.subTest(args=args), without_helpers(), mock.patch.object(cli, "KARABINER_CONFIG_FILE", config), mock.patch.object(cli.subprocess, "run") as launch:
                result = self.invoke(cli.main, args)
                self.assertEqual(result[0], 2)
                self.assertEqual(result[1], "")
                self.assertIn("A numpad block needs the companion helpers. Set KARABINER_HELPERS_ROOT", result[2])
                self.assertNotIn("Traceback", result[2])
                self.assertEqual([p.read_text() for p in artifacts], ["keep this file"] * 4)
                self.assertFalse(config.with_suffix(".json.bak").exists())
                launch.assert_not_called()

    def test_repository_script_and_module_have_identical_cli_output(self):
        self.source.write_text("language 1\na sends b\n")
        for args in (["--help"], ["--version"], ["--lint", str(self.source)]):
            results = [subprocess.run(command + list(args), cwd=ROOT, capture_output=True, text=True)
                       for command in ([sys.executable, "-m", "karabiner_dsl"], [sys.executable, str(ROOT / "karabiner_dsl.py")])]
            self.assertEqual([(r.returncode, r.stdout, r.stderr) for r in results], [(results[0].returncode, results[0].stdout, results[0].stderr)] * 2)


if __name__ == "__main__":
    unittest.main()
