"""Decompiler: Karabiner-Elements JSON back into .kb text."""

import json
import re
import shlex
from pathlib import Path

from .keys import (
    ARROW_EXPANSION,
    DEFAULT_TITLE,
    HINT_HELPER_BINARY,
    HINT_NOTIFICATION_ID,
    MULTI_KEY_EXPANSION,
    SIDE_MODIFIER_KEYS,
    expand_modifier_sides,
    format_variable_name,
    is_modifier_mapping,
    parse_key_combo,
    reverse_app,
    reverse_key,
    reverse_modifier,
    resolve_side_modifier_keys,
)


def format_key_combo_from_event(event):
    key_name = (event.get("consumer_key_code") or event.get("key_code")
                or event.get("apple_vendor_keyboard_key_code"))
    modifiers = [reverse_modifier(m) for m in event.get("modifiers", [])]
    return "+".join(modifiers + [reverse_key(key_name)])
def format_key_combo_from_event_raw(event):
    key_name = (event.get("consumer_key_code") or event.get("key_code")
                or event.get("apple_vendor_keyboard_key_code"))
    modifiers = [reverse_modifier(m) for m in event.get("modifiers", [])]
    return "+".join(modifiers + [key_name])
def format_from_event_raw(from_event):
    if "simultaneous" in from_event:
        keys = [
            event.get("key_code") or event.get("consumer_key_code")
            or event.get("apple_vendor_keyboard_key_code")
            for event in from_event["simultaneous"]
        ]
        return "+".join(keys) + " together"
    key_name = (from_event.get("consumer_key_code")
                or from_event.get("key_code"))
    if "consumer_key_code" in from_event:
        key_name = reverse_key(key_name)
    modifiers = from_event.get("modifiers", {})
    mandatory = [reverse_modifier(m) for m in modifiers.get("mandatory", [])]
    return "+".join(mandatory + [key_name])
def format_from_event(from_event):
    key_name = reverse_key(from_event.get("consumer_key_code")
                           or from_event.get("key_code"))
    modifiers = from_event.get("modifiers", {})
    mandatory = [reverse_modifier(m) for m in modifiers.get("mandatory", [])]
    return "+".join(mandatory + [key_name])
def format_passthrough(from_event):
    modifiers = from_event.get("modifiers", {})
    optional = [m for m in modifiers.get("optional", []) if m != "any"]
    if not optional:
        return ""
    return " " + " ".join(f"+{reverse_modifier(m)}" for m in optional)
def event_to_verb_target(event):
    if "shell_command" in event:
        command = event["shell_command"]
        if command.startswith("open -a "):
            try:
                parts = shlex.split(command)
            except ValueError:
                parts = None
            if parts and len(parts) == 3 and parts[:2] == ["open", "-a"]:
                return "opens", parts[2]
        bundle_match = re.fullmatch(r"open -b ([A-Za-z0-9._-]+)", command)
        if bundle_match:
            return "opens", reverse_app(bundle_match.group(1))
        dock_match = re.fullmatch(
            r"open -b \$\(/usr/libexec/PlistBuddy -c "
            r"'print :persistent-apps:(\d+):tile-data:bundle-identifier' "
            r"~/Library/Preferences/com\.apple\.dock\.plist\)", command)
        if dock_match:
            return "opens", f"dock {int(dock_match.group(1)) + 1}"
        return "runs", command
    return "sends", format_key_combo_from_event(event)
def event_suffix(event):
    return " lazy" if event.get("lazy") else ""
def event_halt_suffix(event):
    return " halt" if event.get("halt") else ""
def pretty_parameter_line(name, value):
    aliases = {
        "basic.to_if_alone_timeout_milliseconds": f"alone timeout {value}ms",
        "basic.to_if_held_down_threshold_milliseconds":
        f"held threshold {value}ms",
        "basic.to_delayed_action_delay_milliseconds": f"delay {value}ms",
    }
    return aliases.get(name, f"parameter {name} {value}")
def conditions_from_manipulator(manipulator, ignore_variable_names=None):
    if ignore_variable_names is None:
        ignore_variable_names = set()

    result = []
    for condition in manipulator.get("conditions", []):
        if condition["type"] == "variable_if":
            if condition["name"] in ignore_variable_names:
                continue
            result.append({
                "type": "variable",
                "name": condition["name"],
                "value": condition["value"],
            })
            continue
        if condition["type"] == "frontmost_application_if":
            values = []
            for bundle in condition.get("bundle_identifiers", []):
                app = reverse_app(bundle)
                if app not in values:
                    values.append(app)
            result.append({
                "type":
                "app",
                "values": values,
            })
        elif condition["type"] in ("device_if", "device_unless"):
            values = []
            for identifier in condition.get("identifiers", []):
                if identifier == {"is_keyboard": True, "is_built_in_keyboard": False}:
                    values.append("external_keyboard")
                elif "vendor_id" in identifier and "product_id" in identifier:
                    values.append(
                        f"{identifier['vendor_id']}:{identifier['product_id']}"
                    )
                elif "product_name" in identifier:
                    values.append(identifier["product_name"])
            result.append({
                "type":
                "device_except" if condition["type"] == "device_unless" else "device",
                "values": values,
            })
    return result
def format_when_header(condition):
    if condition["type"] == "variable":
        return f"when {format_variable_name(condition['name'])} is {condition['value']}"
    return f"when {condition['type']} {', '.join(condition['values'])}"
def indent_lines(lines, prefix="  "):
    return [prefix + line if line else line for line in lines]
def wrap_with_conditions(conditions, inner_lines):
    lines = list(inner_lines)
    for condition in reversed(conditions):
        lines = [format_when_header(condition)] + indent_lines(lines)
    return lines
def merge_conditioned_blocks(entries):
    merged = []
    for conditions, lines in entries:
        if conditions and merged and merged[-1][0] == conditions:
            merged[-1][1].extend(lines)
            continue
        merged.append((conditions, list(lines)))
    return [wrap_with_conditions(conditions, lines) for conditions, lines in merged]
def decompile_mapping_manipulator(manipulator):
    from_text = format_from_event(manipulator["from"])
    if "any" in manipulator["from"].get("modifiers", {}).get("optional", []):
        from_text = "any+" + from_text
    verb, target = event_to_verb_target(manipulator["to"][0])
    return f"{from_text} {verb} {target}{format_passthrough(manipulator['from'])}"
def decompile_tap_hold_rule(rule):
    manipulator = rule["manipulators"][0]
    from_text = format_from_event_raw(manipulator["from"])
    tap_event = manipulator["to_if_alone"][0]
    hold_event = manipulator["to"][0]
    tap_verb = "sends" if "shell_command" not in tap_event else None
    hold_verb = "sends" if "shell_command" not in hold_event else None
    tap_target = (format_key_combo_from_event_raw(tap_event)
                  if tap_verb else event_to_verb_target(tap_event)[1])
    hold_target = (format_key_combo_from_event_raw(hold_event)
                   if hold_verb else event_to_verb_target(hold_event)[1])
    if tap_verb is None:
        tap_verb, _ = event_to_verb_target(tap_event)
    if hold_verb is None:
        hold_verb, _ = event_to_verb_target(hold_event)
    return [
        f"{from_text} tap {tap_verb} {tap_target}{format_passthrough(manipulator['from'])}",
        f"{from_text} hold {hold_verb} {hold_target}{event_suffix(hold_event)}{format_passthrough(manipulator['from'])}",
    ]
def decompile_action_event(field, event):
    lead = f"{field} " if field else ""
    if "set_variable" in event:
        payload = event["set_variable"]
        line = f"{lead}set {format_variable_name(payload['name'])} {payload['value']}"
        if "key_up_value" in payload:
            line += f" until key up {payload['key_up_value']}"
        return line

    if event.get("key_code") == "vk_none" and "hold_down_milliseconds" in event:
        return f"{lead}wait {event['hold_down_milliseconds']}ms"

    vertical_wheel = event.get("mouse_key", {}).get("vertical_wheel")
    if vertical_wheel is not None:
        direction = "up" if vertical_wheel < 0 else "down"
        return f"{lead}scrolls {direction} {abs(vertical_wheel)}"

    horizontal_wheel = event.get("mouse_key", {}).get("horizontal_wheel")
    if horizontal_wheel is not None:
        direction = "left" if horizontal_wheel < 0 else "right"
        return f"{lead}scrolls {direction} {abs(horizontal_wheel)}"

    mouse_x = event.get("mouse_key", {}).get("x")
    if mouse_x is not None:
        direction = "left" if mouse_x < 0 else "right"
        return f"{lead}moves {direction} {abs(mouse_x)}"

    mouse_y = event.get("mouse_key", {}).get("y")
    if mouse_y is not None:
        direction = "up" if mouse_y < 0 else "down"
        return f"{lead}moves {direction} {abs(mouse_y)}"

    if "pointing_button" in event:
        button = {"button1": "left", "button2": "right", "button3": "middle"}[event["pointing_button"]]
        return f"{lead}clicks {button}"

    if "shell_command" in event:
        verb, target = event_to_verb_target(event)
    else:
        verb, target = "sends", format_key_combo_from_event_raw(event)
    return f"{lead}{verb} {target}{event_suffix(event)}{event_halt_suffix(event)}"
def _basic_modifier_family(rule):
    """Recognize the concrete left/right variants of one basic chord."""
    description = rule.get("description", "")
    if not description.startswith("basic "):
        return None
    spec = description[len("basic "):]
    if spec.endswith(" together"):
        return None
    key, mandatory, consumer = parse_key_combo(spec)
    manipulators = rule.get("manipulators", [])
    if not manipulators:
        return None
    first = manipulators[0]
    optional = first["from"].get("modifiers", {}).get("optional", [])
    expected = []
    for variant in expand_modifier_sides(mandatory):
        event = {"consumer_key_code" if consumer else "key_code": key}
        modifiers = {}
        if variant:
            modifiers["mandatory"] = variant
        if optional:
            modifiers["optional"] = optional
        if modifiers:
            event["modifiers"] = modifiers
        expected.append({**first, "from": event})
    return spec if expected == manipulators else None


def decompile_advanced_basic_rule(rule):
    manipulator = rule["manipulators"][0]
    spec = _basic_modifier_family(rule) or format_from_event_raw(manipulator["from"])
    lines = [f"basic {spec}:"]

    modifiers = manipulator["from"].get("modifiers", {})
    mandatory = modifiers.get("mandatory", [])
    optional = modifiers.get("optional", [])
    if mandatory and "simultaneous" in manipulator["from"]:
        lines.append("  mandatory " +
                     " ".join(reverse_modifier(m) for m in mandatory))
    if optional:
        lines.append("  optional " +
                     " ".join(reverse_modifier(m) for m in optional))
    elif ("simultaneous" in manipulator["from"]
          or is_modifier_mapping(manipulator["from"], manipulator.get("to", []))):
        lines.append("  optional none")

    field_aliases = {
        "to": "",
        "to_if_alone": "alone",
        "to_if_held_down": "held",
        "to_after_key_up": "after up",
    }

    for field in ("to", "to_if_alone", "to_if_held_down", "to_after_key_up"):
        for event in manipulator.get(field, []):
            lines.append("  " +
                         decompile_action_event(field_aliases[field], event))

    delayed = manipulator.get("to_delayed_action", {})
    delayed_aliases = {
        "to_if_canceled": "if canceled",
        "to_if_invoked": "if invoked",
    }
    for subfield in ("to_if_canceled", "to_if_invoked"):
        for event in delayed.get(subfield, []):
            lines.append(
                "  " +
                decompile_action_event(delayed_aliases[subfield], event))

    for name, value in manipulator.get("parameters", {}).items():
        lines.append(f"  {pretty_parameter_line(name, value)}")

    return lines
def find_expansion_name(values):
    for name, expanded in MULTI_KEY_EXPANSION.items():
        if list(values) == list(expanded):
            return name
    for name, expanded in ARROW_EXPANSION.items():
        if list(values) == list(expanded):
            return name
    return None
def extract_send_event_parts(event):
    key_name = reverse_key(
        event.get("consumer_key_code") or event.get("key_code"))
    modifiers = [reverse_modifier(m) for m in event.get("modifiers", [])]
    return modifiers, key_name
def decompile_mapping_manipulators(manipulators):
    lines = []
    expansion_groups = sorted(
        list(MULTI_KEY_EXPANSION.items()) + list(ARROW_EXPANSION.items()),
        key=lambda item: len(item[1]),
        reverse=True,
    )

    index = 0
    while index < len(manipulators):
        compressed_line = None
        consumed = 0

        for from_name, from_values in expansion_groups:
            size = len(from_values)
            group = manipulators[index:index + size]
            if len(group) != size:
                continue

            group_from_values = [
                reverse_key(m["from"].get("consumer_key_code")
                            or m["from"].get("key_code")) for m in group
            ]
            if group_from_values != list(from_values):
                continue

            modifier_policy = group[0]["from"].get("modifiers", {})
            if any(
                    m["from"].get("modifiers", {}) != modifier_policy
                    for m in group[1:]):
                continue
            passthrough = format_passthrough(group[0]["from"])
            prefix = "any+" if "any" in modifier_policy.get("optional", []) else ""

            verbs_and_targets = [
                event_to_verb_target(m["to"][0]) for m in group
            ]
            verbs = {verb for verb, _ in verbs_and_targets}
            if len(verbs) != 1:
                continue

            verb = verbs_and_targets[0][0]
            if verb == "sends":
                send_parts = [
                    extract_send_event_parts(m["to"][0]) for m in group
                ]
                modifiers = send_parts[0][0]
                if any(mods != modifiers for mods, _ in send_parts[1:]):
                    continue

                target_values = [key for _, key in send_parts]
                target_name = find_expansion_name(target_values)
                if target_name:
                    target = "+".join(
                        modifiers +
                        [target_name]) if modifiers else target_name
                elif modifiers:
                    continue
                else:
                    target = " ".join(target_values)
            else:
                target_values = [target for _, target in verbs_and_targets]
                if len(set(target_values)) != 1:
                    continue
                target = target_values[0]

            compressed_line = f"{prefix}{from_name} {verb} {target}{passthrough}"
            consumed = size
            break

        if compressed_line:
            lines.append(compressed_line)
            index += consumed
            continue

        lines.append(decompile_mapping_manipulator(manipulators[index]))
        index += 1

    return lines
def decompile_numpad_mapping_lines(manipulators):
    lines = []
    for manipulator in manipulators:
        to_events = manipulator.get("to", [])
        is_simple = (len(to_events) == 1 and "set_variable" not in to_events[0]
                     and "mouse_key" not in to_events[0]
                     and to_events[0].get("key_code") != "vk_none")
        if (is_modifier_mapping(manipulator["from"], to_events)
                and not manipulator["from"].get("modifiers", {}).get("optional")):
            is_simple = False
        if is_simple:
            lines.append(decompile_mapping_manipulator(manipulator))
            continue
        lines.extend(
            decompile_advanced_basic_rule({
                "description": "numpad mapping",
                "manipulators": [manipulator],
            }))
    return lines
def numpad_device_from_rule(rule):
    for manipulator in rule.get("manipulators", []):
        for condition in manipulator.get("conditions", []):
            if condition.get("type") != "device_if":
                continue
            for identifier in condition.get("identifiers", []):
                vendor_id = identifier.get("vendor_id")
                product_id = identifier.get("product_id")
                if vendor_id is not None and product_id is not None:
                    return f"{vendor_id}:{product_id}"
                if "product_name" in identifier:
                    return identifier["product_name"]
    return None
def numpad_queue_mode_from_manipulator(manipulator):
    for event in manipulator.get("to", []):
        command = event.get("shell_command", "")
        if "--queue-mode" not in command:
            continue
        try:
            parts = shlex.split(command)
        except ValueError:
            parts = command.split()
        if "--queue-mode" not in parts:
            continue
        index = parts.index("--queue-mode")
        if index + 1 < len(parts):
            return parts[index + 1]
    return None
def decompile_numpad_rules(rules):
    consumed = set()
    numpads = {}

    def ensure_numpad(name):
        return numpads.setdefault(
            name, {
                "name": name,
                "device": None,
                "base": {
                    "default": [],
                    "apps": [],
                },
                "layers": {},
                "mode_t9": False,
                "mode_neru": False,
            })

    def ensure_layer(numpad, layer_id):
        return numpad["layers"].setdefault(
            layer_id, {
                "id": layer_id,
                "key": None,
                "entries": [],
                "default": [],
                "apps": [],
            })

    for rule in rules:
        description = rule.get("description", "")
        if not description.startswith("numpad "):
            continue
        consumed.add(description)

        mode_match = re.fullmatch(r"numpad\s+(\S+)\s+(T9|Neru)\s+mode",
                                  description)
        if mode_match:
            numpad = ensure_numpad(mode_match.group(1))
            numpad["device"] = numpad["device"] or numpad_device_from_rule(rule)
            if mode_match.group(2) == "T9":
                numpad["mode_t9"] = True
            else:
                numpad["mode_neru"] = True
            continue

        layer_match = re.fullmatch(r"numpad\s+(\S+)\s+(\S+)\s+\+\s+(\S+)",
                                    description)
        if not layer_match:
            layer_match = re.fullmatch(
                r"numpad\s+(\S+)\s+hold\s+(\S+)\s+as\s+(\S+)",
                description)
        if layer_match:
            numpad = ensure_numpad(layer_match.group(1))
            numpad["device"] = numpad["device"] or numpad_device_from_rule(rule)
            layer = ensure_layer(numpad, layer_match.group(3))
            layer["key"] = layer_match.group(2)
            continue

        entries_match = re.fullmatch(
            r"numpad\s+(\S+)\s+(\S+)\s+mode entries", description)
        if entries_match:
            numpad = ensure_numpad(entries_match.group(1))
            numpad["device"] = numpad["device"] or numpad_device_from_rule(rule)
            layer = ensure_layer(numpad, entries_match.group(2))
            for manipulator in rule.get("manipulators", []):
                from_key = format_from_event_raw(manipulator["from"])
                queue_mode = numpad_queue_mode_from_manipulator(manipulator)
                if queue_mode == "t9":
                    layer["entries"].append(f"{from_key} enters t9")
                elif queue_mode == "neru-left":
                    layer["entries"].append(
                        f"{from_key} enters neru left_click")
                elif queue_mode == "neru-right":
                    layer["entries"].append(
                        f"{from_key} enters neru right_click")
                elif queue_mode == "neru":
                    layer["entries"].append(f"{from_key} enters neru")
            continue

        base_default_match = re.fullmatch(r"numpad\s+(\S+)\s+base default",
                                          description)
        if base_default_match:
            numpad = ensure_numpad(base_default_match.group(1))
            numpad["device"] = numpad["device"] or numpad_device_from_rule(rule)
            numpad["base"]["default"].extend(
                decompile_numpad_mapping_lines(rule.get("manipulators", [])))
            continue

        base_app_match = re.fullmatch(r"numpad\s+(\S+)\s+base\s+\((.+)\)",
                                      description)
        if base_app_match:
            numpad = ensure_numpad(base_app_match.group(1))
            numpad["device"] = numpad["device"] or numpad_device_from_rule(rule)
            numpad["base"]["apps"].append({
                "apps":
                base_app_match.group(2),
                "lines":
                decompile_numpad_mapping_lines(rule.get("manipulators", [])),
            })
            continue

        layer_default_match = re.fullmatch(
            r"numpad\s+(\S+)\s+(\S+)\s+layer default", description)
        if layer_default_match:
            numpad = ensure_numpad(layer_default_match.group(1))
            numpad["device"] = numpad["device"] or numpad_device_from_rule(rule)
            layer = ensure_layer(numpad, layer_default_match.group(2))
            layer["default"].extend(
                decompile_numpad_mapping_lines(rule.get("manipulators", [])))
            continue

        layer_app_match = re.fullmatch(
            r"numpad\s+(\S+)\s+(\S+)\s+layer\s+\((.+)\)", description)
        if layer_app_match:
            numpad = ensure_numpad(layer_app_match.group(1))
            numpad["device"] = numpad["device"] or numpad_device_from_rule(rule)
            layer = ensure_layer(numpad, layer_app_match.group(2))
            layer["apps"].append({
                "apps":
                layer_app_match.group(3),
                "lines":
                decompile_numpad_mapping_lines(rule.get("manipulators", [])),
            })

    blocks = []
    for numpad in numpads.values():
        lines = []
        if numpad.get("device") and not re.fullmatch(r"\d+:\d+",
                                                     numpad["name"]):
            lines.append(f"device {numpad['name']} {numpad['device']}")
            lines.append("")
        lines.append(f"numpad {numpad['name']}:")
        lines.append("  hud on")

        base = numpad["base"]
        if base["default"] or base["apps"]:
            lines.extend(["", "  base:"])
            if base["default"]:
                lines.append("    default:")
                lines.extend(indent_lines(base["default"], "      "))
            for app_section in base["apps"]:
                if lines[-1] != "":
                    lines.append("")
                lines.append(f"    when app {app_section['apps']}:")
                lines.extend(indent_lines(app_section["lines"], "      "))

        for layer in numpad["layers"].values():
            lines.extend(["", f"  {layer.get('key')} + {layer['id']}:"])
            lines.extend(indent_lines(layer["entries"], "    "))
            if layer["default"]:
                lines.extend(["", "    default:"])
                lines.extend(indent_lines(layer["default"], "      "))
            for app_section in layer["apps"]:
                if lines[-1] != "":
                    lines.append("")
                lines.append(f"    when app {app_section['apps']}:")
                lines.extend(indent_lines(app_section["lines"], "      "))

        if numpad["mode_t9"]:
            lines.extend([
                "",
                "  mode t9:",
                "    keypad_num_lock exits",
                "    keypad_slash clears",
                "    keypad_asterisk commits",
                "    delete_or_backspace backtracks",
                "    keypad_7 types t9 7",
                "    keypad_8 types t9 8",
                "    keypad_9 types t9 9",
                "    keypad_hyphen types t9 -",
                "    keypad_4 types t9 4",
                "    keypad_5 types t9 5",
                "    keypad_6 types t9 6",
                "    keypad_plus types t9 +",
                "    keypad_1 types t9 1",
                "    keypad_2 types t9 2",
                "    keypad_3 types t9 3",
                "    keypad_0 types t9 0",
                "    keypad_period types t9 1",
                "    keypad_enter commits return",
            ])

        if numpad["mode_neru"]:
            lines.extend([
                "",
                "  mode neru:",
                "    keypad_num_lock exits",
                "    keypad_slash clicks left then exits",
                "    keypad_asterisk clicks right then exits",
                "    delete_or_backspace backtracks",
                "    keypad_hyphen backtracks",
                "    keypad_plus selects center",
                "    keypad_0 resets",
                "    keypad_period backtracks",
                "    keypad_enter clicks left then exits",
                "    keys keypad_1..keypad_9 pass through",
            ])

        blocks.append(lines)

    return blocks, consumed
def decompile_hyper_rule(rule):
    manipulator = rule["manipulators"][0]
    lines = [
        "hyper is $", f"$ is {reverse_key(manipulator['from']['key_code'])}"
    ]
    delayed = manipulator.get("to_delayed_action", {})
    if ("| hints" in rule.get("description", "") or any(
            "set_notification_message" in event and
            event["set_notification_message"].get("id") == HINT_NOTIFICATION_ID
            and event["set_notification_message"].get("text")
            for event in delayed.get("to_if_invoked", []))
            or any("shell_command" in event
                   and str(HINT_HELPER_BINARY) in event["shell_command"]
                   for event in delayed.get("to_if_invoked", []))):
        lines.extend(["", "hints on"])
    if manipulator.get("to_if_alone"):
        verb, target = event_to_verb_target(manipulator["to_if_alone"][0])
        lines.extend(["", f"$ tap {verb} {target}"])
    return lines
def simple_modification_key_pair(modification):
    from_key = modification.get("from", {}).get("key_code")
    to_events = modification.get("to", [])
    if not from_key or len(to_events) != 1:
        return None
    to_key = to_events[0].get("key_code")
    if not to_key:
        return None
    return from_key, to_key
def device_swap_pair_set(first, second):
    result = set()
    first_keys = resolve_side_modifier_keys(first)
    second_keys = resolve_side_modifier_keys(second)
    for first_key, second_key in zip(first_keys, second_keys):
        result.add((first_key, second_key))
        result.add((second_key, first_key))
    return result
def decompile_profile_devices(profile):
    lines = []
    modifier_names = tuple(SIDE_MODIFIER_KEYS.keys()) + tuple(
        key for keys in SIDE_MODIFIER_KEYS.values() for key in keys)
    swap_candidates = [(first, second)
                       for index, first in enumerate(modifier_names)
                       for second in modifier_names[index + 1:]
                       if len(resolve_side_modifier_keys(first)) ==
                       len(resolve_side_modifier_keys(second))]

    for device in profile.get("devices", []):
        identifiers = device.get("identifiers", {})
        vendor_id = identifiers.get("vendor_id")
        product_id = identifiers.get("product_id")
        if vendor_id is None or product_id is None:
            continue
        device_kind = " keyboard+pointing" if identifiers.get(
            "is_pointing_device") else ""

        modification_pairs = {
            pair
            for pair in (
                simple_modification_key_pair(modification)
                for modification in device.get("simple_modifications", []))
            if pair
        }
        if device.get("ignore") is False:
            lines.append(f"device {vendor_id}:{product_id}{device_kind} enabled")
        if device.get("ignore") is True:
            lines.append(f"device {vendor_id}:{product_id}{device_kind} ignored")
        if not modification_pairs:
            continue

        for first, second in swap_candidates:
            if modification_pairs == device_swap_pair_set(first, second):
                lines.append(
                    f"device {vendor_id}:{product_id}{device_kind} swaps {first} {second}"
                )
                break

    return lines
def decompile_standalone_rule(rule, hyper_key, hyper_var):
    description = rule["description"]
    manipulators = rule.get("manipulators", [])
    conditions = []
    if manipulators:
        conditions = conditions_from_manipulator(manipulators[0], {hyper_var})

    if (hyper_key and description.startswith(f"{hyper_key} + ")
            and manipulators and any(
                c.get("type") == "variable_if" and c.get("name") == hyper_var
                for c in manipulators[0].get("conditions", []))):
        description = "$ + " + description.split(" + ", 1)[1]

    return conditions, [description]
def is_parseable_description(description):
    patterns = (
        r'^\S+\s+together\s+(sends|opens|runs)\s+.+$',
        r'^\S+\s+\+\s+\S+\s+(sends|opens|runs)\s+.+$',
        r'^\S+\s+(sends|opens|runs)\s+.+$',
    )
    return any(re.match(pattern, description) for pattern in patterns)
def decompile_json_object(data):
    profiles = data.get("profiles", [])
    if profiles:
        profile = profiles[0]
        rules = profile.get("complex_modifications", {}).get("rules", [])
        device_lines = decompile_profile_devices(profile)
        title = data.get("title", DEFAULT_TITLE)
    else:
        rules = data.get("rules", [])
        device_lines = []
        title = data.get("title", DEFAULT_TITLE)
    hyper_key = None
    hyper_var = None
    hold_blocks = []
    sublayer_blocks = []
    numpad_blocks, numpad_rule_descriptions = decompile_numpad_rules(rules)
    standalone_entries = []
    leading_lines = []

    for rule in rules:
        description = rule.get("description", "")
        manipulators = rule.get("manipulators", [])

        if description in numpad_rule_descriptions:
            continue

        if "| hold=hyper" in description and manipulators:
            leading_lines.extend(decompile_hyper_rule(rule))
            hyper_key = reverse_key(manipulators[0]["from"]["key_code"])
            hyper_var = manipulators[0]["to"][0]["set_variable"]["name"]
            continue

        if (len(manipulators) == 1 and manipulators[0].get("to_if_alone")
                and manipulators[0].get("to")
                and "set_variable" not in manipulators[0]["to"][0]):
            standalone_entries.append(
                (conditions_from_manipulator(manipulators[0]),
                 decompile_tap_hold_rule(rule)))
            continue

        if description.startswith("$:") and description.endswith(
                " activation"):
            continue

        if (_basic_modifier_family(rule) is not None or (len(manipulators) == 1
                and (manipulators[0].get("to_if_held_down")
                     or manipulators[0].get("to_delayed_action") or any(
                         event.get("halt")
                         for event in manipulators[0].get("to_if_alone", []))
                     or manipulators[0].get("to_after_key_up")
                      or description.startswith("basic ")
                      or len(manipulators[0].get("to", [])) > 1
                     or any("set_variable" in event
                            for field in ("to", "to_if_alone",
                                          "to_if_held_down", "to_after_key_up")
                            for event in manipulators[0].get(field, []))
                     or manipulators[0].get("parameters")))):
            standalone_entries.append(
                (conditions_from_manipulator(manipulators[0]),
                 decompile_advanced_basic_rule(rule)))
            continue

        if description.startswith("$: hold layer"):
            ignore_names = {hyper_var} if hyper_var else set()
            block_conditions = conditions_from_manipulator(
                manipulators[0], ignore_names) if manipulators else []
            block_lines = ["$:"] + indent_lines(
                decompile_mapping_manipulators(manipulators))
            hold_blocks.append(
                wrap_with_conditions(block_conditions, block_lines))
            continue

        sublayer_match = re.fullmatch(r"\$:(.+) mappings", description)
        if sublayer_match:
            sub_key = sublayer_match.group(1)
            ignore_names = {hyper_var, f"sublayer_{sub_key}_active"}
            block_conditions = conditions_from_manipulator(
                manipulators[0], ignore_names) if manipulators else []
            block_lines = [f"$:{sub_key}"] + indent_lines(
                decompile_mapping_manipulators(manipulators))
            sublayer_blocks.append(
                wrap_with_conditions(block_conditions, block_lines))
            continue

        if (manipulators and not is_parseable_description(description) and all(
                m.get("type") == "basic" and m.get("to")
                and not m.get("to_if_alone") and not m.get("to_if_held_down")
                and not m.get("to_delayed_action") and not m.get("parameters")
                for m in manipulators)):
            block_conditions = conditions_from_manipulator(
                manipulators[0], {hyper_var} if hyper_var else set())
            block_lines = decompile_mapping_manipulators(manipulators)
            standalone_entries.append((block_conditions, block_lines))
            continue

        standalone_entries.append(
            decompile_standalone_rule(rule, hyper_key, hyper_var))

    standalone_blocks = merge_conditioned_blocks(standalone_entries)

    lines = []
    if title != DEFAULT_TITLE:
        lines.append(f'title "{title}"')
        lines.append("")

    lines.extend(leading_lines)

    if device_lines:
        if lines:
            lines.append("")
        lines.extend(device_lines)

    sections = [hold_blocks, sublayer_blocks, standalone_blocks, numpad_blocks]
    for section in sections:
        if not section:
            continue
        if lines:
            lines.append("")
        for index, block in enumerate(section):
            if index:
                lines.append("")
            lines.extend(block)

    return "\n".join(lines).rstrip() + "\n"
def reverse_json_file(path):
    data = json.loads(Path(path).read_text())
    return decompile_json_object(data)
