"""karabiner_dsl — Write Karabiner configs in plain English, compile to JSON.

The engine lives in sibling modules. This package re-exports the public API
so callers keep one import surface, and the CLI lives in karabiner_dsl.cli.
"""

from karabiner_dsl.dsl import (
    compile_file,
    compile_text,
    reverse_json_file,
    roundtrip_file,
    write_output,
    DEFAULT_TITLE,
    Parser,
    Compiler,
    # Constants
    CONSUMER_KEYS,
    MODIFIER_ALIASES,
    KEY_ALIASES,
    ARROW_EXPANSION,
    MULTI_KEY_EXPANSION,
    APP_BUNDLES,
    REVERSE_MODIFIER_ALIASES,
    REVERSE_KEY_ALIASES,
    REVERSE_CONSUMER_KEYS,
    REVERSE_APP_BUNDLES,
    # Helper functions
    resolve_key,
    resolve_modifier,
    parse_key_combo,
    expand_multi,
    app_name_to_bundle,
    strip_inline_comment,
    reverse_modifier,
    reverse_key,
    reverse_app,
    format_key_combo_from_event,
    format_from_event,
    format_passthrough,
    event_to_verb_target,
    conditions_from_manipulator,
    format_when_header,
    indent_lines,
    wrap_with_conditions,
    decompile_mapping_manipulator,
    find_expansion_name,
    extract_send_event_parts,
    decompile_mapping_manipulators,
    decompile_hyper_rule,
    decompile_standalone_rule,
    decompile_json_object,
    normalize_kb_text,
    canonical_json,
    test_artifact_paths,
)

__all__ = [
    "compile_file",
    "compile_text",
    "reverse_json_file",
    "roundtrip_file",
    "write_output",
    "DEFAULT_TITLE",
    "Parser",
    "Compiler",
    "main",
]


from karabiner_dsl._version import __version__  # noqa: E402
from karabiner_dsl.cli import main  # noqa: E402
