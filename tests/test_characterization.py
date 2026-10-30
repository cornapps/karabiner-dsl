"""Legacy winners asserted independently of the compiler's intermediate form."""

import unittest

from karabiner_dsl.matching import pick_winner
from karabiner_dsl.pipeline import compile_text


def mappings(source):
    return [m for p in compile_text(source)["profiles"]
            for rule in p["complex_modifications"]["rules"]
            for m in rule["manipulators"]]


class LegacyWinnerTests(unittest.TestCase):
    def test_legacy_promotions_keep_the_observed_winner(self):
        cases = [
            ("a sends b\na:\n  sends c", "c"),
            ("actions:\n  sample:\n    sends c\na sends b\na triggers sample", "c"),
            ("when device 1:2:\n  a sends b\na:\n  sends c", "c"),
            ("devices:\n  remote:\n    id 1:2\n    ignores modifiers\nwhen device remote:\n  a sends b\na:\n  sends c", "b"),
            ("a sends b\nbasic a sends c", "c"),
            ("a sends b\na, d sends c", "c"),
        ]
        event = {"key_code": "a", "modifiers": []}
        context = {"device_enabled": True, "device": {"vendor_id": 1, "product_id": 2}}
        for source, expected_key in cases:
            with self.subTest(source=source):
                compiled = mappings(source)
                winner = pick_winner(compiled, event, context)["winner_index"]
                self.assertIsNotNone(winner)
                self.assertEqual(compiled[winner]["to"], [{"key_code": expected_key}])

    def test_three_context_remote_winners_and_missing_state(self):
        source = """devices:
  remote:
    id 1:2
    ignores modifiers
when device remote and app Terminal and @scroll:
  volume_up scrolls down 32
when device remote and @scroll:
  volume_up scrolls up 32
when device remote:
  volume_up sends volume_down
"""
        compiled = mappings(source)
        event = {"consumer_key_code": "volume_increment", "modifiers": []}
        base = {"device_enabled": True, "device": {"vendor_id": 1, "product_id": 2}}
        contexts = [
            ({"app_bundle": "com.apple.Terminal", "variables": {"scroll": 1}}, [{"mouse_key": {"vertical_wheel": 32}}]),
            ({"app_bundle": "com.apple.Safari", "variables": {"scroll": 1}}, [{"mouse_key": {"vertical_wheel": -32}}]),
            ({"app_bundle": "com.apple.Terminal", "variables": {"scroll": 0}}, [{"consumer_key_code": "volume_decrement"}]),
        ]
        for context, expected_action in contexts:
            with self.subTest(context=context):
                winner = pick_winner(compiled, event, base | context)["winner_index"]
                self.assertIsNotNone(winner)
                self.assertEqual(compiled[winner]["to"], expected_action)
        self.assertIsNone(pick_winner(compiled, event, base)["winner_index"])
        # This catches an actual priority inversion, not merely a count change.
        wrong = list(reversed(compiled))
        winner = pick_winner(wrong, event, base | contexts[0][0])["winner_index"]
        self.assertNotEqual(wrong[winner]["to"], contexts[0][1])


if __name__ == "__main__":
    unittest.main()
