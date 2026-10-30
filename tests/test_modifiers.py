import os
import unittest
from unittest import mock

from karabiner_dsl.pipeline import compile_text
from karabiner_dsl.decompile import decompile_json_object
from tests.test_grammar import compile_source, manipulators
from karabiner_dsl.keys import HELPERS_ROOT_VARIABLE


# Helper features need an install directory. The output only names it.
EXAMPLE_HELPERS_ROOT = "/example/karabiner-helpers"


def setUpModule():
    environment = mock.patch.dict(os.environ, {HELPERS_ROOT_VARIABLE: EXAMPLE_HELPERS_ROOT})
    environment.start()
    unittest.addModuleCleanup(environment.stop)


DEVICES = """devices:
  gadget:
    id 1:2
    ignores modifiers
  keyboard:
    id 3:4
"""


class ModifierDefaultTests(unittest.TestCase):
    def test_device_defaults_inherit_through_nested_conditions_and_macros(self):
        source = DEVICES + """actions:
  dictate:
    sends left_control
    sends left_control
when app Codex:
  when device gadget and not @layer:
    left_option+tab, left_shift+dictation sends nothing
    up_arrow triggers dictate
    left_option holds @layer
"""
        result = manipulators(source)
        self.assertEqual(len(result), 4)
        self.assertEqual(result[0]["from"], {"key_code": "tab", "modifiers": {"mandatory": ["left_option"], "optional": ["any"]}})
        self.assertEqual(result[1]["from"], {"consumer_key_code": "dictation", "modifiers": {"mandatory": ["left_shift"], "optional": ["any"]}})
        self.assertEqual(result[2]["to"], [{"key_code": "left_control"}] * 2)
        self.assertEqual(result[3]["to"], [{"set_variable": {"name": "layer", "value": 1, "key_up_value": 0}}])
        for mapping in result:
            self.assertEqual(mapping["from"]["modifiers"]["optional"], ["any"])
            self.assertEqual(mapping["conditions"][-1], {"type": "variable_if", "name": "layer", "value": 0})

    def test_named_raw_forward_and_merged_device_definitions_share_defaults(self):
        for selector in ("gadget", "1:2"):
            source = f"when device {selector}:\n  a sends b\n" + DEVICES
            with self.subTest(selector=selector):
                self.assertEqual(manipulators(source)[0]["from"], {"key_code": "a", "modifiers": {"optional": ["any"]}})
        source = "devices:\n  gadget:\n    id 1:2\nwhen device gadget:\n  a sends b\ndevices:\n  gadget:\n    ignores modifiers"
        self.assertEqual(manipulators(source)[0]["from"]["modifiers"]["optional"], ["any"])

    def test_default_is_scoped_to_positive_device_matches(self):
        for scope in ("when device keyboard:", "when device_except gadget:", "when app Codex:"):
            with self.subTest(scope=scope):
                self.assertNotIn("modifiers", manipulators(DEVICES + scope + "\n  a sends b")[0]["from"])
        self.assertNotIn("modifiers", manipulators(DEVICES + "a sends b")[0]["from"])

    def test_mixed_device_list_keeps_each_devices_modifier_policy(self):
        for mapping in ("a sends b", "a:\n    sends b"):
            result = manipulators(DEVICES + "when device gadget, keyboard:\n  " + mapping)
            self.assertEqual(len(result), 2)
            indexed = {m["conditions"][0]["identifiers"][0]["vendor_id"]: m for m in result}
            self.assertEqual(indexed[1]["from"]["modifiers"]["optional"], ["any"])
            self.assertNotIn("modifiers", indexed[3]["from"])

    def test_explicit_optional_modifiers_and_none_override_device_defaults(self):
        for optional, expected in (("shift", {"optional": ["shift"]}), ("none", {})):
            source = DEVICES + f"when device gadget:\n  a:\n    optional {optional}\n    sends b"
            with self.subTest(optional=optional):
                self.assertEqual(manipulators(source)[0]["from"].get("modifiers", {}), expected)
        source = DEVICES + "when device gadget:\n  a sends b +ctrl"
        self.assertEqual(manipulators(source)[0]["from"]["modifiers"], {"optional": ["control"]})

    def test_inherited_default_keeps_the_old_any_prefix_priority(self):
        before = DEVICES + "when device gadget:\n  any+a sends b\na:\n  sends c"
        after = before.replace("any+a", "a")
        self.assertEqual(compile_text(before), compile_text(after))
        self.assertEqual([m["to"] for m in manipulators(after)], [[{"key_code": key}] for key in "bc"])
        before = DEVICES + "a:\n  sends c\nwhen device gadget:\n  any+a sends b"
        self.assertEqual(compile_text(before), compile_text(before.replace("any+a", "a")))

    def test_plain_typing_device_rules_stay_in_the_legacy_fallback_tier(self):
        result = manipulators(DEVICES + "when device keyboard:\n  a sends b\na:\n  sends c", allow_shadow=True)
        self.assertEqual([m["to"] for m in result], [[{"key_code": key}] for key in "cb"])

    def test_modifier_to_modifier_mappings_default_to_any_without_losing_mandatory_modifiers(self):
        for source in ("left_option sends left_command", "left_option:\n  sends left_command", "option sends cmd"):
            with self.subTest(source=source):
                self.assertEqual(manipulators(source)[0]["from"], {"key_code": "left_option", "modifiers": {"optional": ["any"]}})
        source = "left_shift+left_option sends left_command"
        self.assertEqual(manipulators(source)[0]["from"]["modifiers"], {"mandatory": ["left_shift"], "optional": ["any"]})
        self.assertNotIn("modifiers", manipulators("spacebar sends left_shift")[0]["from"])
        self.assertNotIn("modifiers", manipulators("left_option:\n  sends left_control\n  sends left_control")[0]["from"])

    def test_explicit_modifier_mapping_overrides_are_preserved(self):
        result = manipulators("left_option:\n  optional none\n  sends left_command")
        self.assertNotIn("modifiers", result[0]["from"])
        result = manipulators("left_option sends left_command +shift")
        self.assertEqual(result[0]["from"]["modifiers"], {"optional": ["shift"]})

    def test_scoped_swaps_preserve_full_conditions_and_both_directions(self):
        source = """devices:
  main_keyboard:
    id 1:2
when device external_keyboard except main_keyboard:
  swaps left_command left_option
"""
        result = manipulators(source)
        self.assertEqual([m["from"] for m in result], [{"key_code": key, "modifiers": {"optional": ["any"]}} for key in ("left_command", "left_option")])
        self.assertEqual([m["to"] for m in result], [[{"key_code": key}] for key in ("left_option", "left_command")])
        for mapping in result:
            self.assertEqual(mapping["conditions"], [
                {"type": "device_if", "identifiers": [{"is_keyboard": True, "is_built_in_keyboard": False}]},
                {"type": "device_unless", "identifiers": [{"vendor_id": 1, "product_id": 2}]},
            ])
        self.assertNotIn("devices", compile_source(source))
        generic = manipulators("when device 1:2:\n  swaps command option")
        self.assertEqual([m["from"]["key_code"] for m in generic], ["left_command", "left_option", "right_command", "right_option"])
        for invalid in ("swaps command option", "when device 1:2:\n  swaps command left_option"):
            with self.assertRaises(ValueError):
                compile_text(invalid)

    def test_registry_swaps_keep_the_existing_simple_modification_shape(self):
        profile = compile_source("devices:\n  pad:\n    id 1:2\n    swaps left_command left_option\n    ignores modifiers")
        self.assertEqual(profile["devices"][0]["simple_modifications"], [
            {"from": {"key_code": "left_command"}, "to": [{"key_code": "left_option"}]},
            {"from": {"key_code": "left_option"}, "to": [{"key_code": "left_command"}]},
        ])

    def test_numpad_defaults_cover_generated_modes_and_explicit_mapping_overrides(self):
        for selector in ("gadget", "1:2"):
            source = DEVICES + f"""numpad {selector}:
  base:
    default:
      a sends b
      c:
        optional none
        sends d
      e sends f +shift
  mode neru:
    keypad_num_lock exits
"""
            with self.subTest(selector=selector):
                result = manipulators(source)
                indexed = {m["from"]["key_code"]: m for m in result}
                self.assertEqual(indexed["a"]["from"]["modifiers"]["optional"], ["any"])
                self.assertEqual(indexed["keypad_num_lock"]["from"]["modifiers"]["optional"], ["any"])
                self.assertNotIn("modifiers", indexed["c"]["from"])
                self.assertEqual(indexed["e"]["from"]["modifiers"]["optional"], ["shift"])

    def test_defaults_and_overrides_roundtrip_without_device_metadata(self):
        cases = [
            DEVICES + "when device gadget:\n  a sends b",
            DEVICES + "when device gadget, keyboard:\n  a sends b",
            "left_option sends left_command",
            "left_option:\n  optional none\n  sends left_command",
            "a+b together:\n  optional none\n  sends c",
            DEVICES + "numpad gadget:\n  base:\n    default:\n      a sends b\n      left_option:\n        optional none\n        sends left_command",
        ]
        for source in cases:
            with self.subTest(source=source):
                compiled = compile_text(source)
                self.assertEqual(compiled, compile_text(decompile_json_object(compiled)))

    def test_grouped_hold_mappings_preserve_inherited_optional_any(self):
        source = DEVICES + "hyper caps_lock\nwhen device gadget:\n  $:\n    hjkl sends arrows"
        compiled = compile_text(source)
        restored = decompile_json_object(compiled)
        self.assertIn("any+hjkl sends arrows", restored)
        self.assertEqual(compiled, compile_text(restored))

    def test_grouping_keeps_distinct_optional_modifier_policies(self):
        for first, rest in (("any+", ""), ("", "any+")):
            source = "hyper caps_lock\n$:\n" + "\n".join(
                f"  {first if index == 0 else rest}{key} sends {target}"
                for index, (key, target) in enumerate(zip("hjkl", ("left_arrow", "down_arrow", "up_arrow", "right_arrow")))
            )
            with self.subTest(first=first, rest=rest):
                compiled = compile_text(source)
                restored = decompile_json_object(compiled)
                self.assertNotIn("hjkl sends arrows", restored)
                self.assertEqual(compiled, compile_text(restored))


if __name__ == "__main__":
    unittest.main()
