# karabiner-dsl

Write Karabiner-Elements rules as short English sentences in a `.kb` file.
Compile them to JSON, check them for mistakes, or turn supported JSON rules
back into readable source.

```kb
caps_lock sends escape
cmd+shift+1 opens Finder

when app Terminal:
  ctrl+l sends cmd+k
```

For a starter config to copy, see [examples/basic_example.kb](https://github.com/cornapps/karabiner-dsl/blob/main/examples/basic_example.kb).

## Install

Karabiner DSL requires Python 3.11 or newer. Install the `karabiner-dsl`
command with `uv`:

```bash
uv tool install karabiner-dsl
karabiner-dsl config.kb -o config.json
```

`pipx install karabiner-dsl` also works. The installed command accepts every
flag shown below.

## Run from source

Requires Python 3.11 or newer and `uv`. From the repository root:

```bash
uv sync
uv run python -m karabiner_dsl config.kb
uv run python -m karabiner_dsl config.kb -o config.json
uv run python -m karabiner_dsl --lint config.kb
uv run python -m karabiner_dsl --explain config.kb --key caps_lock --modifiers none --device-enabled yes
uv run python -m karabiner_dsl --reverse config.json
uv run python -m karabiner_dsl --roundtrip config.kb
```

To install the generated configuration for Karabiner-Elements on macOS:

```bash
uv run python -m karabiner_dsl --apply config.kb
```

`--apply` replaces the current `karabiner.json`, first saving a backup.
Review the generated JSON before applying it. `--roundtrip` writes adjacent
`.test.json` and `.test.kb` files and checks the reconstructed configuration.

Installing the repository with `pip install .` also provides a
`karabiner-dsl` command with the same flags as the module command above.

## Language at a glance

Use indentation for shared conditions and actions. Colons on `when` headers
are optional.

```kb
a, b sends escape
page_up scrolls up 32
page_down scrolls down 32
f6 moves right 100
f7 clicks left
f8 sends nothing

right_option holds @navigation
when @navigation:
  h sends left_arrow
```

Name devices once, then refer to them in conditions. The IDs below are
placeholders; replace them with IDs from Karabiner's connected-device list.

```kb
devices:
  remote:
    id 1:2
    enabled
    ignores modifiers

when device remote:
  up_arrow sends volume_up
  down_arrow sends volume_down
```

`ignores modifiers` lets device rules match while other modifiers are held.
Individual rules can override that policy.

Name a sequence under `actions:` and call it with `triggers`:

```kb
actions:
  copy_then_paste:
    sends cmd+c
    wait 200ms
    sends cmd+v

f9 triggers copy_then_paste
```

Use a key block for a sequence, key-release behavior, or timing:

```kb
f10:
  sends a
  after up sends b
```

The [language guide](https://github.com/cornapps/karabiner-dsl/blob/main/GUIDE.md) covers devices, conditions, variables, macros,
hold layers, tap-hold rules, modifier policies, and rule priority.
`--explain` traces generated order using supplied context; missing facts stay
unknown. `--lint` also warns about mappings proved fully shadowed by earlier
ones. See the guide's [worked priority example](https://github.com/cornapps/karabiner-dsl/blob/main/GUIDE.md#explain-an-overlapping-input).

## Editor support

The [Zed extension](https://github.com/cornapps/karabiner-dsl/blob/main/editors/zed/karabiner-dsl/README.md) provides highlighting
for `.kb` files. Compiler linting checks the meaning of the source.

## Development

- `karabiner_dsl/` contains the parser, compiler, decompiler, linter, and CLI.
- `karabiner_dsl.py` is an alternative entry point for the CLI.
- `tests/` contains grammar and behavior regression tests.
- `editors/zed/` contains the editor extension.

Run `just check` for the test suite, fixture linting, roundtrip checks, and
Python syntax checks. Run `just zed-check` after editing the editor grammar.

## Reverse compilation

Reverse compilation recognizes supported patterns and chooses a canonical
spelling. Comments, original formatting, and rule descriptions may change.
Some imported JSON structures remain unsupported; check roundtrip results
before replacing a source file.

## License

Karabiner DSL is released under the [MIT License](https://github.com/cornapps/karabiner-dsl/blob/main/LICENSE).
