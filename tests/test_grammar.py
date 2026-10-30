import os
import textwrap
import unittest
from unittest import mock

from karabiner_dsl.pipeline import compile_text
from karabiner_dsl.lint import diagnose_text
from karabiner_dsl.decompile import decompile_json_object
from karabiner_dsl.keys import HELPERS_ROOT_VARIABLE


# Helper features need an install directory. The output only names it.
EXAMPLE_HELPERS_ROOT = "/example/karabiner-helpers"


def setUpModule():
    environment = mock.patch.dict(os.environ, {HELPERS_ROOT_VARIABLE: EXAMPLE_HELPERS_ROOT})
    environment.start()
    unittest.addModuleCleanup(environment.stop)


def compile_source(source, *, allow_shadow=False):
    source = textwrap.dedent(source).strip() + "\n"
    warnings = [item["message"] for item in diagnose_text(source)
                if item["severity"] == "warning"
                and not (allow_shadow and item["code"].startswith("shadowed-"))]
    if warnings:
        raise AssertionError(warnings)
    return compile_text(source)["profiles"][0]


def manipulators(source, *, allow_shadow=False):
    return [m for rule in compile_source(source, allow_shadow=allow_shadow)["complex_modifications"]["rules"]
            for m in rule["manipulators"]]


class GrammarTests(unittest.TestCase):
    def test_all_registry_device_kinds(self):
        for kind, flags in (("keyboard", {"is_keyboard": True}),
                            ("pointing", {"is_keyboard": False, "is_pointing_device": True}),
                            ("keyboard+pointing", {"is_keyboard": True, "is_pointing_device": True})):
            with self.subTest(kind=kind):
                profile = compile_source(f"devices:\n  pad:\n    id 1:2\n    kind {kind}\n    enabled")
                self.assertEqual(profile["devices"][0]["identifiers"], {"vendor_id": 1, "product_id": 2, **flags})

    def test_device_registry_merges_blocks_and_all_properties(self):
        profile = compile_source("""
            devices:
              keyboard:
                enabled
                swaps right_command right_option
                id 1:2
                kind keyboard+pointing
              remote:
                id 3:4
                ignored
            devices:
              keyboard:
                keys:
                  one = keypad_1
            when device keyboard:
              a sends b
        """)
        keyboard, remote = profile["devices"]
        self.assertEqual(keyboard["identifiers"], {
            "vendor_id": 1, "product_id": 2, "is_keyboard": True, "is_pointing_device": True})
        self.assertFalse(keyboard["ignore"])
        self.assertTrue(remote["ignore"])
        self.assertEqual(keyboard["simple_modifications"], [
            {"from": {"key_code": "right_command"}, "to": [{"key_code": "right_option"}]},
            {"from": {"key_code": "right_option"}, "to": [{"key_code": "right_command"}]},
        ])

    def test_named_device_references_resolve_forward_and_in_exclusions(self):
        actual = manipulators("""
            when device external_keyboard except main_keyboard:
              any+left_option sends left_command
            when device presenter and @scroll:
              volume_up scrolls down 32
            devices:
              main_keyboard:
                id 1:2
              presenter:
                id 3:4
        """)
        self.assertEqual(actual[0]["conditions"], [
            {"type": "device_if", "identifiers": [{"is_keyboard": True, "is_built_in_keyboard": False}]},
            {"type": "device_unless", "identifiers": [{"vendor_id": 1, "product_id": 2}]},
        ])
        self.assertEqual(actual[1]["conditions"], [
            {"type": "device_if", "identifiers": [{"vendor_id": 3, "product_id": 4}]},
            {"type": "variable_if", "name": "scroll", "value": 1},
        ])
        self.assertEqual(manipulators("when device_except 1:2:\n  a sends b")[0]["conditions"],
                         [{"type": "device_unless", "identifiers": [{"vendor_id": 1, "product_id": 2}]}])

    def test_legacy_device_aliases_and_product_name_selectors_still_work(self):
        self.assertEqual(manipulators("device presenter 1:2\nwhen device presenter\n  a sends b")[0]["conditions"],
                         [{"type": "device_if", "identifiers": [{"vendor_id": 1, "product_id": 2}]}])
        self.assertEqual(manipulators("when device LegacyKeyboard:\n  a sends b")[0]["conditions"],
                         [{"type": "device_if", "identifiers": [{"product_name": "LegacyKeyboard"}]}])

    def test_macro_expansion_is_ordered_recursive_and_does_not_mutate_calls(self):
        result = manipulators("""
            page_up triggers submit
            page_down:
              triggers dictate
              sends return
              after up triggers dictate
            actions:
              submit:
                triggers dictate
                wait 2000ms
                sends return
              dictate:
                sends left_control
                sends left_control
        """)
        taps = [{"key_code": "left_control"}, {"key_code": "left_control"}]
        self.assertEqual(result[0]["to"], taps + [
            {"key_code": "vk_none", "hold_down_milliseconds": 2000}, {"key_code": "return_or_enter"}])
        self.assertEqual(result[1]["to"], taps + [{"key_code": "return_or_enter"}])
        self.assertEqual(result[1]["to_after_key_up"], taps)

    def test_unknown_duplicate_and_cyclic_macros_fail_clearly(self):
        for source, message in [
            ("a triggers missing", "Unknown action: missing"),
            ("actions:\n  a:\n    triggers b\n  b:\n    triggers a", "Recursive action: a -> b -> a"),
            ("actions:\n  a:\n    sends b\n  a:\n    sends c", "Duplicate action: a"),
            ("actions:\n  a:\n    mystery b", "invalid action step"),
        ]:
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, message):
                compile_text(source)

    def test_basic_is_optional_for_blocks_and_inline_rules(self):
        plain = "a, b:\n  sends c\n  after up sends d\n  alone timeout 300ms"
        self.assertEqual(compile_source(plain), compile_source("basic " + plain))
        self.assertEqual(manipulators("a clicks middle"), manipulators("basic a clicks middle"))

    def test_all_pointer_directions_and_buttons_work_inline_and_in_blocks(self):
        for verb, default in (("scrolls", 32), ("moves", 1536)):
            for direction in ("up", "down", "left", "right"):
                for suffix, amount in ((" 17", 17), ("", default)):
                    action = f"{verb} {direction}{suffix}"
                    with self.subTest(action=action):
                        inline = manipulators(f"a {action}")
                        self.assertEqual(inline, manipulators(f"a:\n  {action}"))
                        axis = ("vertical_wheel" if direction in ("up", "down") else "horizontal_wheel") if verb == "scrolls" else ("y" if direction in ("up", "down") else "x")
                        self.assertEqual(inline[0]["to"], [{"mouse_key": {axis: -amount if direction in ("up", "left") else amount}}])
        for button, code in (("left", "button1"), ("right", "button2"), ("middle", "button3")):
            with self.subTest(button=button):
                self.assertEqual(manipulators(f"a clicks {button}")[0]["to"], [{"pointing_button": code}])
        self.assertEqual(manipulators("a sends nothing")[0]["to"], [{"key_code": "vk_none"}])

    def test_boolean_verbs_inline_and_in_action_blocks(self):
        for verb, value, key_up in (("enables", 1, None), ("disables", 0, None), ("holds", 1, 0)):
            expected = {"name": "mode", "value": value}
            if key_up is not None:
                expected["key_up_value"] = key_up
            for source in (f"a {verb} @mode", f"a:\n  {verb} @mode"):
                with self.subTest(source=source):
                    self.assertEqual(manipulators(source)[0]["to"], [{"set_variable": expected}])

    def test_legacy_single_step_actions_share_the_inline_parser(self):
        for action in ("set @mode 2 until key up 0", "wait 50ms", "tracks pointer", "right-clicks current"):
            with self.subTest(action=action):
                self.assertEqual(manipulators(f"a {action}"), manipulators(f"a:\n  {action}"))

    def test_truth_checks_negative_spellings_and_optional_colons(self):
        for expression, value in (("@mode", 1), ("not @mode", 0), ("no @mode", 0), ("!@mode", 0),
                                  ("@mode is 1", 1), ("var mode is 2", 2), ("variable mode -1", -1)):
            for colon in ("", ":"):
                with self.subTest(expression=expression, colon=colon):
                    self.assertEqual(manipulators(f"when {expression}{colon}\n  a sends b")[0]["conditions"],
                                     [{"type": "variable_if", "name": "mode", "value": value}])
        self.assertEqual(manipulators("when app Codex:\n  a sends b"), manipulators("when app Codex\n  a sends b"))

    def test_compound_conditions_match_nested_legacy_conditions_in_order(self):
        for expression, nested in (
            ("device 1:2 and @layer", "device 1:2\n  when @layer is 1"),
            ("device 1:2 and not @layer", "device 1:2\n  when @layer is 0"),
            ("device 1:2 unless @t9, @neru, @nav", "device 1:2\n  when @t9 is 0\n    when @neru is 0\n      when @nav is 0"),
            ("app Codex and device 1:2 unless @nav", "app Codex\n  when device 1:2\n    when @nav is 0"),
        ):
            indent = " " * (2 * (nested.count("\n") + 1))
            with self.subTest(expression=expression):
                self.assertEqual(manipulators(f"when {expression}:\n  a sends b"),
                                 manipulators(f"when {nested}\n{indent}a sends b"))
        result = manipulators('when app "Research and Notes" and not @busy:\n  a sends b')[0]
        self.assertEqual(result["conditions"][0]["bundle_identifiers"], [r".*\.Research and Notes.*"])

    def test_comma_chords_expand_independently_for_inline_and_block_rules(self):
        for source in ("a, cmd+b sends c", "a, cmd+b:\n  sends c"):
            with self.subTest(source=source):
                result = manipulators(source)
                self.assertEqual([m["from"] for m in result], [
                    {"key_code": "a"},
                    {"key_code": "b", "modifiers": {"mandatory": ["left_command"]}},
                    {"key_code": "b", "modifiers": {"mandatory": ["right_command"]}},
                ])
                self.assertEqual([m["to"] for m in result], [[{"key_code": "c"}]] * 3)

    def test_any_prefix_is_per_chord_and_combines_with_concrete_modifiers(self):
        result = manipulators("any+left_option+tab, any+left_shift+dictation, escape sends nothing")
        self.assertEqual(result[0]["from"], {"key_code": "tab", "modifiers": {"mandatory": ["left_option"], "optional": ["any"]}})
        self.assertEqual(result[1]["from"], {"consumer_key_code": "dictation", "modifiers": {"mandatory": ["left_shift"], "optional": ["any"]}})
        self.assertEqual(result[2]["from"], {"key_code": "escape"})
        self.assertEqual(manipulators("any+a:\n  sends b"), manipulators("a:\n  optional any\n  sends b"))

    def test_numpad_inline_verbs_macros_grouped_maps_and_aliases(self):
        profile = compile_source("""
            numpad pad:
              base:
                default:
                  new scrolls up 32
                  dictate triggers submit
                  any+left_option+backspace, return clicks middle
                  a, b:
                    triggers dictate
              mode neru:
                app_4, new, layer backtracks
            devices:
              pad:
                id 1:2
                keys:
                  new = keypad_hyphen
                  dictate = keypad_0
                  app_4 = backspace
                  layer = keypad_period
            actions:
              dictate:
                sends left_control
                sends left_control
              submit:
                triggers dictate
                wait 2000ms
                sends return
        """)
        rules = profile["complex_modifications"]["rules"]
        mode, base = rules
        self.assertEqual([m["from"]["key_code"] for m in mode["manipulators"]],
                         ["delete_or_backspace", "keypad_hyphen", "keypad_period"])
        mappings = base["manipulators"]
        self.assertEqual(mappings[0]["to"], [{"mouse_key": {"vertical_wheel": -32}}])
        self.assertEqual(mappings[1]["to"], [{"key_code": "left_control"}] * 2 + [
            {"key_code": "vk_none", "hold_down_milliseconds": 2000}, {"key_code": "return_or_enter"}])
        self.assertEqual(mappings[2]["from"], {"key_code": "delete_or_backspace", "modifiers": {"mandatory": ["left_option"], "optional": ["any"]}})
        self.assertEqual(mappings[3]["from"], {"key_code": "return_or_enter"})
        self.assertEqual([m["to"] for m in mappings[4:]], [[{"key_code": "left_control"}] * 2] * 2)
        self.assertEqual(mappings[0]["conditions"][0], {"type": "device_if", "identifiers": [{"vendor_id": 1, "product_id": 2}]})

    def test_numpad_legacy_passthrough_suffixes_stay_out_of_event_payloads(self):
        prefix = "devices:\n  pad:\n    id 1:2\nnumpad pad:\n  base:\n    default:\n"
        for mapping, expected, optional in (
            ("a sends b +shift", {"key_code": "b"}, ["shift"]),
            ("a sends cmd+b +ctrl +option", {"key_code": "b", "modifiers": ["command"]}, ["control", "option"]),
            ("a runs echo test +shift", {"shell_command": "echo test"}, ["shift"]),
            ("a opens Visual Studio Code +shift", {"shell_command": "open -a 'Visual Studio Code'"}, ["shift"]),
        ):
            with self.subTest(mapping=mapping):
                result = manipulators(prefix + "      " + mapping)[0]
                self.assertEqual(result["from"], {"key_code": "a", "modifiers": {"optional": optional}})
                self.assertEqual(result["to"], [expected])
        expanded = manipulators(prefix + "      hjkl sends arrows +shift")
        self.assertEqual([m["from"] for m in expanded], [
            {"key_code": key, "modifiers": {"optional": ["shift"]}} for key in "hjkl"])
        self.assertEqual([m["to"] for m in expanded], [[{"key_code": key}] for key in ("left_arrow", "down_arrow", "up_arrow", "right_arrow")])
        expanded = manipulators(prefix + "      a, b scrolls up 32 +shift")
        self.assertEqual([m["from"] for m in expanded], [
            {"key_code": key, "modifiers": {"optional": ["shift"]}} for key in "ab"])

    def test_key_aliases_normalize_sources_and_targets(self):
        for alias, canonical in (("backspace", "delete_or_backspace"), ("return", "return_or_enter"), ("nothing", "vk_none")):
            with self.subTest(alias=alias):
                self.assertEqual(manipulators(f"{alias} sends {alias}"), manipulators(f"{canonical} sends {canonical}"))
                prefix = "devices:\n  pad:\n    id 1:2\nnumpad pad:\n  base:\n    default:\n"
                self.assertEqual(manipulators(prefix + f"      {alias} sends {alias}"),
                                 manipulators(prefix + f"      {canonical} sends {canonical}"))

    def test_reverse_roundtrip_retains_horizontal_scroll_middle_click_and_modifier_families(self):
        for source in ("a scrolls left 17", "a clicks middle", "cmd+shift+a:\n  sends b\n  wait 30ms\n  sends c",
                       "when device 1:2:\n  cmd+shift+a sends b"):
            with self.subTest(source=source):
                compiled = compile_text(source)
                self.assertEqual(compiled, compile_text(decompile_json_object(compiled)))

    def test_grouped_device_override_precedes_numpad_default_and_global_fallback(self):
        source = """
            a sends c
            when device pad and not @nav:
              a, b sends b
            devices:
              pad:
                id 1:2
            numpad pad:
              base:
                default:
                  a sends d
        """
        self.assertEqual([m["to"] for m in manipulators(source)],
                         [[{"key_code": key}] for key in ("b", "b", "d", "c")])

    def test_legacy_scoped_singletons_retain_fallback_priority(self):
        for scope in ("device 1:2", "app Codex", "@mode is 1"):
            with self.subTest(scope=scope):
                source = f"when {scope}\n  a sends b\nbasic a:\n  sends c"
                self.assertEqual([m["to"] for m in manipulators(source, allow_shadow=True)],
                                 [[{"key_code": "c"}], [{"key_code": "b"}]])
        source = "devices:\n  pad:\n    id 1:2\nwhen device 1:2\n  a sends b\nnumpad pad:\n  base:\n    default:\n      a sends c"
        self.assertEqual([m["to"] for m in manipulators(source)],
                         [[{"key_code": "c"}], [{"key_code": "b"}]])


if __name__ == "__main__":
    unittest.main()
