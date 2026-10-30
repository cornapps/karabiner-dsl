# Karabiner DSL Guide

A `.kb` file describes keyboard and pointer behavior using short sentences.
Indentation groups actions and conditions. Lines beginning with `#` are
comments.

## Compile and check a file

From the repository root:

```bash
uv run python -m karabiner_dsl my.kb -o my.json
uv run python -m karabiner_dsl --lint my.kb
uv run python -m karabiner_dsl --reverse my.json
uv run python -m karabiner_dsl --roundtrip my.kb
```

After `uv tool install karabiner-dsl`, run `karabiner-dsl` in place of
`uv run python -m karabiner_dsl` from any directory.

Lint reports unrecognized source lines, unknown output key codes, and mappings
proved unreachable because an earlier mapping covers every alternative.
Warnings make `--lint` exit with status 1. Informational language-mode notices
keep status 0; add `--json` for structured diagnostics.
Roundtrip compiles the file, reconstructs source from the JSON, and compares
the results. It writes adjacent `.test.json` and `.test.kb` files.

On macOS, `--apply my.kb` installs the generated configuration for
Karabiner-Elements. It replaces the current `karabiner.json` after saving a
backup. Inspect the generated file before applying it.

## Sentences and key blocks

A mapping names a trigger, a verb, and a target:

```kb
caps_lock sends escape
cmd+shift+1 opens Finder
f1 runs open https://example.com
```

`sends` emits a key event. `opens` launches an application by name. `runs`
executes a shell command.

Pointer actions work on a single line:

```kb
page_up scrolls up 32
page_down scrolls down 32
f2 scrolls left 16
f3 moves right 100
f4 clicks middle
f5 sends nothing
```

`scrolls` and `moves` accept `up`, `down`, `left`, or `right` followed by an
amount. `clicks` accepts `left`, `right`, or `middle`. `nothing` consumes the
trigger without sending a normal key.

Use a key block for several actions or behavior at different moments:

```kb
f6:
  sends a
  sends b
  after up sends c
```

The `basic` keyword remains accepted before a key block or inline mapping.
Key blocks already have basic priority. In legacy language 1, adding `basic`
to an ordinary inline `sends`, `opens`, or `runs` mapping raises its priority;
see [Rule priority](#rule-priority) before changing that spelling.

## Keys and modifiers

Join modifiers and the trigger key with `+`:

```kb
cmd+shift+1 opens Finder
ctrl+option+left_arrow sends home
fn+escape sends escape
```

`cmd`, `ctrl`, `option`, and `shift` in trigger chords accept either physical
side. Use names such as `left_command` or `right_shift` when the side matters.
`either_cmd`, `either_ctrl`, `either_option`, and `either_shift` are also
accepted.

When a modifier is itself a key event, a short name selects its left-hand
key: `shift` sends `left_shift`, for example.

Useful key aliases include:

- `space` for `spacebar`
- `return` for `return_or_enter`
- `backspace` for `delete_or_backspace`
- `nothing` for `vk_none`
- `volume_up`, `volume_down`, and `dictation` for consumer-key events

These aliases work in trigger and target positions where that key is valid.

## Several triggers

Separate triggers with commas when they share the same action:

```kb
a, b sends escape
left_option+tab, left_shift+dictation sends nothing
```

Comma-separated triggers also work on a block:

```kb
f7, f8:
  sends a
  after up sends b
```

Within hold layers, directional expansions pair several triggers with several
targets:

```kb
$:
  hjkl sends arrows
  arrows sends ctrl+option+cmd+arrows
```

Supported trigger groups include `hjkl`, `wasd`, `uiop`, `jkl;`, and `arrows`.
For `hjkl sends arrows`, `h` sends Left, `j` sends Down, `k` sends Up, and `l`
sends Right.

## Named devices

Declare physical devices in a `devices:` registry:

```kb
devices:
  keyboard:
    id 1:2
    kind keyboard+pointing
    enabled
  remote:
    id 3:4
    enabled
    ignores modifiers
```

The IDs in this guide are placeholders. Replace them with the
`vendor_id:product_id` values from Karabiner's connected-device list.

`kind` accepts `keyboard`, `pointing`, or `keyboard+pointing`. `enabled` makes
the device available to the profile; `ignored` excludes it. A named
`swaps right_command right_option` property stores a modifier swap for that
device. Multiple `devices:` blocks can contribute to the registry.

Use the device name in a condition:

```kb
when device remote:
  up_arrow sends volume_up
  down_arrow sends volume_down
```

Raw IDs, such as `when device 3:4:`, are also accepted.

### Modifier policies

`ignores modifiers` lets a device's rules match while other modifiers are
held. The policy follows nested conditions and keeps any modifiers required
by the trigger chord.

Override it inside an individual key block:

```kb
when device remote:
  a:
    optional shift
    sends b
  c:
    optional none
    sends d
```

`optional shift` allows Shift in addition to the required trigger modifiers.
`optional none` allows no additional modifiers. `optional any` explicitly
allows any additional modifiers. The inline `any+` prefix is another way to
request that policy:

```kb
any+up_arrow sends home
```

Inside hold-layer mappings, a trailing `+shift` is an optional passthrough
modifier; a leading `shift+` in a trigger chord requires Shift.

Direct modifier-to-modifier mappings accept other held modifiers
automatically:

```kb
left_option sends left_command
```

For a conditional swap in both directions:

```kb
when device external_keyboard except keyboard:
  swaps left_command left_option
```

`external_keyboard` matches external keyboards; `except keyboard` excludes
the named device. Scoped swaps generate both directions under the complete
condition. A swap declared as a device property applies to that device
through Karabiner's per-device settings.

## Conditions

`when` scopes the indented rules. A trailing colon is optional.

```kb
when app Terminal, Safari:
  ctrl+n sends cmd+down_arrow
```

A comma-separated app or device list matches any listed value. Nested
conditions all need to match:

```kb
when app Terminal:
  when device remote:
    home sends cmd+up_arrow
```

Use `and` for conditions on one line:

```kb
when device remote and @navigation:
  volume_up scrolls down 32
```

Use `unless` for variables that must be off:

```kb
when device remote unless @navigation, @editing:
  tab sends return
```

Use `except` for a device exclusion, as in the keyboard swap above.

## Variables

Variables store named state. These boolean verbs set them to `1` or `0`:

```kb
f9 enables @navigation
f10 disables @navigation
right_option holds @navigation
```

`holds` sets the variable to `1` on key down and resets it to `0` on release.

A bare variable condition means its value is `1`. `not`, `no`, and `!` check
for `0`:

```kb
when @navigation:
  h sends left_arrow
when not @navigation:
  h sends h
```

`when no @navigation:` and `when !@navigation:` are equivalent to the
negative condition above. For other integer values, use
`when @navigation is 2:`. A key block can set an explicit value with
`set @navigation 2` or reset it on release with
`set @navigation 1 until key up 0`.

## Reusable actions

Define a named sequence under `actions:`. `triggers` expands its steps at
the call site:

```kb
actions:
  copy:
    sends cmd+c
  copy_then_paste:
    triggers copy
    wait 200ms
    sends cmd+v

f11 triggers copy_then_paste
f12:
  triggers copy
  sends return
```

Actions can trigger other actions. Unknown names and recursive action cycles
are errors. `wait` adds a pause between emitted key events; it does not wait
for an application or shell command to finish.

## Hold layers and sublayers

Declare a hyper symbol, bind its physical key, then indent its mappings:

```kb
hyper is $
$ is caps_lock
$ tap sends escape

$:
  hjkl sends arrows +shift
  t opens Terminal
```

Tap Caps Lock to send Escape. Hold it to activate the layer. While held,
`h`, `j`, `k`, and `l` send arrows, and `t` opens Terminal. The `+shift` suffix
lets Shift pass through to the arrow events.

A sublayer uses `$:<key>`:

```kb
$:d
  hjkl sends brightness_down volume_down volume_up brightness_up
  space sends play_pause
```

Hold the hyper key and press `d` to enter the sublayer. Direct hyper combos
are also supported:

```kb
$ + arrows sends ctrl+option+cmd+arrows
```

## Simultaneous keys

Use `together` when ordinary keys must be pressed together:

```kb
f+j together sends escape
```

This describes a simultaneous pair, rather than a modifier chord.

## Tap-hold rules

Describe the tap and hold on separate lines:

```kb
spacebar tap sends spacebar
spacebar hold sends left_shift lazy
```

Tap Space to send a space; hold it to act as Left Shift. `lazy` defers the
modifier event until another key needs it.

## Timing and key lifecycle

A key block can describe different moments of a press:

```kb
when not @space_mode:
  spacebar:
    alone sends spacebar halt
    held set @space_mode 1 until key up 0
    if canceled sends spacebar
    alone timeout 300ms
    held threshold 150ms
    delay 150ms
```

- `alone` runs when the press qualifies as a tap by itself.
- `held` runs once the hold threshold is reached.
- `after up` runs when the key is released.
- `if invoked` runs when the delayed-action timer completes.
- `if canceled` runs when another key down cancels that timer.
- `alone timeout` sets the limit for the alone branch.
- `held threshold` sets the delay before the held branch.
- `delay` sets the timer for the invoked and canceled branches.

These lifecycle prefixes accept actions such as `sends`, `opens`, `runs`,
`triggers`, and `set`. `halt` stops competing alone or delayed behavior for
the press. `lazy` is useful for modifier events.

## Rule priority

Karabiner tries generated manipulators in order. The first applicable one
wins: its input key, modifiers, and every condition must match. A more
specific condition does not automatically give a mapping higher priority.

Unversioned files use legacy language 1. Write `language 1` at the top to
acknowledge that mode. Unversioned lint reports the promotion constructs
actually used in the file. This compiler rejects `language 2` and unknown
versions before generating or installing output.

Language 1 emits these groups in this order:

1. Hyper-key activation and its tap behavior.
2. Paired `tap` / `hold` declarations.
3. Key blocks, comma-separated mappings, scoped `swaps`, explicit `basic`,
   `any+` mappings, and inline structured actions: `triggers`, `enables`,
   `disables`, `holds`, `set`, `set_variable`, `wait`, `delay`, `scrolls`,
   `moves`, `track`, `tracks`, `clicks`, `left-clicks`, and `right-clicks`.
   Inline event actions carrying `lazy` or `halt` also belong here. A
   device's inherited `ignores modifiers` promotes its ordinary inline
   `sends`, `opens`, and `runs` mappings into this group.
4. Sublayer activation controls.
5. Mappings inside `$:` hold layers.
6. Mappings inside `$:<key>` sublayers.
7. Generated mappings from specialized extension blocks, when present.
8. Standalone mappings: ordinary single-trigger inline `sends`, `opens`,
   and `runs`, inline `together`, and spaced `trigger + keys` combinations.

Group 3 follows source order, including all expansions of each mapping.
Group 8 follows standalone declaration order. Tap-hold pairs combine by
trigger and conditions, at the first declaration. Hold-layer mappings group
by identical conditions in first-seen order; sublayers group first by their
activation key, then by conditions. Repeated sublayer headers share an
activation control. These legacy grouping rules can move mappings across
one another; use `--explain` to inspect the actual emitted indices.

For example, `pause triggers submit` is in group 3 and beats an overlapping
ordinary `pause sends return` in group 8, even if the latter appears first.
Changing that fallback to `basic pause sends return` or combining it with
another trigger moves it into group 3 and can change the winner.

Device-registry `swaps` become per-device simple modifications in a separate
Karabiner stage before complex modifications. Their position in the source
does not control complex-rule priority. Scoped `swaps` generate complex
manipulators and participate in group 3.

### Explain an overlapping input

This neutral example is also available as
[priority_sample.kb](tests/fixtures/priority_sample.kb). Its device ID is a
placeholder. All three mappings inherit `ignores modifiers`, so they share
group 3 and keep the displayed order:

```kb
language 1

devices:
  remote:
    id 1:2
    enabled
    ignores modifiers

when device remote and app Terminal and @scroll:
  volume_up scrolls down 32

when device remote and @scroll:
  volume_up scrolls up 32

when device remote:
  volume_up sends volume_down
```

Supply the input and context explicitly:

```bash
uv run python -m karabiner_dsl --explain tests/fixtures/priority_sample.kb \
  --key volume_up --modifiers none --device remote --device-enabled yes \
  --app com.apple.Terminal --var scroll=1
```

That context selects generated index 0: scroll down. Changing `--app` to
`com.apple.finder` selects index 1: scroll up. Keeping Finder and changing
`--var scroll=0` selects index 2: send Volume Down. Indices start at zero.

`--explain` shows generated order, source lines, mapping units, expanded
macro calls and definitions, actions, and the reasons each candidate matches,
fails, or remains unknown. Add `--json` for the same report as structured
data, or `-o explanation.json` to save it.

The tool uses supplied facts, rather than reading live Karabiner state:

- `--key` supplies the event after simple modifications. `--filter-key` only
  limits displayed candidates; it supplies no event fact and leaves the
  global winner calculation unchanged.
- `--modifiers` accepts physical names such as `left_shift,left_command`,
  or `none` for an explicitly empty set. Omitting it leaves modifiers unknown.
- `--device` supplies a registry name or `vendor:product` ID.
  `--device-enabled yes|no` separately supplies eligibility for the complex
  stage. Device declarations alone do not establish that runtime fact.
- `--app` supplies the actual frontmost bundle ID.
- Repeated `--var name=value` options supply variable values. Omitted
  variables stay unknown, including variables Karabiner may default to zero.
- `--context context.json` supplies an object containing `device`,
  `device_enabled`, `app_bundle`, and/or `variables`. Device fields can include
  interface details. Conflicting file and command-line values are errors.

Missing context and unsupported predicates produce `unknown`. If an earlier
candidate is unknown, a later matching fallback remains only a possible
winner. Simultaneous recognition needs timing evidence and stays unknown;
this tool does not simulate tap-hold timing or lifecycle execution.

### Shadow warnings

`--lint` warns when one earlier mapping unit provably covers every generated
alternative of a later unit at earlier output indices. It compares triggers,
modifier acceptance, and supported device, app, and variable conditions.
Extra conditions narrow eligibility; they do not confer priority.

For example, this key block wins before the ordinary inline fallback:

```kb
a sends b
a:
  sends c
```

Lint reports the first line as fully shadowed and identifies the covering
source line. Partial overlap, arbitrary regex relationships, timing-dependent
cases, and coverage requiring several distinct earlier units remain unproved
and produce no full-shadow warning. A clean lint result therefore establishes
only that these bounded checks found no unreachable unit.

## Reverse compilation and limits

The decompiler chooses canonical source for supported JSON patterns. It may
change formatting, aliases, grouping, and descriptions. It does not recover
comments or named action definitions from expanded events.

Some imported configurations contain structures or state machines that the
decompiler only partially models. Use roundtrip checks to assess a result
before adopting the reconstructed source.

For focused examples, see [tap-hold](examples/tap_hold_sample.kb) and
[SpaceFn state](examples/spacefn_state_sample.kb).
