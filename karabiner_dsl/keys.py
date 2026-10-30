"""Key names, modifier tables, device parsing, and Karabiner lookup tables."""

import os
import re
from pathlib import Path

DEFAULT_TITLE = "Generated Karabiner Config"
HINT_NOTIFICATION_ID = "karabiner_dsl_layer_hints"
HINT_DELAY_MILLISECONDS = 1300
REPO_ROOT = Path(__file__).resolve().parent.parent
# Hint overlays, numpad blocks, and accessibility actions shell out to
# companion helpers. KARABINER_HELPERS_ROOT names their install directory;
# the helper paths below are relative to it.
HELPERS_ROOT_VARIABLE = "KARABINER_HELPERS_ROOT"
HINT_HELPER_BINARY = Path(".build") / "kb_hints_overlay"
HINT_HELPER_ASSETS = Path("overlay_helper")
NUMPAD_HUD_SCRIPT = Path("tools") / "numpad_hud.py"
NUMPAD_NERU_SCRIPT = Path("tools") / "numpad_neru.py"
NUMPAD_AX_SCRIPT = Path("tools") / "numpad_ax.py"
NUMPAD_PREFERRED_PYTHON = Path("/usr/local/bin/python3")
NUMPAD_FALLBACK_PYTHON = Path("/usr/bin/python3")
NUMPAD_T9_VARIABLE = "numpad_t9_mode"
NUMPAD_NERU_VARIABLE = "numpad_neru_mode"
NUMPAD_PENDING_T9_VARIABLE = "numpad_pending_t9_mode"
NUMPAD_PENDING_NERU_VARIABLE = "numpad_pending_neru_mode"


class MissingHelpersError(ValueError):
    """The source uses a feature whose companion helpers are not configured."""


def helpers_root(feature):
    """Return the companion helper install directory used by a feature."""
    root = os.environ.get(HELPERS_ROOT_VARIABLE)
    if not root:
        raise MissingHelpersError(
            f"{feature} needs the companion helpers. Set {HELPERS_ROOT_VARIABLE} "
            "to the directory where they are installed.")
    return Path(root)
CONSUMER_KEYS = {
    "volume_up": "volume_increment",
    "volume_down": "volume_decrement",
    "brightness_up": "display_brightness_increment",
    "brightness_down": "display_brightness_decrement",
    "play_pause": "play_or_pause",
    "rewind": "rewind",
    "fast_forward": "fast_forward",
    "mute": "mute",
    "dictation": "dictation",
}
MODIFIER_ALIASES = {
    "ctrl": "control",
    "control": "control",
    "cmd": "command",
    "command": "command",
    "opt": "option",
    "option": "option",
    "alt": "option",
    "shift": "shift",
}
GENERIC_MODIFIER_ALIASES = {
    "either_ctrl": "control",
    "either_control": "control",
    "either_cmd": "command",
    "either_command": "command",
    "either_opt": "option",
    "either_option": "option",
    "either_alt": "option",
    "either_shift": "shift",
}
STANDALONE_MODIFIER_KEYS = {
    "ctrl": "left_control",
    "control": "left_control",
    "cmd": "left_command",
    "command": "left_command",
    "opt": "left_option",
    "option": "left_option",
    "alt": "left_option",
    "shift": "left_shift",
}
SIDE_MODIFIER_KEYS = {
    "control": ("left_control", "right_control"),
    "command": ("left_command", "right_command"),
    "option": ("left_option", "right_option"),
    "shift": ("left_shift", "right_shift"),
}
MODIFIER_KEY_CODES = {key for pair in SIDE_MODIFIER_KEYS.values() for key in pair} | {"fn"}


def is_modifier_mapping(from_event, to_events):
    return (from_event.get("key_code") in MODIFIER_KEY_CODES
            and len(to_events) == 1
            and to_events[0].get("key_code") in MODIFIER_KEY_CODES)


KEY_ALIASES = {
    "nothing": "vk_none",
    "backspace": "delete_or_backspace",
    "delete_forward": "delete_forward",
    "space": "spacebar",
    "return": "return_or_enter",
    "enter": "return_or_enter",
    "esc": "escape",
    "hyphen": "hyphen",
    "minus": "hyphen",
    "equal": "equal_sign",
    "equals": "equal_sign",
    "plus": "equal_sign",
    "open_bracket": "open_bracket",
    "close_bracket": "close_bracket",
    "left_bracket": "open_bracket",
    "right_bracket": "close_bracket",
    "semicolon": "semicolon",
    "quote": "quote",
    "backquote": "grave_accent_and_tilde",
    "grave": "grave_accent_and_tilde",
    "tilde": "grave_accent_and_tilde",
    "comma": "comma",
    "period": "period",
    "dot": "period",
    "slash": "slash",
    "backslash": "backslash",
    "\\": "backslash",
    "tab": "tab",
    "delete": "delete_or_backspace",
    "forward_delete": "delete_forward",
}
ARROW_EXPANSION = {
    "arrows": ["left_arrow", "down_arrow", "up_arrow", "right_arrow"],
}
KNOWN_KEY_CODES = set(
    list("abcdefghijklmnopqrstuvwxyz") + [str(n) for n in range(10)] +
    [f"f{n}" for n in range(1, 25)] +
    [f"keypad_{n}" for n in range(10)] +
    [f"international{n}" for n in range(1, 10)] +
    [f"lang{n}" for n in range(1, 10)] + [
        "up_arrow", "down_arrow", "left_arrow", "right_arrow",
        "home", "end", "page_up", "page_down", "insert",
        "return_or_enter", "escape", "delete_or_backspace", "delete_forward",
        "tab", "spacebar", "hyphen", "equal_sign", "open_bracket",
        "close_bracket", "backslash", "non_us_pound", "semicolon", "quote",
        "grave_accent_and_tilde", "comma", "period", "slash",
        "non_us_backslash", "caps_lock", "print_screen", "scroll_lock",
        "pause", "application", "help", "power",
        "keypad_num_lock", "keypad_slash", "keypad_asterisk",
        "keypad_hyphen", "keypad_plus", "keypad_enter", "keypad_period",
        "keypad_equal_sign", "keypad_equal_sign_as400", "keypad_comma",
        "left_control", "left_shift", "left_option", "left_command",
        "right_control", "right_shift", "right_option", "right_command",
        "fn", "mute", "volume_up", "volume_down",
        "japanese_eisuu", "japanese_kana", "japanese_pc_nfer",
        "japanese_pc_xfer", "japanese_pc_katakana", "vk_none",
    ])
MULTI_KEY_EXPANSION = {
    "hjkl": list("hjkl"),
    "wasd": list("wasd"),
    "uiop": list("uiop"),
    "jkl;": ["j", "k", "l", "semicolon"],
}
APP_BUNDLES = {
    "Safari": [r"^com\.apple\.Safari$"],
    "Terminal": [r"^com\.apple\.Terminal$"],
    "Finder": "com.apple.finder",
    "Messages": "com.apple.MobileSMS",
    "Mail": "com.apple.mail",
    "Chrome": [r"^com\.google\.Chrome$", r"^com\.google\.chrome$"],
    "Chromium": [r"^org\.chromium\.Chromium$"],
    "Brave Browser": [r"^com\.brave\.Browser$"],
    "Firefox": [r"^org\.mozilla\.firefox$", r"^org\.mozilla\.nightly$"],
    "Visual Studio Code": "com.microsoft.VSCode",
    "VSCode": "com.microsoft.VSCode",
    "Slack": "com.tinyspeck.slackmacgap",
    "Discord": "com.DiscordInc.Discord",
    "iTerm": [r"^com\.googlecode\.iterm2$"],
    "iTerm2": [r"^com\.googlecode\.iterm2$"],
    "Warp": "dev.warp.Warp-Stable",
    "Cursor": "com.todesktop.230313mzl4w4u92",
    "Alacritty": [r"^com\.alacritty$", r"^io\.alacritty$"],
    "Hyper": [r"^co\.zeit\.hyper$"],
    "WezTerm": [r"^com\.github\.wez\.wezterm$"],
    "Ghostty": [r"^com\.mitchellh\.ghostty$"],
    "Kitty": "net.kovidgoyal.kitty",
    "WhatsApp": "net.whatsapp.WhatsApp",
    "Shortwave": "com.nickvdhoogen.Shortwave",
    "Home Assistant": "io.home-assistant.iOS",
    "BetterTouchTool": "com.hegenberg.BetterTouchTool",
    "Codex": "com.openai.codex",
    "Claude": [r"^com\.anthropic\.claudefordesktop$"],
    "Loopback": "com.rogueamoeba.Loopback",
    "Preview": "com.apple.Preview",
    "Notes": "com.apple.Notes",
    "Music": "com.apple.Music",
    "Spotify": "com.spotify.client",
    "Obsidian": "md.obsidian",
    "Arc": "company.thebrowser.Browser",
    "Linear": "com.linear",
    "Notion": "notion.id",
    "Figma": "com.figma.Desktop",
    "Zoom": "us.zoom.xos",
    "1Password": "com.1password.1password",
}
REVERSE_MODIFIER_ALIASES = {
    "left_control": "left_control",
    "left_command": "left_command",
    "left_option": "left_option",
    "left_shift": "left_shift",
    "control": "ctrl",
    "command": "cmd",
    "option": "option",
    "shift": "shift",
}
REVERSE_KEY_ALIASES = {
    "delete_or_backspace": "backspace",
    "delete_forward": "delete_forward",
    "spacebar": "space",
    "return_or_enter": "return",
    "escape": "escape",
    "equal_sign": "equal",
    "grave_accent_and_tilde": "backquote",
}
REVERSE_CONSUMER_KEYS = {value: key for key, value in CONSUMER_KEYS.items()}
REVERSE_APP_BUNDLES = {
    "com.apple.Safari": "Safari",
    "com.apple.Terminal": "Terminal",
    "com.apple.finder": "Finder",
    "com.apple.MobileSMS": "Messages",
    "com.apple.mail": "Mail",
    "com.google.Chrome": "Chrome",
    "org.chromium.Chromium": "Chromium",
    "com.brave.Browser": "Brave Browser",
    "org.mozilla.firefox": "Firefox",
    "com.microsoft.VSCode": "Visual Studio Code",
    "com.tinyspeck.slackmacgap": "Slack",
    "com.DiscordInc.Discord": "Discord",
    "com.googlecode.iterm2": "iTerm2",
    "dev.warp.Warp-Stable": "Warp",
    "com.todesktop.230313mzl4w4u92": "Cursor",
    "com.alacritty": "Alacritty",
    "org.alacritty": "Alacritty",
    "io.alacritty": "Alacritty",
    "co.zeit.hyper": "Hyper",
    "net.kovidgoyal.kitty": "Kitty",
    "com.github.wez.wezterm": "WezTerm",
    "com.mitchellh.ghostty": "Ghostty",
    "net.whatsapp.WhatsApp": "WhatsApp",
    "com.nickvdhoogen.Shortwave": "Shortwave",
    "io.home-assistant.iOS": "Home Assistant",
    "com.hegenberg.BetterTouchTool": "BetterTouchTool",
    "com.openai.codex": "Codex",
    "com.anthropic.claudefordesktop": "Claude",
    "com.rogueamoeba.Loopback": "Loopback",
    "com.apple.Preview": "Preview",
    "com.apple.Notes": "Notes",
    "com.apple.Music": "Music",
    "com.spotify.client": "Spotify",
    "md.obsidian": "Obsidian",
    "company.thebrowser.Browser": "Arc",
    "com.linear": "Linear",
    "notion.id": "Notion",
    "com.figma.Desktop": "Figma",
    "us.zoom.xos": "Zoom",
    "com.1password.1password": "1Password",
    "com.google.chrome": "Chrome",
    "org.mozilla.nightly": "Firefox",
}
REVERSE_APP_BUNDLES_CASEFOLD = {
    bundle.lower(): app
    for bundle, app in REVERSE_APP_BUNDLES.items()
}
def resolve_key(name):
    return KEY_ALIASES.get(name, name)
def resolve_modifier(name):
    if name in GENERIC_MODIFIER_ALIASES:
        return GENERIC_MODIFIER_ALIASES[name]
    return MODIFIER_ALIASES.get(name, name)
def resolve_combo_modifier(name):
    return resolve_modifier(name)
def is_modifier_alias(name):
    return name in MODIFIER_ALIASES or name in GENERIC_MODIFIER_ALIASES
def resolve_standalone_modifier_key(name):
    if name in GENERIC_MODIFIER_ALIASES:
        name = GENERIC_MODIFIER_ALIASES[name]
    return STANDALONE_MODIFIER_KEYS.get(name, name)
def resolve_side_modifier_keys(name):
    modifier = resolve_modifier(name)
    if modifier in SIDE_MODIFIER_KEYS:
        return SIDE_MODIFIER_KEYS[modifier]
    if modifier.startswith(("left_", "right_")):
        return (modifier, )
    raise ValueError(f"Unsupported device swap modifier: {name}")
def expand_modifier_sides(modifiers):
    variants = [[]]
    for modifier in modifiers:
        side_keys = SIDE_MODIFIER_KEYS.get(modifier)
        if not side_keys:
            variants = [variant + [modifier] for variant in variants]
            continue

        variants = [
            variant + [side_key]
            for variant in variants
            for side_key in side_keys
        ]
    return variants
def format_modifier_suffix(modifiers):
    if not modifiers:
        return ""
    return " " + " ".join(f"+{reverse_modifier(m)}" for m in modifiers)
def parse_key_combo(expr):
    """Parse 'ctrl+option+cmd+left_arrow' into (key_code, [modifiers], is_consumer)."""
    parts = expr.strip().split("+")
    if len(parts) == 1 and parts[0] in MODIFIER_ALIASES:
        return resolve_standalone_modifier_key(parts[0]), [], False
    if len(parts) == 1 and parts[0] in GENERIC_MODIFIER_ALIASES:
        return resolve_standalone_modifier_key(parts[0]), [], False

    key = resolve_key(parts[-1])
    modifiers = [resolve_combo_modifier(m) for m in parts[:-1]]

    is_consumer = key in CONSUMER_KEYS
    if is_consumer:
        key = CONSUMER_KEYS[key]

    return key, modifiers, is_consumer
def expand_multi(from_keys, to_values):
    """Expand 'hjkl sends arrows' into [(h, left_arrow), (j, down_arrow), ...]."""
    from_expanded = MULTI_KEY_EXPANSION.get(from_keys, list(from_keys))
    to_expanded = ARROW_EXPANSION.get(to_values, to_values.split())

    if len(to_expanded) == 1:
        to_expanded = to_expanded * len(from_expanded)

    if len(from_expanded) != len(to_expanded):
        raise ValueError(
            f"Key count mismatch: {from_keys} ({len(from_expanded)}) "
            f"vs {to_values} ({len(to_expanded)})")

    return list(zip(from_expanded, to_expanded))
def app_name_to_bundle(name):
    bundles = app_name_to_bundles(name)
    return bundles[0]
def app_name_to_bundles(name):
    if name.startswith("^") or name.startswith(".*") or "\\" in name:
        return [name]
    value = APP_BUNDLES.get(name)
    if value is None:
        return [f".*\\.{name}.*"]
    if isinstance(value, list):
        return value
    return [value]
def dock_slot_shell_command(slot_number):
    dock_index = slot_number - 1
    return (
        "open -b $(/usr/libexec/PlistBuddy -c "
        f"'print :persistent-apps:{dock_index}:tile-data:bundle-identifier' "
        "~/Library/Preferences/com.apple.dock.plist)"
    )
def strip_inline_comment(line):
    in_quote = False
    quote_char = None
    for idx, ch in enumerate(line):
        if ch in ('"', "'") and not in_quote:
            in_quote = True
            quote_char = ch
        elif ch == quote_char and in_quote:
            in_quote = False
        elif ch == '#' and not in_quote:
            return line[:idx].rstrip()
    return line
def parse_variable_name(name):
    return name[1:] if name.startswith("@") else name
def format_variable_name(name):
    return f"@{name}"
def parse_device_ids(text):
    m = re.fullmatch(r"(\d+):(\d+)", text)
    if not m:
        raise ValueError(f"Expected device id as vendor:product, got {text!r}")
    return int(m.group(1)), int(m.group(2))
def expand_key_specs(text):
    keys = []
    for part in text.split():
        if ".." not in part:
            keys.append(part)
            continue
        start, end = part.split("..", 1)
        m_start = re.fullmatch(r"(.+?)(\d+)", start)
        m_end = re.fullmatch(r"(.+?)(\d+)", end)
        if not m_start or not m_end or m_start.group(1) != m_end.group(1):
            keys.append(part)
            continue
        prefix = m_start.group(1)
        start_num = int(m_start.group(2))
        end_num = int(m_end.group(2))
        step = 1 if end_num >= start_num else -1
        for number in range(start_num, end_num + step, step):
            keys.append(f"{prefix}{number}")
    return keys
def reverse_modifier(name):
    return REVERSE_MODIFIER_ALIASES.get(name, name)
def reverse_key(name):
    if name in REVERSE_CONSUMER_KEYS:
        return REVERSE_CONSUMER_KEYS[name]
    return REVERSE_KEY_ALIASES.get(name, name)
def reverse_app(bundle):
    direct = REVERSE_APP_BUNDLES.get(bundle)
    if direct:
        return direct

    direct = REVERSE_APP_BUNDLES_CASEFOLD.get(bundle.lower())
    if direct:
        return direct

    literal = bundle
    if literal.startswith("^"):
        literal = literal[1:]
    if literal.endswith("$"):
        literal = literal[:-1]
    literal = literal.replace(r"\.", ".")

    if re.fullmatch(r"[A-Za-z0-9._-]+", literal):
        return REVERSE_APP_BUNDLES.get(
            literal, REVERSE_APP_BUNDLES_CASEFOLD.get(literal.lower(), bundle))

    return bundle
