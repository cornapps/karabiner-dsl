"""Provenance observes legacy output and gives future ordering its source units."""

import copy
from dataclasses import asdict
import json
import os
from pathlib import Path
import textwrap
import unittest
from unittest import mock

from karabiner_dsl.compiler import Compiler
from karabiner_dsl.language import LanguageError
from karabiner_dsl.origins import SourceRecord, origin_of
from karabiner_dsl.parser import Parser
from karabiner_dsl.keys import HELPERS_ROOT_VARIABLE


# Helper features need an install directory. The output only names it.
EXAMPLE_HELPERS_ROOT = "/example/karabiner-helpers"


def setUpModule():
    environment = mock.patch.dict(os.environ, {HELPERS_ROOT_VARIABLE: EXAMPLE_HELPERS_ROOT})
    environment.start()
    unittest.addModuleCleanup(environment.stop)


ROOT = Path(__file__).resolve().parents[1]


def compile_source(source, source_path=None):
    parser = Parser(textwrap.dedent(source).strip(), source_path=source_path)
    compiler = Compiler(parser)
    result = compiler.compile()
    return parser, compiler, result


def flat(result):
    return [m for rule in result['profiles'][0]['complex_modifications']['rules']
            for m in rule['manipulators']]


class OriginTests(unittest.TestCase):
    def assert_parallel(self, compiler, result):
        rules = result['profiles'][0]['complex_modifications']['rules']
        self.assertEqual(len(flat(result)), len(compiler.origins))
        for index, origin in enumerate(compiler.origins):
            self.assertEqual(index, origin.index)
            self.assertIs(flat(result)[index],
                          rules[origin.rule_index]['manipulators'][origin.manipulator_index])
            self.assertTrue(origin.known)
            self.assertIn(origin.unit_id, {unit.id for unit in compiler.p.units})
            for action in origin.actions:
                value = flat(result)[index]
                for component in action.path:
                    value = value[component]
                self.assertIsInstance(value, dict)
                self.assertIsNotNone(action.origin.source)
                self.assertEqual(action.origin.unit_id, origin.unit_id)
        # JSON data is made of ordinary containers, never the internal IR type.
        def check(value):
            if isinstance(value, dict):
                self.assertIs(type(value), dict)
                for child in value.values():
                    check(child)
            elif isinstance(value, list):
                for child in value:
                    check(child)
        check(result)
        self.assertEqual(json.loads(json.dumps(result)), result)
        json.dumps([asdict(origin) for origin in compiler.origins])

    def test_inline_block_and_comma_units_observe_legacy_phase_order(self):
        parser, compiler, result = compile_source('''
            a sends b
            c:
              sends d
              alone sends e
            f, g sends h
        ''')
        self.assertEqual([(u.id, u.kind, u.position) for u in parser.units],
                         [('u1', 'inline', (1, 1)), ('u2', 'key_block', (2, 1)),
                          ('u5', 'comma', (5, 1))])
        self.assertEqual([o.unit_id for o in compiler.origins], ['u2', 'u5', 'u5', 'u1'])
        self.assertEqual([o.internal_order for o in compiler.origins], [0, 0, 1, 0])
        self.assertEqual([a.origin.source.line for a in compiler.origins[0].actions], [3, 4])
        self.assert_parallel(compiler, result)

    def test_comma_and_modifier_side_variants_share_unit_in_expansion_order(self):
        parser, compiler, result = compile_source('either_shift+a, either_shift+b sends c')
        self.assertEqual(len(parser.units), 1)
        self.assertEqual([o.unit_id for o in compiler.origins], ['u1'] * 4)
        self.assertEqual([o.internal_order for o in compiler.origins], list(range(4)))
        self.assertEqual([m['from']['key_code'] for m in flat(result)], ['a'] * 2 + ['b'] * 2)
        self.assert_parallel(compiler, result)

    def test_simultaneous_inline_and_block_have_independent_units(self):
        parser, compiler, result = compile_source('a+b together sends c\nd+e together:\n  sends f')
        self.assertEqual([u.kind for u in parser.units], ['simultaneous', 'simultaneous'])
        self.assertEqual([o.source.line for o in compiler.origins], [2, 1])
        self.assert_parallel(compiler, result)

    def test_tap_hold_first_declaration_anchors_and_each_branch_keeps_its_source(self):
        parser, compiler, result = compile_source('''
            when app Terminal:
              space hold sends left_shift lazy
            a sends b
            when app Terminal:
              space tap sends escape
            when app Finder:
              space tap sends return
              space hold sends left_control
        ''')
        paired = [u for u in parser.units if u.kind == 'tap_hold']
        self.assertEqual([(u.source.line, [d.line for d in u.declarations]) for u in paired],
                         [(2, [2, 5]), (7, [7, 8])])
        self.assertEqual([o.source.line for o in compiler.origins], [2, 7, 3])
        self.assertEqual([a.origin.source.line for a in compiler.origins[0].actions], [2, 5])
        self.assertEqual([a.origin.source.line for a in compiler.origins[1].actions], [8, 7])
        self.assert_parallel(compiler, result)

    def test_hyper_binding_anchor_tap_extension_and_repeated_block_positions(self):
        parser, compiler, result = compile_source('''
            hyper is $
            $ tap sends escape
            $ is caps_lock
            $: # first body
              a sends b
            when app Terminal:
              $:d
                c sends d
            $:
              e sends f
            $:d
              g sends h
        ''')
        self.assertNotIn('u1', {u.id for u in parser.units})
        binding = next(u for u in parser.units if u.id == 'u3')
        self.assertEqual([d.line for d in binding.declarations], [3, 2])
        self.assertEqual(compiler.origins[0].source.line, 3)
        tap = next(a for a in compiler.origins[0].actions if a.path[0] == 'to_if_alone')
        self.assertEqual(tap.origin.source.line, 2)
        activation = next(o for o in compiler.origins if o.generator == 'sublayer_activation')
        self.assertEqual(activation.contributing_units, ('u7', 'u11'))
        self.assertEqual(activation.source.line, 7)
        self.assertEqual({u.id: u.group_id for u in parser.units if u.id in ('u5', 'u8', 'u10', 'u12')},
                         {'u5': 'g4', 'u8': 'g7', 'u10': 'g9', 'u12': 'g11'})
        self.assert_parallel(compiler, result)

    def test_ignored_legacy_hyper_tap_clause_does_not_steal_valid_origin(self):
        parser, compiler, result = compile_source(
            'hyper caps_lock\n$ tap sends escape\n$ tap unrecognized')
        tap = next(a for a in compiler.origins[0].actions if a.path[0] == 'to_if_alone')
        self.assertEqual(tap.origin.source.line, 2)
        self.assertEqual([d.line for d in parser.units[0].declarations], [1, 2])
        self.assertEqual(flat(result)[0]['to_if_alone'], [{'key_code': 'escape'}])
        self.assert_parallel(compiler, result)

    def test_hyper_shorthand_and_array_hold_and_combo_expansions(self):
        parser, compiler, result = compile_source('''
            hyper caps_lock
            $:
              arrows sends ctrl+arrows
              a, b sends c
            $ + arrows sends ctrl+arrows
        ''')
        self.assertEqual([o.source.line for o in compiler.origins], [1] + [3] * 4 + [4] * 2 + [5] * 4)
        self.assertEqual([u.kind for u in parser.units], ['hyper_activation', 'inline', 'comma', 'inline'])
        self.assert_parallel(compiler, result)

    def test_grouping_can_interleave_units_without_falsifying_source_order(self):
        parser, compiler, result = compile_source('''
            hyper caps_lock
            when device gadget, keyboard:
              $:
                a sends c
                b sends d
            devices:
              gadget:
                id 1:2
                ignores modifiers
              keyboard:
                id 3:4
        ''')
        # Defaults split each mapping; legacy groups by condition afterwards.
        self.assertEqual([o.unit_id for o in compiler.origins], ['u1', 'u4', 'u5', 'u4', 'u5'])
        self.assertEqual([o.internal_order for o in compiler.origins[1:]], [0, 0, 1, 1])
        self.assertEqual([m['from']['key_code'] for m in flat(result)[1:]], ['a', 'b', 'a', 'b'])
        self.assert_parallel(compiler, result)

    def test_forward_and_merged_defaults_keep_split_unit_and_action_origins(self):
        for mapping in ('a sends b', 'a:\n    sends b', 'a, b triggers go'):
            with self.subTest(mapping=mapping):
                source = 'when device gadget, keyboard:\n  ' + mapping + '''
 devices:
   gadget:
     id 1:2
   keyboard:
     id 3:4
 devices:
   gadget:
     ignores modifiers
 actions:
   go:
     sends c
'''.replace('\n ', '\n')
                parser, compiler, result = compile_source(source)
                self.assertEqual(len(parser.units), 1)
                self.assertTrue(all(o.unit_id == 'u2' for o in compiler.origins))
                self.assert_parallel(compiler, result)

    def test_nested_macros_keep_calls_definitions_steps_and_lifecycle_paths(self):
        parser, compiler, result = compile_source('''
            actions:
              leaf:
                sends b
                wait 20ms
              outer:
                triggers leaf
                sends c
            a, d:
              triggers outer
              if invoked triggers leaf
              after up sends e
        ''')
        self.assertEqual(len(parser.units), 1)
        for origin in compiler.origins:
            events = origin.actions
            first = events[0].origin
            self.assertEqual(first.source.line, 9)
            self.assertEqual([(m.name, m.call.line, m.definition.line) for m in first.macro_calls],
                             [('outer', 9, 5), ('leaf', 6, 2)])
            self.assertIn(3, [location.line for location in first.related])
            invoked = next(a for a in events if a.path == ('to_delayed_action', 'to_if_invoked', 0))
            self.assertEqual(invoked.origin.source.line, 10)
            self.assertEqual(invoked.origin.macro_calls[0].name, 'leaf')
        self.assert_parallel(compiler, result)

    def test_deepcopy_preserves_ir_metadata_without_serializing_it(self):
        parser, compiler, result = compile_source('actions:\n  go:\n    sends b\na, c triggers go')
        cloned = copy.deepcopy(parser)
        self.assertIsInstance(cloned.advanced_basic[0], SourceRecord)
        self.assertEqual(origin_of(cloned.advanced_basic[0]), origin_of(parser.advanced_basic[0]))
        other = Compiler(cloned)
        self.assertEqual(other.compile(), result)
        self.assertEqual(other.origins, compiler.origins)
        self.assertNotIn('origin', json.dumps(parser.advanced_basic))
        self.assert_parallel(other, result)

    def test_language_header_preserves_original_line_and_path_coordinates(self):
        source = '# comment\n\nlanguage 1\n\na sends b\nc:\n  sends d'
        parser = Parser(source, source_path=ROOT / 'example.kb')
        compiler = Compiler(parser)
        result = compiler.compile()
        baseline = Compiler(Parser(source.replace('language 1', ''))).compile()
        self.assertEqual(result, baseline)
        self.assertEqual(parser.lines, source.splitlines())
        self.assertEqual(parser.language.header_line, 3)
        self.assertEqual([o.source.line for o in compiler.origins], [6, 5])
        self.assertEqual(compiler.origins[0].actions[0].origin.source.column, 3)
        self.assertTrue(all(o.source.path == str(ROOT / 'example.kb') for o in compiler.origins))
        self.assert_parallel(compiler, result)

    def test_language_validation_precedes_ordinary_parse(self):
        with self.assertRaises(LanguageError):
            Parser('language 2\nwhen app:\n  a sends b')
        parser, compiler, result = compile_source('language 1\nactions:\n  language:\n    sends b\na triggers language')
        self.assertEqual(len(parser.units), 1)
        self.assertEqual(compiler.origins[0].source.line, 5)
        self.assertEqual(flat(result)[0]['to'], [{'key_code': 'b'}])

    def test_numpad_generated_controls_alias_rows_macros_and_mapping_variants(self):
        parser, compiler, result = compile_source('''
            numpad pad:
              base:
                default:
                  a, b triggers go
                  either_shift+c:
                    sends d
                when app Terminal:
                  arrows sends ctrl+arrows
              hold layer as nav:
                a enters t9
                b enters neru right_click
                default:
                  c scrolls down 32
              mode t9:
                row types t9 1
                a, b exits
              mode neru:
                c clicks left
            devices:
              pad:
                id 1:2
                ignores modifiers
                keys:
                  layer = keypad_period
                rows:
                  row = keypad_1 keypad_2 keypad_3
            actions:
              go:
                sends return
        ''')
        expected = {4: 2, 5: 2, 8: 4, 9: 1, 10: 1, 11: 1, 13: 1, 15: 3, 16: 2, 18: 1}
        observed = {}
        for origin in compiler.origins:
            observed[origin.source.line] = observed.get(origin.source.line, 0) + 1
        self.assertEqual(observed, expected)
        self.assertEqual({o.generator for o in compiler.origins},
                         {'numpad_mode', 'numpad_layer_activation', 'numpad_entry', 'numpad_mapping'})
        macro = next(o for o in compiler.origins if o.source.line == 4)
        self.assertEqual(macro.actions[0].origin.macro_calls[0].definition.line, 28)
        self.assertEqual(macro.actions[0].origin.source.line, 4)
        self.assert_parallel(compiler, result)

    def test_scoped_swaps_share_one_unit_and_registry_prepass_stays_separate(self):
        parser, compiler, result = compile_source('device 1:2 swaps cmd opt\nwhen device 1:2:\n  swaps cmd opt')
        self.assertEqual(len(parser.units), 1)
        self.assertEqual([o.unit_id for o in compiler.origins], ['u3'] * 4)
        self.assertEqual(len(result['profiles'][0]['devices'][0]['simple_modifications']), 4)
        self.assert_parallel(compiler, result)

    def test_unknown_external_ir_is_explicit_and_has_parallel_entry(self):
        parser = Parser('a sends b')
        parser.standalone[0] = dict(parser.standalone[0])
        compiler = Compiler(parser)
        result = compiler.compile()
        self.assertEqual(len(compiler.origins), len(flat(result)))
        self.assertFalse(compiler.origins[0].known)
        self.assertIsNone(compiler.origins[0].source)
        self.assertEqual(compiler.origins[0].generator, 'simple')

    def test_identical_descriptions_and_payloads_retain_their_actual_sources(self):
        parser, compiler, result = compile_source('a sends b\na sends b\na:\n  sends b\na:\n  sends b')
        self.assertEqual([o.source.line for o in compiler.origins], [3, 5, 1, 2])
        self.assertEqual(len(parser.units), 4)
        self.assert_parallel(compiler, result)

    def test_unregistered_generated_manipulator_is_explicitly_unknown(self):
        compiler = Compiler(Parser('a sends b'))
        compiler.rules.append({'description': 'a sends b', 'manipulators': [
            {'type': 'basic', 'from': {'key_code': 'a'}, 'to': [{'key_code': 'b'}]}]})
        result = compiler.compile()
        self.assertEqual(len(compiler.origins), len(flat(result)))
        self.assertEqual(compiler.origins[0].generator, 'unknown')
        self.assertIsNone(compiler.origins[0].unit_id)
        self.assertFalse(compiler.origins[0].known)
        self.assertTrue(compiler.origins[1].known)

    def test_current_224_manipulators_and_both_historical_goldens_have_complete_sources(self):
        for path in (ROOT / 'tests/fixtures/showcase.kb', ROOT / 'tests/fixtures/showcase.original.kb',
                     ROOT / 'tests/fixtures/showcase.before.kb'):
            with self.subTest(path=path):
                parser = Parser(path.read_text(), source_path=path)
                compiler = Compiler(parser)
                result = compiler.compile()
                self.assertEqual(len(compiler.origins), 224)
                self.assert_parallel(compiler, result)


if __name__ == '__main__':
    unittest.main()
