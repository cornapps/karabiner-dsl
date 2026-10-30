"""Hand-authored generated JSON cases; expected winners never use compiler IR."""

import copy
import json
import unittest
import warnings

from karabiner_dsl.matching import match_manipulator, pick_winner


ENABLED = {"device_enabled": True}
KEY = {"key_code": "a", "modifiers": []}


def mapping(*conditions, source=None, target="b"):
    return {"type": "basic", "from": source if source is not None else {"key_code": "a"},
            "to": [{"key_code": target}], "conditions": list(conditions)}


# Neutral remote fixture from the approved proposal, written independently of
# the parser/compiler. The three literal outcomes below are the behavior oracle.
REMOTE_RULES = [
    {"type": "basic",
     "from": {"consumer_key_code": "volume_increment", "modifiers": {"optional": ["any"]}},
     "conditions": [
         {"type": "device_if", "identifiers": [{"vendor_id": 1200, "product_id": 34, "is_consumer": True}]},
         {"type": "frontmost_application_if", "bundle_identifiers": [r"^com\.apple\.Terminal$"]},
         {"type": "variable_if", "name": "scroll", "value": 1}],
     "to": [{"mouse_key": {"vertical_wheel": 32}}]},
    {"type": "basic",
     "from": {"consumer_key_code": "volume_increment", "modifiers": {"optional": ["any"]}},
     "conditions": [
         {"type": "device_if", "identifiers": [{"vendor_id": 1200, "product_id": 34, "is_consumer": True}]},
         {"type": "variable_if", "name": "scroll", "value": 1}],
     "to": [{"mouse_key": {"vertical_wheel": -32}}]},
    {"type": "basic",
     "from": {"consumer_key_code": "volume_increment", "modifiers": {"optional": ["any"]}},
     "conditions": [
         {"type": "device_if", "identifiers": [{"vendor_id": 1200, "product_id": 34, "is_consumer": True}]}],
     "to": [{"consumer_key_code": "volume_decrement"}]},
]
REMOTE_EVENT = {"consumer_key_code": "volume_increment", "modifiers": ["left_shift"]}
REMOTE_CONTEXT = {"device_enabled": True,
                  "device": {"vendor_id": 1200, "product_id": 34, "is_consumer": True}}
GOLDEN_CASES = [
    ("com.apple.Terminal", 1, 0, [{"mouse_key": {"vertical_wheel": 32}}]),
    ("org.example.Editor", 1, 1, [{"mouse_key": {"vertical_wheel": -32}}]),
    ("com.apple.Terminal", 0, 2, [{"consumer_key_code": "volume_decrement"}]),
]


class MatchingTests(unittest.TestCase):
    def status(self, rule, event=None, context=None):
        return match_manipulator(rule, KEY if event is None else event,
                                 ENABLED if context is None else context)["status"]

    def test_three_context_golden_winner_and_action(self):
        for app, scroll, expected_index, expected_action in GOLDEN_CASES:
            with self.subTest(app=app, scroll=scroll):
                context = {**REMOTE_CONTEXT, "app_bundle": app, "variables": {"scroll": scroll}}
                result = pick_winner(REMOTE_RULES, REMOTE_EVENT, context)
                self.assertEqual(result["winner_index"], expected_index)
                self.assertTrue(result["winner_definitive"])
                self.assertEqual(result["possible_indices"], [expected_index])
                self.assertEqual(REMOTE_RULES[result["winner_index"]]["to"], expected_action)

    def test_order_mutations_pick_concretely_wrong_actions(self):
        for order, case, wrong_action in [
            ([1, 0, 2], GOLDEN_CASES[0], [{"mouse_key": {"vertical_wheel": -32}}]),
            ([2, 0, 1], GOLDEN_CASES[1], [{"consumer_key_code": "volume_decrement"}]),
        ]:
            app, scroll, _, correct_action = case
            context = {**REMOTE_CONTEXT, "app_bundle": app, "variables": {"scroll": scroll}}
            reordered = [REMOTE_RULES[index] for index in order]
            result = pick_winner(reordered, REMOTE_EVENT, context)
            self.assertEqual(result["winner_index"], 0)
            self.assertEqual(reordered[0]["to"], wrong_action)
            self.assertNotEqual(reordered[result["winner_index"]]["to"], correct_action)

    def test_earlier_unknown_keeps_later_fallback_uncertain(self):
        rules = [mapping({"type": "variable_if", "name": "layer", "value": 1}),
                 mapping(target="c"), mapping(target="d")]
        result = pick_winner(rules, KEY, ENABLED)
        self.assertIsNone(result["winner_index"])
        self.assertFalse(result["winner_definitive"])
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["possible_indices"], [0, 1])
        self.assertEqual([c["status"] for c in result["candidates"]], ["unknown", "match", "match"])
        self.assertEqual(result["missing_facts"], ["context.variables.layer"])

    def test_false_candidate_excluded_and_later_candidates_ruled_out(self):
        result = pick_winner([mapping(source={"key_code": "x"}), mapping(), mapping()], KEY, ENABLED)
        self.assertEqual(result["winner_index"], 1)
        self.assertEqual(result["possible_indices"], [1])
        self.assertEqual([c["possible"] for c in result["candidates"]], [False, True, False])
        self.assertEqual([c["index"] for c in result["candidates"]], [0, 1, 2])

    def test_no_match_and_empty_rules(self):
        for rules in ([], [mapping(source={"key_code": "z"})]):
            result = pick_winner(rules, KEY, ENABLED)
            self.assertEqual(result["status"], "no_match")
            self.assertIsNone(result["winner_index"])
            self.assertEqual(result["possible_indices"], [])

    def test_false_and_unknown_is_false_in_both_orders(self):
        unknown = {"type": "input_source_if", "input_sources": [{"language": "en"}]}
        false = {"type": "variable_if", "name": "layer", "value": 1}
        for conditions in ((unknown, false), (false, unknown)):
            self.assertEqual(self.status(mapping(*conditions), context={**ENABLED, "variables": {"layer": 0}}), "no_match")
        self.assertEqual(self.status(mapping(unknown), {"key_code": "z", "modifiers": []}), "no_match")

    def test_absent_event_modifiers_and_eligibility_are_unknown(self):
        result = match_manipulator(mapping(), {}, {})
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["missing_facts"], ["context.device_enabled", "event.key_code", "event.modifiers"])
        self.assertEqual(self.status(mapping(), {"key_code": "a"}), "unknown")
        self.assertEqual(self.status(mapping(), KEY), "match")
        any_mod = mapping(source={"key_code": "a", "modifiers": {"optional": ["any"]}})
        self.assertEqual(self.status(any_mod, {"key_code": "a"}), "unknown")

    def test_ignored_device_excludes_even_unknown_conditions(self):
        rule = mapping({"type": "unmodeled"})
        result = pick_winner([rule, mapping()], {}, {"device_enabled": False})
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["possible_indices"], [])

    def test_native_event_kinds_do_not_alias_across_namespaces(self):
        for field, key in (("key_code", "a"), ("consumer_key_code", "volume_increment"),
                           ("pointing_button", "button1")):
            rule = mapping(source={field: key})
            self.assertEqual(self.status(rule, {field: key, "modifiers": []}), "match")
            self.assertEqual(self.status(rule, {field: "other", "modifiers": []}), "no_match")
        self.assertEqual(self.status(mapping(source={"consumer_key_code": "mute"}),
                                     {"key_code": "mute", "modifiers": []}), "no_match")

    def test_native_modifier_families_cover_both_sides(self):
        for family in ("command", "control", "option", "shift"):
            rule = mapping(source={"key_code": "a", "modifiers": {"mandatory": [family]}})
            for actual in ([f"left_{family}"], [f"right_{family}"], [f"left_{family}", f"right_{family}"]):
                self.assertEqual(self.status(rule, {**KEY, "modifiers": actual}), "match")
            self.assertEqual(self.status(rule), "no_match")
            self.assertEqual(self.status(rule, {**KEY, "modifiers": [f"left_{family}", "fn"]}), "no_match")

    def test_side_specific_requirements_and_native_aliases(self):
        for alias, native in (("left_alt", "left_option"), ("right_alt", "right_option"),
                              ("left_gui", "left_command"), ("right_gui", "right_command")):
            for required, actual in ((alias, native), (native, alias)):
                rule = mapping(source={"key_code": "a", "modifiers": {"mandatory": [required]}})
                self.assertEqual(self.status(rule, {**KEY, "modifiers": [actual]}), "match")
        rule = mapping(source={"key_code": "a", "modifiers": {"mandatory": ["left_shift"]}})
        self.assertEqual(self.status(rule, {**KEY, "modifiers": ["right_shift"]}), "no_match")
        self.assertEqual(self.status(rule, {**KEY, "modifiers": ["shift"]}), "unknown")

    def test_optional_any_does_not_remove_mandatory_requirements(self):
        rule = mapping(source={"key_code": "a", "modifiers": {"mandatory": ["shift"], "optional": ["any"]}})
        self.assertEqual(self.status(rule), "no_match")
        self.assertEqual(self.status(rule, {**KEY, "modifiers": ["right_shift", "left_command", "caps_lock"]}), "match")

    def test_optional_families_and_explicit_none(self):
        rule = mapping(source={"key_code": "a", "modifiers": {"optional": ["shift"]}})
        for actual in ([], ["left_shift"], ["right_shift"], ["left_shift", "right_shift"]):
            self.assertEqual(self.status(rule, {**KEY, "modifiers": actual}), "match")
        self.assertEqual(self.status(rule, {**KEY, "modifiers": ["caps_lock"]}), "no_match")
        for spec in ({}, {"optional": []}, {"mandatory": [], "optional": []}):
            rule = mapping(source={"key_code": "a", "modifiers": spec})
            self.assertEqual(self.status(rule), "match")
            self.assertEqual(self.status(rule, {**KEY, "modifiers": ["fn"]}), "no_match")

    def test_unknown_modifier_does_not_invent_coverage(self):
        for modifiers in ({"optional": ["future"]}, {"mandatory": ["future"]}, {"mandatory": ["any"]}):
            rule = mapping(source={"key_code": "a", "modifiers": modifiers})
            self.assertEqual(self.status(rule, {**KEY, "modifiers": ["left_shift"]}), "unknown")

    def test_device_alternatives_and_interface_fields(self):
        condition = {"type": "device_if", "identifiers": [
            {"vendor_id": 7, "product_id": 8, "is_keyboard": True, "is_consumer": False},
            {"device_address": "00:11:22"}]}
        rule = mapping(condition)
        self.assertEqual(self.status(rule, context={**ENABLED, "device": {"device_address": "00:11:22"}}), "match")
        self.assertEqual(self.status(rule, context={**ENABLED, "device": {
            "vendor_id": 7, "product_id": 8, "is_keyboard": True, "is_consumer": False}}), "match")
        self.assertEqual(self.status(rule, context={**ENABLED, "device": {
            "vendor_id": 7, "is_keyboard": False, "device_address": "different"}}), "no_match")
        self.assertEqual(self.status(rule, context={**ENABLED, "device": {"vendor_id": 7}}), "unknown")

    def test_device_unknown_field_and_known_false(self):
        rule = mapping({"type": "device_if", "identifiers": [{"vendor_id": 7, "future_flag": True}]})
        self.assertEqual(self.status(rule, context={**ENABLED, "device": {"vendor_id": 8}}), "no_match")
        self.assertEqual(self.status(rule, context={**ENABLED, "device": {"vendor_id": 7, "future_flag": True}}), "unknown")

    def test_missing_negated_facts_remain_unknown(self):
        for condition, missing in [
            ({"type": "variable_unless", "name": "layer", "value": 0}, "context.variables.layer"),
            ({"type": "device_unless", "identifiers": [{"is_keyboard": True}]}, "context.device.is_keyboard"),
            ({"type": "frontmost_application_unless", "bundle_identifiers": ["Terminal"]}, "context.app_bundle"),
        ]:
            result = match_manipulator(mapping(condition), KEY, ENABLED)
            self.assertEqual(result["status"], "unknown")
            self.assertIn(missing, result["missing_facts"])

    def test_device_unless_negates_all_alternatives(self):
        rule = mapping({"type": "device_unless", "identifiers": [{"vendor_id": 7}, {"is_consumer": True}]})
        for device, expected in (({"vendor_id": 7}, "no_match"),
                                 ({"vendor_id": 8, "is_consumer": False}, "match"),
                                 ({"vendor_id": 8}, "unknown")):
            self.assertEqual(self.status(rule, context={**ENABLED, "device": device}), expected)

    def test_application_patterns_are_or_search_and_case_sensitive(self):
        condition = {"type": "frontmost_application_if", "bundle_identifiers": [
            r"^org\.example\.(Editor|Viewer)$", "Terminal"]}
        for app, expected in (("org.example.Viewer", "match"), ("com.apple.Terminal", "match"),
                              ("org.example.viewer", "no_match"), ("xorg.example.Editor", "no_match")):
            self.assertEqual(self.status(mapping(condition), context={**ENABLED, "app_bundle": app}), expected)
        condition["type"] = "frontmost_application_unless"
        self.assertEqual(self.status(mapping(condition), context={**ENABLED, "app_bundle": "org.example.Viewer"}), "no_match")

    def test_application_unknown_or_true_and_false(self):
        for unknown_field in ({"bundle_identifiers": ["[", "Terminal"]},
                              {"bundle_identifiers": ["Terminal"], "file_paths": ["/Terminal$"]}):
            condition = {"type": "frontmost_application_if", **unknown_field}
            self.assertEqual(self.status(mapping(condition), context={**ENABLED, "app_bundle": "Terminal"}), "match")
            self.assertEqual(self.status(mapping(condition), context={**ENABLED, "app_bundle": "Editor"}), "unknown")
            condition["type"] = "frontmost_application_unless"
            self.assertEqual(self.status(mapping(condition), context={**ENABLED, "app_bundle": "Terminal"}), "no_match")
            self.assertEqual(self.status(mapping(condition), context={**ENABLED, "app_bundle": "Editor"}), "unknown")

    def test_regex_failure_and_unsupported_extensions_are_unknown(self):
        for pattern in ("[", "(?i)terminal", r"\Aterminal", r"\d+", "x++"):
            rule = mapping({"type": "frontmost_application_if", "bundle_identifiers": [pattern]})
            self.assertEqual(self.status(rule, context={**ENABLED, "app_bundle": "terminal"}), "unknown")

    def test_cpp_bracket_constructs_cannot_produce_a_definite_winner(self):
        patterns = [r"^org\.example\.[[:alnum:]]+$",
                    r"^org\.example\.[[.A.]]+$",
                    r"^org\.example\.[[=A=]]+$"]
        context = {**ENABLED, "app_bundle": "org.example.App1"}
        for kind in ("frontmost_application_if", "frontmost_application_unless"):
            for pattern in patterns:
                with self.subTest(kind=kind, pattern=pattern), warnings.catch_warnings(record=True) as emitted:
                    rules = [mapping({"type": kind, "bundle_identifiers": [pattern]}),
                             mapping(target="c")]
                    result = pick_winner(rules, KEY, context)
                    self.assertEqual(result["status"], "unknown")
                    self.assertIsNone(result["winner_index"])
                    self.assertEqual(result["possible_indices"], [0, 1])
                    self.assertEqual(result["candidates"][0]["status"], "unknown")
                    self.assertEqual(emitted, [])

    def test_supported_app_alternative_still_resolves_an_unsupported_regex(self):
        condition = {"type": "frontmost_application_if", "bundle_identifiers": [
            r"^org\.example\.[[:alnum:]]+$", r"^org\.example\.[A-Za-z0-9]+$"]}
        context = {**ENABLED, "app_bundle": "org.example.App1"}
        rules = [mapping(condition), mapping(target="c")]
        self.assertEqual(pick_winner(rules, KEY, context)["winner_index"], 0)
        condition["type"] = "frontmost_application_unless"
        self.assertEqual(pick_winner(rules, KEY, context)["winner_index"], 1)

    def test_variables_use_supplied_values_and_strict_types(self):
        for expected, actual, status in ((1, 1, "match"), (1, True, "no_match"),
                                         (True, "true", "no_match"), ("mode", "mode", "match"),
                                         (0, 0, "match")):
            rule = mapping({"type": "variable_if", "name": "layer", "value": expected})
            self.assertEqual(self.status(rule, context={**ENABLED, "variables": {"layer": actual}}), status)
        rule = mapping({"type": "variable_if", "name": "layer", "value": 0})
        self.assertEqual(self.status(rule, context={**ENABLED, "variables": {}}), "unknown")
        rule["conditions"][0]["type"] = "variable_unless"
        self.assertEqual(self.status(rule, context={**ENABLED, "variables": {"layer": 1}}), "match")

    def test_unsupported_conditions_types_and_from_fields(self):
        rules = [mapping({"type": "keyboard_type_if", "keyboard_types": ["ansi"]}),
                 mapping(source={"key_code": "a", "integer_value": 1}),
                 mapping(source={"key_code": "a", "future_option": True}),
                 mapping(source={"any": "key_code"}),
                 {**mapping(), "type": "mouse_basic"}]
        for rule in rules:
            self.assertEqual(self.status(rule), "unknown")
            self.assertEqual(self.status(rule, context={"device_enabled": False}), "no_match")
        rule = mapping(source={"key_code": "z", "integer_value": 1})
        self.assertEqual(self.status(rule), "no_match")

    def test_simultaneous_never_invents_temporal_evidence(self):
        rule = mapping(source={"simultaneous": [{"key_code": "a"}, {"key_code": "s"}],
                               "simultaneous_options": {"key_down_order": "strict"}})
        for event in (KEY, {"simultaneous": [{"key_code": "a"}, {"key_code": "s"}], "modifiers": []}):
            result = match_manipulator(rule, event, ENABLED)
            self.assertEqual(result["status"], "unknown")
            self.assertTrue(any("history" in reason["message"] for reason in result["reasons"]))

    def test_lifecycle_and_shell_payloads_preserved_without_execution(self):
        rule = {**mapping(), "to": [{"shell_command": "never execute this"},
                                  {"set_variable": {"name": "held", "value": 1, "key_up_value": 0}}],
                "to_if_alone": [{"key_code": "escape"}],
                "to_if_held_down": [{"key_code": "left_shift"}],
                "to_after_key_up": [{"key_code": "tab"}],
                "to_delayed_action": {"to_if_invoked": [{"key_code": "c"}]},
                "parameters": {"basic.to_if_held_down_threshold_milliseconds": 250}}
        before = copy.deepcopy(rule)
        context = {**ENABLED, "variables": {"held": 0}}
        result = pick_winner([rule], KEY, context)
        self.assertEqual(result["winner_index"], 0)
        self.assertEqual(rule, before)
        self.assertEqual(context["variables"], {"held": 0})
        self.assertEqual(json.loads(json.dumps(result)), result)


if __name__ == "__main__":
    unittest.main()
