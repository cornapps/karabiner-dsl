"""Compare all runtime fields and retain priority for overlapping inputs.

Karabiner rule descriptions and grouping are presentation. Reordering two
manipulators is safe only when their input events cannot overlap. Unlike a
sorted JSON comparison, this test catches changes to first-match priority,
including optional-any versus a chord with mandatory modifiers.
"""

from collections import Counter, defaultdict, deque
import copy
import json
import os
from pathlib import Path
import unittest
from unittest import mock

from karabiner_dsl.keys import HELPERS_ROOT_VARIABLE, NUMPAD_FALLBACK_PYTHON, NUMPAD_PREFERRED_PYTHON
from karabiner_dsl.pipeline import compile_file, compile_text
from karabiner_dsl.lint import lint_file


# Helper features need an install directory. The output only names it.
EXAMPLE_HELPERS_ROOT = "/example/karabiner-helpers"


def setUpModule():
    environment = mock.patch.dict(os.environ, {HELPERS_ROOT_VARIABLE: EXAMPLE_HELPERS_ROOT})
    environment.start()
    unittest.addModuleCleanup(environment.stop)


FIXTURES = Path(__file__).parent / "fixtures"
NUMPAD_PYTHON = NUMPAD_PREFERRED_PYTHON if NUMPAD_PREFERRED_PYTHON.exists() else NUMPAD_FALLBACK_PYTHON


def signature(value):
    return json.dumps(value, sort_keys=True)


def option_a_expected(before):
    expected = copy.deepcopy(before)
    changed = []
    for profile in expected["profiles"]:
        for rule in profile["complex_modifications"]["rules"]:
            for manipulator in rule["manipulators"]:
                presenter = any(condition.get("type") == "device_if"
                                and {"vendor_id": 1, "product_id": 1} in condition.get("identifiers", [])
                                for condition in manipulator.get("conditions", []))
                event = manipulator["from"]
                if presenter and "optional" not in event.get("modifiers", {}):
                    if not (event.get("key_code") == "tab" or event.get("consumer_key_code") in ("volume_increment", "volume_decrement")):
                        raise AssertionError(f"Unapproved modifier-default change: {event}")
                    event.setdefault("modifiers", {})["optional"] = ["any"]
                    changed.append(event)
    if len(changed) != 9:
        raise AssertionError(f"Expected exactly nine authorized presenter changes, got {len(changed)}")
    return expected


def inputs_overlap(left, right):
    if {k: v for k, v in left.items() if k != "modifiers"} != {k: v for k, v in right.items() if k != "modifiers"}:
        return False
    modifiers = [left.get("modifiers", {}), right.get("modifiers", {})]
    required = set().union(*(set(m.get("mandatory", [])) for m in modifiers))
    return all("any" in m.get("optional", []) or required <= set(m.get("mandatory", []) + m.get("optional", []))
               for m in modifiers)


def assert_semantically_identical(test, before, after):
    before, after = copy.deepcopy(before), copy.deepcopy(after)
    test.assertEqual(len(before["profiles"]), len(after["profiles"]))
    for old_profile, new_profile in zip(before["profiles"], after["profiles"]):
        old_rules = old_profile["complex_modifications"].pop("rules")
        new_rules = new_profile["complex_modifications"].pop("rules")
        test.assertTrue(all(set(rule) == {"description", "manipulators"} for rule in old_rules + new_rules))
        old = [m for rule in old_rules for m in rule["manipulators"]]
        new = [m for rule in new_rules for m in rule["manipulators"]]
        test.assertEqual(Counter(map(signature, old)), Counter(map(signature, new)),
                         "Every manipulator, condition, action, timing and count must be unchanged")
        positions = defaultdict(deque)
        for index, manipulator in enumerate(old):
            positions[signature(manipulator)].append(index)
        order = [positions[signature(m)].popleft() for m in new]
        for index, previous_index in enumerate(order):
            for earlier in order[:index]:
                if earlier > previous_index:
                    test.assertFalse(inputs_overlap(old[earlier]["from"], old[previous_index]["from"]),
                                     f"Priority changed for overlapping inputs at golden positions {earlier}, {previous_index}")
    # Includes profile settings, device order, flags and simple modifications.
    test.assertEqual(before, after)


class GoldenTests(unittest.TestCase):
    def golden(self, name="showcase_golden.json"):
        # Fill in this machine's helper checkout and numpad Python.
        source = (FIXTURES / name).read_text()
        return json.loads(source.replace("__HELPERS_ROOT__", EXAMPLE_HELPERS_ROOT)
                          .replace("__NUMPAD_PYTHON__", str(NUMPAD_PYTHON)))

    def test_original_config_still_compiles_to_exact_golden_json(self):
        self.assertEqual(self.golden(), compile_text((FIXTURES / "showcase.original.kb").read_text()))

    def test_migrated_config_is_semantically_identical_to_golden(self):
        self.assertEqual(lint_file(FIXTURES / "showcase.kb"), [])
        assert_semantically_identical(self, option_a_expected(self.golden()), compile_file(FIXTURES / "showcase.kb"))

    def test_option_a_merged_checkpoint_and_exact_allowed_delta(self):
        golden = self.golden("showcase_before_golden.json")
        self.assertEqual(golden, compile_text((FIXTURES / "showcase.before.kb").read_text()))
        assert_semantically_identical(self, option_a_expected(golden), compile_file(FIXTURES / "showcase.kb"))
        text = (FIXTURES / "showcase.kb").read_text()
        self.assertNotIn("any+", text)
        self.assertNotIn("optional any", text)
        self.assertIn("when device macro_pad:", text)
        self.assertIn("swaps left_command left_option", text)

    def test_comparison_rejects_priority_changes_and_timing_or_device_changes(self):
        before = compile_text("any+a:\n  sends b\na sends c")
        reordered = copy.deepcopy(before)
        reordered["profiles"][0]["complex_modifications"]["rules"].reverse()
        with self.assertRaises(AssertionError):
            assert_semantically_identical(self, before, reordered)
        changed = copy.deepcopy(self.golden())
        changed["profiles"][0]["devices"][0]["ignore"] = True
        with self.assertRaises(AssertionError):
            assert_semantically_identical(self, self.golden(), changed)
        changed = copy.deepcopy(before)
        changed["profiles"][0]["complex_modifications"]["rules"][0]["manipulators"][0]["to"][0]["hold_down_milliseconds"] = 1
        with self.assertRaises(AssertionError):
            assert_semantically_identical(self, before, changed)

    def test_comparison_allows_reordering_distinct_keys_only(self):
        before = compile_text("a sends b\nc sends d")
        after = copy.deepcopy(before)
        after["profiles"][0]["complex_modifications"]["rules"].reverse()
        assert_semantically_identical(self, before, after)


if __name__ == "__main__":
    unittest.main()
