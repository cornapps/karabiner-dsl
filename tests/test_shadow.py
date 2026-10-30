"""Independent native-JSON proofs and source-unit completeness regressions."""

import copy
from dataclasses import replace
import itertools
import json
import os
from pathlib import Path
import unittest
from unittest import mock
import warnings

from karabiner_dsl.compiler import Compiler
from karabiner_dsl.matching import match_manipulator, pick_winner
from karabiner_dsl.origins import ManipulatorOrigin, Origin, SourceLocation
from karabiner_dsl.parser import Parser
from karabiner_dsl.shadow import find_shadowed, manipulator_covers
from karabiner_dsl.keys import HELPERS_ROOT_VARIABLE


# Helper features need an install directory. The output only names it.
EXAMPLE_HELPERS_ROOT = "/example/karabiner-helpers"


def setUpModule():
    environment = mock.patch.dict(os.environ, {HELPERS_ROOT_VARIABLE: EXAMPLE_HELPERS_ROOT})
    environment.start()
    unittest.addModuleCleanup(environment.stop)


KEY = {"key_code": "a", "modifiers": []}
ENABLED = {"device_enabled": True}
TERMINAL = r"^com\.apple\.Terminal$"
EDITOR = r"^org\.example\.Editor$"


def mapping(*conditions, key="a", modifiers=None):
    source = {"key_code": key}
    if modifiers is not None:
        source["modifiers"] = modifiers
    return {"type": "basic", "from": source, "conditions": list(conditions),
            "to": [{"key_code": "b"}]}


def app(*patterns, negative=False):
    return {"type": "frontmost_application_unless" if negative else "frontmost_application_if",
            "bundle_identifiers": list(patterns)}


def device(*identifiers, negative=False):
    return {"type": "device_unless" if negative else "device_if", "identifiers": list(identifiers)}


def variable(value, name="mode", negative=False):
    return {"type": "variable_unless" if negative else "variable_if", "name": name, "value": value}


def origin(index, unit, line=None):
    return ManipulatorOrigin(index, 0, index,
                             Origin(SourceLocation(line or index + 1, path="example.kb"), unit),
                             "test", 0)


def compiled(source):
    compiler = Compiler(Parser(source, source_path="example.kb"))
    result = compiler.compile()
    flat = [m for rule in result["profiles"][0]["complex_modifications"]["rules"]
            for m in rule["manipulators"]]
    return flat, compiler.origins


class CoverageTests(unittest.TestCase):
    def assert_covered(self, earlier, later, *, event=None, context=None):
        self.assertIs(manipulator_covers(earlier, later), True)
        event, context = event or KEY, ENABLED | (context or {})
        self.assertEqual(match_manipulator(later, event, context)["status"], "match")
        self.assertEqual(pick_winner([earlier, later], event, context)["winner_index"], 0)

    def assert_counterexample(self, earlier, later, *, event=None, context=None):
        self.assertIsNot(manipulator_covers(earlier, later), True)
        event, context = event or KEY, ENABLED | (context or {})
        self.assertEqual(match_manipulator(earlier, event, context)["status"], "no_match")
        self.assertEqual(pick_winner([earlier, later], event, context)["winner_index"], 1)

    def test_same_event_selector_and_trigger_required(self):
        self.assert_covered(mapping(), mapping())
        self.assertIs(manipulator_covers(mapping(key="b"), mapping()), False)
        consumer = mapping()
        consumer["from"] = {"consumer_key_code": "a"}
        self.assertIs(manipulator_covers(consumer, mapping()), False)
        for field in ("consumer_key_code", "pointing_button"):
            rule = mapping()
            rule["from"] = {field: "test"}
            self.assert_covered(rule, rule, event={field: "test", "modifiers": []})

    def test_optional_any_covers_none_and_chords_but_reverse_has_witness(self):
        broad = mapping(modifiers={"optional": ["any"]})
        for narrow in (mapping(), mapping(modifiers={"mandatory": ["shift"]}),
                       mapping(modifiers={"mandatory": ["left_command"], "optional": ["fn"]})):
            self.assertIs(manipulator_covers(broad, narrow), True)
        self.assert_counterexample(mapping(), broad, event={**KEY, "modifiers": ["fn"]})
        self.assertIs(manipulator_covers(mapping(), broad), False)

    def test_generic_families_cover_concrete_sides_including_both(self):
        for family in ("shift", "control", "command", "option"):
            generic = mapping(modifiers={"mandatory": [family]})
            left = mapping(modifiers={"mandatory": ["left_" + family]})
            right = mapping(modifiers={"mandatory": ["right_" + family]})
            both = mapping(modifiers={"mandatory": ["left_" + family, "right_" + family]})
            for narrow, actual in ((left, ["left_" + family]), (right, ["right_" + family]),
                                   (both, ["left_" + family, "right_" + family])):
                self.assert_covered(generic, narrow, event={**KEY, "modifiers": actual})
            self.assert_counterexample(left, generic, event={**KEY, "modifiers": ["right_" + family]})
            self.assert_counterexample(both, generic, event={**KEY, "modifiers": ["left_" + family]})

    def test_optional_families_and_mandatory_any_requirement(self):
        optional = mapping(modifiers={"optional": ["shift"]})
        mandatory = mapping(modifiers={"mandatory": ["shift"], "optional": ["any"]})
        self.assert_covered(optional, mapping())
        self.assert_counterexample(optional, mapping(modifiers={"optional": ["any"]}),
                                   event={**KEY, "modifiers": ["left_command"]})
        self.assert_counterexample(mandatory, optional)
        self.assert_covered(mandatory, mapping(modifiers={"mandatory": ["right_shift", "fn"]}),
                            event={**KEY, "modifiers": ["right_shift", "fn"]})

    def test_explicit_none_empty_lists_and_aliases(self):
        for spec in ({}, {"mandatory": []}, {"optional": []}, {"mandatory": [], "optional": []}):
            self.assert_covered(mapping(), mapping(modifiers=spec))
            self.assert_covered(mapping(modifiers=spec), mapping())
        for alias, native in (("left_alt", "left_option"), ("right_gui", "right_command")):
            self.assert_covered(mapping(modifiers={"mandatory": [alias]}),
                                mapping(modifiers={"mandatory": [native]}),
                                event={**KEY, "modifiers": [native]})

    def test_unconditional_covers_understood_restricted_conditions(self):
        narrow = mapping(device({"vendor_id": 7, "is_keyboard": True}),
                         app(TERMINAL), variable(1))
        context = {"device": {"vendor_id": 7, "is_keyboard": True},
                   "app_bundle": "com.apple.Terminal", "variables": {"mode": 1}}
        self.assert_covered(mapping(), narrow, context=context)
        self.assert_counterexample(narrow, mapping(), context={**context, "variables": {"mode": 0}})

    def test_and_conditions_can_only_narrow_eligibility(self):
        earlier = mapping(variable(1), app(TERMINAL, EDITOR))
        later = mapping(device({"vendor_id": 7}), app(TERMINAL), variable(1))
        context = {"device": {"vendor_id": 7}, "app_bundle": "com.apple.Terminal", "variables": {"mode": 1}}
        self.assert_covered(earlier, later, context=context)
        self.assert_counterexample(later, earlier, context={**context, "device": {"vendor_id": 8}})
        self.assertIsNone(manipulator_covers(mapping(variable(1, "other")), later))

    def test_app_or_alternatives_identity_and_only_safe_universals(self):
        self.assert_covered(mapping(app(TERMINAL, EDITOR)), mapping(app(TERMINAL)),
                            context={"app_bundle": "com.apple.Terminal"})
        self.assert_counterexample(mapping(app(TERMINAL)), mapping(app(TERMINAL, EDITOR)),
                                   context={"app_bundle": "org.example.Editor"})
        for universal in ("", ".*"):
            self.assert_covered(mapping(app(universal)), mapping(), context={"app_bundle": "anything\nelse"})
            self.assert_covered(mapping(app(universal)), mapping(app(EDITOR)),
                                context={"app_bundle": "org.example.Editor"})
        self.assertIsNone(manipulator_covers(mapping(app("^.*$")), mapping()))
        # Neither plausible overlap nor plausible disjointness is a regex proof.
        for earlier, later in ((r"^org\..*$", EDITOR), (TERMINAL, EDITOR), ("Editor", EDITOR)):
            self.assertIsNone(manipulator_covers(mapping(app(earlier)), mapping(app(later))))

    def test_negative_app_reverses_alternative_containment(self):
        narrow_exclusion = mapping(app(TERMINAL, negative=True))
        broad_exclusion = mapping(app(TERMINAL, EDITOR, negative=True))
        self.assert_covered(narrow_exclusion, broad_exclusion, context={"app_bundle": "org.example.Other"})
        self.assert_counterexample(broad_exclusion, narrow_exclusion, context={"app_bundle": "org.example.Editor"})
        self.assertIsNone(manipulator_covers(narrow_exclusion, mapping(app(EDITOR))))
        self.assertIsNone(manipulator_covers(mapping(app(TERMINAL)), narrow_exclusion))

    def test_device_subset_fields_interface_flags_and_or(self):
        earlier = mapping(device({"vendor_id": 7}, {"device_address": "abc"}))
        later = mapping(device({"vendor_id": 7, "product_id": 8, "is_keyboard": True, "is_consumer": False},
                               {"device_address": "abc", "location_id": 4, "is_pointing_device": True}))
        for actual in ({"vendor_id": 7, "product_id": 8, "is_keyboard": True, "is_consumer": False},
                       {"device_address": "abc", "location_id": 4, "is_pointing_device": True}):
            self.assert_covered(earlier, later, context={"device": actual})
        self.assert_counterexample(later, earlier, context={"device": {
            "vendor_id": 7, "product_id": 9, "device_address": "other"}})
        self.assertIsNone(manipulator_covers(mapping(device({"vendor_id": 7})), later))
        self.assertIsNone(manipulator_covers(mapping(device({"vendor_id": 7, "is_keyboard": True})),
                                           mapping(device({"vendor_id": 7, "is_consumer": True}))))

    def test_negative_device_containment_and_literal_disjointness(self):
        earlier = mapping(device({"vendor_id": 7, "product_id": 8}, negative=True))
        later = mapping(device({"vendor_id": 7}, negative=True))
        self.assert_covered(earlier, later, context={"device": {"vendor_id": 9, "product_id": 8}})
        self.assert_counterexample(later, earlier, context={"device": {"vendor_id": 7, "product_id": 9}})
        self.assert_covered(later, mapping(device({"vendor_id": 9, "is_keyboard": True})),
                            context={"device": {"vendor_id": 9, "is_keyboard": True}})
        self.assertIsNone(manipulator_covers(later, mapping(device({"product_id": 8}))))
        multiple = mapping(device({"vendor_id": 7}, {"is_keyboard": True}, negative=True))
        self.assertIsNone(manipulator_covers(multiple, mapping(device({"vendor_id": 9}))))
        self.assert_covered(multiple, mapping(device({"vendor_id": 9, "is_keyboard": False})),
                            context={"device": {"vendor_id": 9, "is_keyboard": False}})

    def test_variable_equality_negation_and_strict_types(self):
        for value in (0, 1, True, False, "1", ""):
            self.assert_covered(mapping(variable(value)), mapping(variable(value)),
                                context={"variables": {"mode": value}})
            self.assert_covered(mapping(variable(value, negative=True)), mapping(variable(value, negative=True)),
                                context={"variables": {"mode": "different"}})
        self.assert_covered(mapping(variable(1, negative=True)), mapping(variable(True)),
                            context={"variables": {"mode": True}})
        self.assert_counterexample(mapping(variable(1)), mapping(variable(True)),
                                   context={"variables": {"mode": True}})
        self.assertIsNone(manipulator_covers(mapping(variable(1)), mapping(variable(0, negative=True))))
        self.assertIsNone(manipulator_covers(mapping(variable(0)), mapping()))
        result = match_manipulator(mapping(variable(0)), KEY, ENABLED)
        self.assertEqual(result["status"], "unknown")
        self.assertIn("context.variables.mode", result["missing_facts"])

    def test_unsupported_regex_even_in_true_alternatives_is_unknown(self):
        patterns = ("[", "(?i)terminal", r"\Aterminal", r"\d+", "x++",
                    "[[:alnum:]]", "[[.A.]]", "[[=A=]]")
        for pattern, negative in itertools.product(patterns, (False, True)):
            with self.subTest(pattern=pattern, negative=negative), warnings.catch_warnings(record=True) as emitted:
                unsupported = mapping(app(pattern, ".*", negative=negative))
                self.assertIsNone(manipulator_covers(mapping(), unsupported))
                self.assertIsNone(manipulator_covers(unsupported, unsupported))
                self.assertEqual(find_shadowed([mapping(), unsupported]), [])
                self.assertEqual(emitted, [])

    def test_unknown_predicates_never_produce_coverage_in_either_direction(self):
        unknown = [None, {}, {**mapping(), "type": "mouse_basic"}, {**mapping(), "future": True},
                   {**mapping(), "conditions": {}}, mapping({"type": "event_changed_if", "value": True}),
                   mapping({"type": "keyboard_type_if", "keyboard_types": ["ansi"]}),
                   mapping({"type": []}), mapping({"type": {}}), mapping(None), mapping(app()),
                   mapping(device()), mapping({**app(TERMINAL), "bundle_identifiers": None}),
                   mapping({**app(TERMINAL), "file_paths": ["/Terminal$"]}),
                   mapping({**app(TERMINAL), "future": True}),
                   mapping(device({})), mapping(device({"vendor_id": True})),
                   mapping(device({"is_keyboard": 1})), mapping(device({"future": True})),
                   mapping(variable(1.0)), mapping(variable(None)), mapping(variable(0, name=4))]
        unknown += [mapping(modifiers=spec) for spec in (
            {"mandatory": ["any"]}, {"mandatory": ["future"]}, {"optional": ["future"]},
            {"optional": "any"}, {"mandatory": [1]}, {"future": []})]
        unknown += [{**mapping(), "from": source} for source in (
            {"key_code": "a", "integer_value": 1}, {"key_code": "a", "future": True},
            {"key_code": 1}, {"any": "key_code"}, {"key_code": "a", "pointing_button": "button1"},
            {"simultaneous": [{"key_code": "a"}, {"key_code": "b"}]},
            {"key_code": "a", "simultaneous_options": {}}, {"key_code": "a", "modifiers": None})]
        for rule in unknown:
            with self.subTest(rule=rule):
                self.assertIsNone(manipulator_covers(mapping(), rule))
                self.assertIsNone(manipulator_covers(rule, mapping()))
                self.assertIsNone(manipulator_covers(rule, rule))
                self.assertEqual(find_shadowed([mapping(), rule]), [])

    def test_proofs_match_independent_concrete_context_grid(self):
        proofs = [
            (mapping(device({"vendor_id": 7})), mapping(device({"vendor_id": 7, "product_id": 8}))),
            (mapping(app(TERMINAL, EDITOR)), mapping(app(TERMINAL))),
            (mapping(variable(1, negative=True)), mapping(variable(True))),
            (mapping(device({"vendor_id": 7}, negative=True)), mapping(device({"vendor_id": 9}))),
        ]
        contexts = [{"device": {"vendor_id": vendor, "product_id": product},
                     "app_bundle": bundle, "variables": {"mode": value}, "device_enabled": True}
                    for vendor, product, bundle, value in itertools.product(
                        (7, 9), (8, 10), ("com.apple.Terminal", "org.example.Editor", "other"),
                        (0, 1, True, False, "1"))]
        for earlier, later in proofs:
            self.assertIs(manipulator_covers(earlier, later), True)
            matches = 0
            for context in contexts:
                if match_manipulator(later, KEY, context)["status"] == "match":
                    matches += 1
                    self.assertEqual(match_manipulator(earlier, KEY, context)["status"], "match")
            self.assertGreater(matches, 0)

    def test_payloads_remain_opaque_and_unchanged(self):
        rule = {**mapping(), "to": [{"shell_command": "never execute this"}],
                "to_if_alone": [{"key_code": "escape"}],
                "to_if_held_down": [{"key_code": "left_shift"}],
                "to_after_key_up": [{"set_variable": {"name": "held", "value": 0}}],
                "to_delayed_action": {"to_if_invoked": [{"key_code": "b"}]},
                "parameters": {"basic.to_if_held_down_threshold_milliseconds": 250}}
        rules = [rule, copy.deepcopy(rule)]
        before = copy.deepcopy(rules)
        self.assert_covered(*rules)
        self.assertEqual(len(find_shadowed(rules)), 1)
        self.assertEqual(rules, before)


class ShadowUnitTests(unittest.TestCase):
    def test_warning_records_keep_indices_sources_and_json_serialization(self):
        records = find_shadowed([mapping(), mapping()], [origin(0, "first", 10), origin(1, "later", 2)])
        self.assertEqual(records, [{
            "unit_id": "later", "source": {"line": 2, "column": 1, "path": "example.kb"},
            "indices": [1], "scope": "unit", "covered_by": [
                {"index": 0, "unit_id": "first", "source": {"line": 10, "column": 1, "path": "example.kb"},
                 "covers": [1]}]}])
        self.assertEqual(json.loads(json.dumps(records)), records)

    def test_absent_origins_are_manipulator_proofs_only(self):
        records = find_shadowed([mapping(), mapping(), mapping()])
        self.assertEqual([r["indices"] for r in records], [[1], [2]])
        self.assertTrue(all(r["unit_id"] is None and r["source"] is None and r["scope"] == "manipulator"
                            for r in records))
        self.assertEqual([r["covered_by"][0]["index"] for r in records], [0, 0])
        self.assertEqual(find_shadowed([]), [])
        with self.assertRaises(ValueError):
            find_shadowed([mapping()], [])
        with self.assertRaises(ValueError):
            find_shadowed([mapping()], [origin(1, "wrong")])

    def test_partial_comma_unit_never_warns_whole_unit(self):
        rules = [mapping(), mapping(), mapping(key="c")]
        origins = [origin(0, "first"), origin(1, "comma"), origin(2, "comma")]
        self.assertEqual(find_shadowed(rules, origins), [])
        rules.insert(1, mapping(key="c"))
        origins = [origin(0, "first"), origin(1, "first"), origin(2, "comma"), origin(3, "comma")]
        record, = find_shadowed(rules, origins)
        self.assertEqual(record["indices"], [2, 3])
        self.assertEqual([(p["index"], p["covers"]) for p in record["covered_by"]], [(0, [2]), (1, [3])])

    def test_partial_modifier_variants_and_same_unit_do_not_warn(self):
        left = mapping(modifiers={"mandatory": ["left_shift"]})
        right = mapping(modifiers={"mandatory": ["right_shift"]})
        rules = [left, left, right]
        origins = [origin(0, "first"), origin(1, "sides"), origin(2, "sides")]
        self.assertEqual(find_shadowed(rules, origins), [])
        rules[0] = mapping(modifiers={"mandatory": ["shift"]})
        self.assertEqual(find_shadowed(rules, origins)[0]["indices"], [1, 2])
        self.assertEqual(find_shadowed([mapping(), mapping()], [origin(0, "one"), origin(1, "one")]), [])

    def test_interleaved_unit_variants_require_actual_prior_coverers(self):
        # Both later alternatives must have a coverer before that exact index.
        origins = [origin(0, "first"), origin(1, "later"), origin(2, "first"), origin(3, "later")]
        rules = [mapping(), mapping(), mapping(key="c"), mapping(key="c")]
        self.assertEqual(find_shadowed(rules, origins)[0]["indices"], [1, 3])
        rules[0], rules[2] = rules[2], rules[0]
        self.assertEqual(find_shadowed(rules, origins), [])

    def test_distinct_earlier_units_are_conservatively_not_combined(self):
        rules = [mapping(), mapping(key="c"), mapping(), mapping(key="c")]
        origins = [origin(0, "a"), origin(1, "c"), origin(2, "comma"), origin(3, "comma")]
        self.assertEqual(find_shadowed(rules, origins), [])

    def test_shared_contributing_units_do_not_self_shadow(self):
        origins = [replace(origin(0, "one"), contributing_units=("one", "two")), origin(1, "two")]
        self.assertEqual(find_shadowed([mapping(), mapping()], origins), [])

    def test_unknown_alternative_prevents_a_whole_unit_warning(self):
        origins = [origin(0, "first"), origin(1, "later"), origin(2, "later")]
        rules = [mapping(), mapping(), mapping(app("[[:alpha:]]"))]
        self.assertEqual(find_shadowed(rules, origins), [])

    def test_compiler_origins_preserve_legacy_order_and_whole_expansions(self):
        rules, origins = compiled("a sends b\na:\n  sends c")
        warning, = find_shadowed(rules, origins)
        self.assertEqual(warning["source"]["line"], 1)
        self.assertEqual(warning["covered_by"][0]["source"]["line"], 2)
        for source in ("basic a sends b\na, c sends d",
                       "basic lshift+a sends b\neither_shift+a sends c"):
            with self.subTest(source=source):
                rules, origins = compiled(source)
                self.assertGreater(len(rules), 2)
                self.assertEqual(find_shadowed(rules, origins), [])
        rules, origins = compiled("any+a:\n  sends b\neither_shift+a sends c")
        self.assertEqual(find_shadowed(rules, origins)[0]["indices"], [1, 2])
        rules, origins = compiled("a, a sends b")
        self.assertEqual(find_shadowed(rules, origins), [])

    def test_analysis_does_not_change_real_generated_output_or_origins(self):
        path = Path(__file__).resolve().parent / "fixtures" / "showcase.kb"
        rules, origins = compiled(path.read_text())
        before = copy.deepcopy((rules, origins))
        self.assertEqual(len(rules), 224)
        records = find_shadowed(rules, origins)
        self.assertEqual((rules, origins), before)
        self.assertEqual(json.loads(json.dumps(records)), records)
        for record in records:
            self.assertEqual(sorted(i for proof in record["covered_by"] for i in proof["covers"]), record["indices"])
            for proof in record["covered_by"]:
                for later in proof["covers"]:
                    self.assertLess(proof["index"], later)
                    self.assertIs(manipulator_covers(rules[proof["index"]], rules[later]), True)


if __name__ == "__main__":
    unittest.main()
