import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from karabiner_dsl import cli
from karabiner_dsl.explain import explain_file, explain_text, format_explanation
from karabiner_dsl.lint import diagnose_text, lint_text
from karabiner_dsl.pipeline import compile_text


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests/fixtures/priority_sample.kb"
EVENT = {"consumer_key_code": "volume_increment", "modifiers": []}
CONTEXT = {"device_enabled": True, "device": {"vendor_id": 1, "product_id": 2}}


class ExplanationTests(unittest.TestCase):
    def test_three_supplied_contexts_pick_literal_actions(self):
        cases = [("com.apple.Terminal", 1, [{"mouse_key": {"vertical_wheel": 32}}]),
                 ("com.apple.Safari", 1, [{"mouse_key": {"vertical_wheel": -32}}]),
                 ("com.apple.Terminal", 0, [{"consumer_key_code": "volume_decrement"}])]
        for app, scroll, action in cases:
            result = explain_file(SAMPLE, EVENT, CONTEXT | {"app_bundle": app, "variables": {"scroll": scroll}})
            winner = next(c for c in result["candidates"] if c["index"] == result["winner_index"])
            self.assertTrue(result["winner_definitive"])
            self.assertEqual(winner["manipulator"]["to"], action)
            self.assertEqual(winner["origin"]["origin"]["source"]["path"], str(SAMPLE))
            self.assertEqual(result["missing_facts"], [])

    def test_missing_state_prevents_fallback_certainty_in_json_and_text(self):
        result = explain_file(SAMPLE, EVENT, CONTEXT)
        self.assertIsNone(result["winner_index"])
        self.assertFalse(result["winner_definitive"])
        self.assertEqual(result["possible_indices"], [0, 1, 2])
        self.assertIn("context.variables.scroll", result["missing_facts"])
        text = format_explanation(result)
        self.assertIn("Winner: unknown", text)
        self.assertIn("not observed live state", text)
        self.assertIn("context.variables.scroll", text)
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_filter_does_not_supply_event_or_recalculate_global_winner(self):
        source = "a sends b\nc sends d"
        result = explain_text(source, filter_key="a")
        self.assertEqual(result["supplied"]["event"], {})
        self.assertIsNone(result["winner_index"])
        self.assertIn("event.key_code", result["missing_facts"])
        result = explain_text(source, {"key_code": "c", "modifiers": []}, {"device_enabled": True}, filter_key="a")
        self.assertEqual(result["winner_index"], 1)
        self.assertEqual([c["index"] for c in result["candidates"]], [0])

    def test_source_and_macro_origins_are_separate_from_karabiner_json(self):
        source = "actions:\n  copy:\n    sends cmd+c\nf1 triggers copy"
        before = compile_text(source)
        result = explain_text(source)
        origin = result["candidates"][0]["origin"]
        self.assertEqual(origin["origin"]["source"]["line"], 4)
        call = origin["actions"][0]["origin"]["macro_calls"][0]
        self.assertEqual((call["call"]["line"], call["definition"]["line"]), (4, 2))
        self.assertIn("call line 4, definition line 2", format_explanation(result))
        self.assertEqual(before, compile_text(source))
        self.assertNotIn("origin", json.dumps(before))

    def test_human_trace_names_each_action_path_and_source_line(self):
        cases = [
            ("language 1\na:\n  sends b\n  sends c\n",
             ("action to[0]: line 3, column 3", "action to[1]: line 4, column 3")),
            ("language 1\nspacebar tap sends spacebar\nspacebar hold sends left_shift lazy\n",
             ("action to[0]: line 3, column 1", "action to_if_alone[0]: line 2, column 1")),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                result = explain_text(source, source_path=SAMPLE)
                rendered = format_explanation(result)
                for action in expected:
                    self.assertIn(action, rendered)
                self.assertIn(str(SAMPLE), rendered)

    def test_explain_keeps_skipped_source_and_unknown_key_diagnostics(self):
        result = explain_text("language 1\na send b\nc sends imaginary_key\n", source_path=SAMPLE)
        self.assertEqual(result["diagnostics"][0]["source"]["line"], 2)
        self.assertEqual(result["diagnostics"][0]["source"]["path"], str(SAMPLE))
        self.assertEqual(result["diagnostics"][1]["sources"][0]["line"], 3)
        rendered = format_explanation(result)
        self.assertIn("line 2: unrecognized line: a send b", rendered)
        self.assertIn("unknown key_code in compiled output: imaginary_key; mapping lines 3", rendered)
        self.assertEqual(json.loads(json.dumps(result)), result)

    def test_lint_separates_informational_mode_from_unreachable_warning(self):
        simple = "a sends b"
        self.assertEqual(lint_text(simple), [])
        self.assertEqual(diagnose_text(simple)[0]["code"], "legacy-mode")
        self.assertEqual(diagnose_text("language 1\n" + simple), [])
        source = "a sends b\na:\n  sends c"
        diagnostics = diagnose_text(source)
        self.assertTrue(any(d["code"] == "legacy-promotion" for d in diagnostics))
        warning = next(d for d in diagnostics if d["severity"] == "warning")
        self.assertEqual(warning["source"]["line"], 1)
        self.assertEqual(warning["covered_by"][0]["source"]["line"], 2)
        self.assertEqual(len(lint_text(source)), 1)

    def test_partial_units_and_unknown_predicates_do_not_warn(self):
        for source in ("a:\n  sends c\na, b sends d", "when app ^[[:alnum:]]+$:\n  a sends b\na sends c"):
            self.assertEqual(lint_text(source), [])

    def test_legacy_promotion_reasons_name_the_constructs(self):
        source = """devices:
  remote:
    id 1:2
    ignores modifiers
when device remote:
  a sends b
c, d sends e
f1 enables @layer
any+f2 sends a
"""
        reasons = {d["reason"] for d in diagnose_text(source) if d["code"] == "legacy-promotion"}
        self.assertEqual(reasons, {"inherited ignores modifiers", "comma-separated triggers", "enables verb", "any+ trigger prefix"})

    def test_promotion_uses_parsed_actions_instead_of_names_or_payload_words(self):
        cases = [
            ("actions:\n  halt:\n    sends b\na triggers halt\n", "triggers verb"),
            ("actions:\n  lazy:\n    sends b\na triggers lazy\n", "triggers verb"),
            ("when device 1:2:\n  swaps left_command left_option\n", "scoped swaps"),
            ("a sends left_shift lazy", "lazy/halt action attributes"),
            ("devices:\n  remote:\n    id 1:2\n    ignores modifiers\nwhen device remote:\n  a runs echo halt works\n",
             "inherited ignores modifiers"),
        ]
        for source, expected in cases:
            with self.subTest(source=source):
                reasons = {d["reason"] for d in diagnose_text(source) if d["code"] == "legacy-promotion"}
                self.assertEqual(reasons, {expected})


class ExplanationCliTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / ".build" / "test-explain"
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "example.kb"
        self.path.write_text(SAMPLE.read_text())

    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                cli.main(args)
                code = 0
            except SystemExit as error:
                code = error.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_explain_cli_supplies_only_explicit_facts_and_never_executes(self):
        with mock.patch.object(cli.subprocess, "run", side_effect=AssertionError("execute")):
            code, output, error = self.invoke(["--explain", str(self.path), "--key", "volume_up", "--json"])
        self.assertEqual((code, error), (0, ""))
        result = json.loads(output)
        self.assertEqual(result["supplied"], {"event": {"consumer_key_code": "volume_increment"}, "context": {}})
        self.assertFalse(result["winner_definitive"])
        code, output, _ = self.invoke(["--explain", str(self.path), "--key", "volume_up", "--modifiers", "none", "--device", "remote", "--device-enabled", "yes", "--app", "com.apple.Terminal", "--var", "scroll=1", "--json"])
        result = json.loads(output)
        self.assertEqual((code, result["winner_index"]), (0, 0))
        self.assertEqual(result["supplied"]["context"]["device"], {"vendor_id": 1, "product_id": 2})

    def test_explain_cli_keeps_warnings_in_both_output_formats(self):
        self.path.write_text("language 1\na send b\n")
        args = ["--explain", str(self.path), "--key", "a", "--modifiers", "none", "--device-enabled", "yes"]
        code, output, error = self.invoke(args)
        self.assertEqual((code, error), (0, ""))
        self.assertIn("warning [source-warning]: line 2: unrecognized line: a send b", output)
        code, output, error = self.invoke(args + ["--json"])
        self.assertEqual((code, error), (0, ""))
        result = json.loads(output)
        self.assertEqual(result["status"], "no_match")
        self.assertIn("unrecognized line: a send b", result["diagnostics"][0]["message"])
        self.assertEqual(result["diagnostics"][0]["source"]["line"], 2)

    def test_context_file_conflicts_bad_flags_and_invalid_values_fail(self):
        context = self.path.with_suffix(".json")
        context.write_text(json.dumps({"device_enabled": False, "variables": {"scroll": 1}}))
        cases = [["--key", "a"], ["--json"],
                 ["--explain", "--modifiers", "shift"],
                 ["--explain", "--var", "scroll"],
                 ["--explain", "--var", "scroll=1", "--var", "scroll=0"],
                 ["--explain", "--var", "scroll=[]"],
                 ["--explain", "--key", "pg_up"],
                 ["--explain", "--device", "missing"],
                 ["--explain", "--context", str(context), "--device-enabled", "yes"]]
        for args in cases:
            with self.subTest(args=args):
                code, output, error = self.invoke([str(self.path)] + args)
                self.assertEqual((code, output), (2, ""))
                self.assertNotIn("Traceback", error)

    def test_lint_mode_notice_is_success_but_shadow_warning_exits_one(self):
        self.path.write_text("a sends b")
        code, output, error = self.invoke(["--lint", str(self.path)])
        self.assertEqual((code, output), (0, ""))
        self.assertIn("info [legacy-mode]", error)
        self.path.write_text("a sends b\na:\n  sends c")
        code, output, error = self.invoke(["--lint", str(self.path), "--json"])
        self.assertEqual((code, error), (1, ""))
        self.assertTrue(any(d["code"] == "shadowed-unit" for d in json.loads(output)["diagnostics"]))


if __name__ == "__main__":
    unittest.main()
