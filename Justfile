# The showcase fixture uses helper features. Its compiled commands only name this directory.
example_helpers := "/example/karabiner-helpers"

default:
    @just --list

check:
    uv run python -m unittest discover -s tests -v
    KARABINER_HELPERS_ROOT={{example_helpers}} uv run python karabiner_dsl.py --lint tests/fixtures/showcase.kb
    uv run python karabiner_dsl.py --lint device_enabled_sample.kb
    uv run python karabiner_dsl.py --lint either_shift_sample.kb
    uv run python karabiner_dsl.py --lint presenter_sample.kb
    KARABINER_HELPERS_ROOT={{example_helpers}} uv run python karabiner_dsl.py --roundtrip tests/fixtures/showcase.kb
    uv run python karabiner_dsl.py --roundtrip device_enabled_sample.kb
    uv run python karabiner_dsl.py --roundtrip either_shift_sample.kb
    uv run python karabiner_dsl.py --roundtrip presenter_sample.kb
    uv run python -m compileall karabiner_dsl

zed-check:
    bash scripts/check-zed-extension.sh

compile file:
    uv run python karabiner_dsl.py {{file}}

apply file:
    uv run python karabiner_dsl.py --apply {{file}}

roundtrip file:
    uv run python karabiner_dsl.py --roundtrip {{file}}

reverse file:
    uv run python karabiner_dsl.py --reverse {{file}}
