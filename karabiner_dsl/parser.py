"""Parser: .kb source text into the intermediate block tree."""

import copy
import json
import re
from pathlib import Path

from .language import read_language
from .origins import (
    Origin,
    SourceLocation,
    SourceRecord,
    SourceUnit,
    carry_origin,
    origin_of,
)

from .keys import (
    ARROW_EXPANSION,
    DEFAULT_TITLE,
    MULTI_KEY_EXPANSION,
    expand_key_specs,
    expand_multi,
    is_modifier_alias,
    parse_device_ids,
    parse_variable_name,
    resolve_modifier,
    resolve_side_modifier_keys,
    strip_inline_comment,
)


class Parser:

    def __init__(self, text, source_path=None):
        self.language = read_language(text)
        self.lines = text.splitlines()
        self.source_path = Path(source_path).resolve() if source_path else None
        self.units = []
        self._units_by_id = {}
        self._tap_units = {}
        self.hyper_binding = None
        self.hyper_tap_origin = None
        self.sublayer_units = {}
        self.action_definitions = {}
        self.title = DEFAULT_TITLE
        self.hints_enabled = False
        self.hyper_key = None
        self.hyper_alias = None
        self.hyper_tap = None
        self.tap_hold = []
        self.advanced_basic = []
        self.device_modifications = []
        self.device_aliases = {}
        self.device_definitions = {}
        self.actions = {}
        self.numpads = []
        self.hold_mappings = []
        self.sublayers = {}
        self.standalone = []
        self.warnings = []
        self._parse()
        self._apply_device_defaults()
        if self.hyper_binding is not None and self.hyper_tap_origin:
            unit = self._units_by_id[self.hyper_binding.origin.unit_id]
            unit.declarations.append(self.hyper_tap_origin)
        self.units.sort(key=lambda unit: unit.position)

    def _location(self, line):
        return SourceLocation(line, self._get_indent(self.lines[line - 1]) + 1,
                              str(self.source_path) if self.source_path else None)

    def _unit(self, line, kind="inline", group_id=None):
        unit_id = f"u{line}"
        if unit_id not in self._units_by_id:
            location = self._location(line)
            unit = SourceUnit(unit_id, kind, location, group_id, [location])
            self._units_by_id[unit_id] = unit
            self.units.append(unit)
        return self._units_by_id[unit_id]

    def _mark(self, record, line, kind="inline", group_id=None):
        unit = self._unit(line, kind, group_id)
        return SourceRecord(record, origin=Origin(self._location(line), unit.id))

    def _mark_action(self, action, line, definition=None):
        if action is None:
            return None
        related = (definition,) if definition else ()
        return SourceRecord(action, origin=Origin(self._location(line), related=related))

    def _mark_standalone(self, line, starts):
        for records, start in zip((self.standalone, self.tap_hold, self.advanced_basic), starts):
            for index in range(start, len(records)):
                record = records[index]
                if origin_of(record).unit_id:
                    continue
                kind = "simultaneous" if record.get("type") == "together" else "inline"
                marked = self._mark(record, line, kind)
                if records is self.tap_hold:
                    key = json.dumps([record["from"], record.get("conditions", []),
                                      record.get("passthrough", [])], sort_keys=True)
                    if key in self._tap_units:
                        unit = self._tap_units[key]
                        unused = self._units_by_id.pop(marked.origin.unit_id)
                        self.units.remove(unused)
                        unit.declarations.append(self._location(line))
                    else:
                        unit = self._units_by_id[marked.origin.unit_id]
                        unit.kind = "tap_hold"
                        self._tap_units[key] = unit
                    marked.origin = Origin(self._location(line), unit.id)
                records[index] = marked

    def _device_default_variants(self, conditions):
        ignored = {self.device_aliases.get(name, name)
                   for name, device in self.device_definitions.items()
                   if device.get("ignores_modifiers")}
        variants = [(list(conditions), False)]
        for index, condition in enumerate(conditions):
            if condition["type"] != "device":
                continue
            groups = {False: [], True: []}
            for value in condition["values"]:
                groups[self.device_aliases.get(value, value) in ignored].append(value)
            variants = [(current[:index] + [{**condition, "values": values}] + current[index + 1:], inherited or ignores)
                        for current, inherited in variants
                        for ignores, values in groups.items() if values]
        return variants

    def _apply_mapping_defaults(self, mappings, optional_field):
        result = []
        for mapping in mappings:
            if mapping.get(optional_field) or mapping.get("optional_explicit"):
                result.append(mapping)
                continue
            for conditions, ignores in self._device_default_variants(mapping.get("conditions", [])):
                item = copy.deepcopy(mapping)
                item["conditions"] = conditions
                if ignores:
                    item[optional_field] = ["any"]
                result.append(item)
        return result

    def _apply_device_defaults(self):
        self.advanced_basic = self._apply_mapping_defaults(self.advanced_basic, "optional")
        self.hold_mappings = self._apply_mapping_defaults(self.hold_mappings, "passthrough")
        for key, mappings in self.sublayers.items():
            self.sublayers[key] = self._apply_mapping_defaults(mappings, "passthrough")
        standalone = []
        for mapping in self.standalone:
            if mapping.get("passthrough"):
                standalone.append(mapping)
                continue
            for conditions, ignores in self._device_default_variants(mapping.get("conditions", [])):
                item = carry_origin({**mapping, "conditions": conditions}, mapping)
                if ignores and item["type"] == "simple":
                    # This is the same basic tier as the old any+ spelling.
                    self._append_basic(carry_origin({"type": "advanced_basic", "from": item["from"],
                                        "mandatory": [], "optional": ["any"], "parameters": {},
                                        "conditions": conditions, "source_line": item["source_line"],
                                        "actions": {"to": [{"field": "to", "kind": "event",
                                                            "verb": item["verb"], "target": item["target"]}]}}, item))
                else:
                    if ignores:
                        item["passthrough"] = ["any"]
                    standalone.append(item)
        self.standalone = standalone
        self.advanced_basic.sort(key=lambda block: block.get("source_line") or 0)
        for numpad in self.numpads:
            numpad["ignores_modifiers"] = self._device_default_variants([
                {"type": "device", "values": [numpad["name"]]}])[0][1]

    def _get_indent(self, line):
        return len(line) - len(line.lstrip())

    def _strip_comment(self, line):
        return strip_inline_comment(line)

    def _parse(self):
        i = 0
        while i < len(self.lines):
            i = self._parse_at(i, indent_floor=0, conditions=[])

    def _parse_at(self, i, indent_floor, conditions):
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()

            if i + 1 == self.language.header_line:
                i += 1
                continue

            if not line:
                i += 1
                continue

            indent = self._get_indent(raw)
            if indent < indent_floor:
                return i

            if line.startswith("title "):
                self.title = line[6:].strip().strip('"').strip("'")
                i += 1

            elif line == "hints on":
                self.hints_enabled = True
                i += 1

            elif line == "hints off":
                self.hints_enabled = False
                i += 1

            elif line == "devices:":
                i = self._parse_devices(i, indent)

            elif line == "actions:":
                i = self._parse_actions(i, indent)

            elif re.match(r"^device\s+[A-Za-z_][A-Za-z0-9_-]*\s+\d+:\d+$",
                          line):
                self._parse_device_alias(line)
                i += 1

            elif re.match(r"^numpad\s+\S+:$", line):
                i = self._parse_numpad(i, indent)

            elif re.match(
                    r"^device\s+\d+:\d+(?:\s+keyboard(?:\+pointing)?)?\s+swaps?\s+\S+\s+\S+$",
                    line):
                self._parse_device_swap(line)
                i += 1

            elif re.match(
                    r"^device\s+\d+:\d+(?:\s+keyboard(?:\+pointing)?)?\s+(?:enabled|ignored)$",
                    line):
                self._parse_device_enabled(line)
                i += 1

            elif line == "hyper is $":
                self.hyper_alias = "$"
                i += 1

            elif re.match(r'^\$\s+is\s+\S+$', line):
                alias_match = re.match(r'^\$\s+is\s+(\S+)$', line)
                if alias_match:
                    self.hyper_binding = self._mark({}, i + 1, "hyper_activation")
                    self.hyper_key = alias_match.group(1)
                    self.hyper_alias = "$"
                i += 1

            elif line.startswith("hyper "):
                self.hyper_binding = self._mark({}, i + 1, "hyper_activation")
                hyper_match = re.match(r'^hyper\s+(\S+?)(?:\s+as\s+\$)?$',
                                       line)
                if hyper_match:
                    self.hyper_key = hyper_match.group(1)
                    if line.endswith(" as $"):
                        self.hyper_alias = "$"
                else:
                    self.hyper_key = line[6:].strip()
                i += 1

            elif re.match(r'^(?:basic\s+)?[^\s:]+(?:\s*,\s*[^\s:]+)*(?: together)?:$', line) and line != "$:":
                i = self._parse_advanced_basic(i, indent, conditions)

            elif line == "$:":
                i = self._parse_block(i, indent, self.hold_mappings,
                                      conditions)

            elif re.match(r'^\$:[a-zA-Z0-9_]+$', line):
                sub_key = line[2:]
                if sub_key not in self.sublayers:
                    self.sublayers[sub_key] = []
                i = self._parse_block(i, indent, self.sublayers[sub_key],
                                      conditions)

            elif line.startswith("$ tap "):
                if self._parse_hyper_tap(line):
                    self.hyper_tap_origin = self._location(i + 1)
                i += 1

            elif line.startswith("$ + "):
                # $ + arrows sends ... → standalone combo with hyper trigger
                start = len(self.standalone)
                self._parse_hyper_combo(line, conditions)
                for index in range(start, len(self.standalone)):
                    self.standalone[index] = self._mark(self.standalone[index], i + 1)
                i += 1

            elif line.startswith("when "):
                i = self._parse_when(i, indent, conditions)

            else:
                starts = tuple(map(len, (self.standalone, self.tap_hold, self.advanced_basic)))
                self._parse_standalone(line, conditions, line_number=i + 1)
                self._mark_standalone(i + 1, starts)
                i += 1

        return i

    def _parse_block(self, i, block_indent, target_list, conditions):
        header_line = i + 1
        group_id = f"g{header_line}"
        if self._strip_comment(self.lines[i]).strip() != "$:":
            sub_key = self._strip_comment(self.lines[i]).strip()[2:]
            control = self._mark({}, header_line, "sublayer_activation", group_id)
            self.sublayer_units.setdefault(sub_key, []).append(control)
        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)

            if not line:
                i += 1
                continue

            if child_indent <= block_indent:
                break

            mappings = self._parse_mapping_line(line, conditions)
            if not mappings:
                self.warnings.append(
                    f"line {i + 1}: unrecognized mapping: {line}")
            trigger = re.split(r"\s+(?:sends|opens|runs)\s+", line, maxsplit=1)[0]
            kind = "comma" if len(self._parse_comma_list(trigger)) > 1 else "inline"
            target_list.extend(self._mark(m, i + 1, kind, group_id) for m in mappings)
            i += 1

        return i

    def _split_passthrough(self, line):
        """Keep legacy +modifier suffixes separate from action payloads."""
        passthrough = []
        clean_parts = []
        for token in line.split():
            if token.startswith("+") and len(token) > 1 and is_modifier_alias(token[1:]):
                passthrough.append(resolve_modifier(token[1:]))
            else:
                clean_parts.append(token)
        return " ".join(clean_parts), passthrough

    def _parse_mapping_line(self, line, conditions=None):
        if conditions is None:
            conditions = []

        match = re.fullmatch(r"(.+?)\s+((?:sends|opens|runs)\s+.+)", line)
        if match and "," in match.group(1):
            return [mapping for key in self._parse_comma_list(match.group(1))
                    for mapping in self._parse_mapping_line(f"{key} {match.group(2)}", conditions)]
        if match and match.group(1).startswith("any+"):
            mappings = self._parse_mapping_line(f"{match.group(1)[4:]} {match.group(2)}", conditions)
            for mapping in mappings:
                mapping["passthrough"] = ["any"]
            return mappings

        line, passthrough = self._split_passthrough(line)

        for verb in ("sends", "opens", "runs"):
            if f" {verb} " in line:
                from_part, to_part = line.split(f" {verb} ", 1)
                from_part = from_part.strip()
                to_part = to_part.strip()

                if from_part in MULTI_KEY_EXPANSION or from_part in ARROW_EXPANSION:
                    # Check if to_part is directly expandable
                    if to_part in ARROW_EXPANSION or " " in to_part:
                        pairs = expand_multi(from_part, to_part)
                        return [{
                            "from": fk,
                            "verb": verb,
                            "to": tv,
                            "passthrough": passthrough,
                            "conditions": list(conditions),
                        } for fk, tv in pairs]

                    # Check if trailing part of a combo is expandable
                    # e.g., ctrl+option+cmd+arrows → expand 'arrows'
                    if "+" in to_part:
                        to_tail = to_part.rsplit("+", 1)[-1]
                        if to_tail in ARROW_EXPANSION or to_tail in MULTI_KEY_EXPANSION:
                            to_prefix = to_part.rsplit("+", 1)[0] + "+"
                            to_expanded = ARROW_EXPANSION.get(
                                to_tail) or MULTI_KEY_EXPANSION.get(to_tail)
                            from_expanded = MULTI_KEY_EXPANSION.get(
                                from_part) or ARROW_EXPANSION.get(from_part)
                            if len(to_expanded) == len(from_expanded):
                                return [{
                                    "from": fk,
                                    "verb": verb,
                                    "to": to_prefix + tv,
                                    "passthrough": passthrough,
                                    "conditions": list(conditions),
                                } for fk, tv in zip(from_expanded, to_expanded)
                                        ]

                return [{
                    "from": from_part,
                    "verb": verb,
                    "to": to_part,
                    "passthrough": passthrough,
                    "conditions": list(conditions),
                }]

        return []

    def _parse_hyper_tap(self, line):
        m = re.match(r'\$\s+tap\s+(sends|opens|runs)\s+(.+)', line)
        if m:
            self.hyper_tap = (m.group(1), m.group(2).strip())
            return True

    def _parse_device_swap(self, line):
        m = re.match(
            r"^device\s+(\d+:\d+)(?:\s+(keyboard(?:\+pointing)?))?\s+swaps?\s+(\S+)\s+(\S+)$",
            line)
        if not m:
            return

        vendor_id, product_id = parse_device_ids(m.group(1))
        device_kind = m.group(2) or "keyboard"
        self.device_modifications.append({
            "type": "swap",
            "vendor_id": vendor_id,
            "product_id": product_id,
            "is_pointing_device": device_kind == "keyboard+pointing",
            "first": m.group(3),
            "second": m.group(4),
        })

    def _parse_device_enabled(self, line):
        m = re.match(
            r"^device\s+(\d+:\d+)(?:\s+(keyboard(?:\+pointing)?))?\s+(enabled|ignored)$",
            line)
        if not m:
            return

        vendor_id, product_id = parse_device_ids(m.group(1))
        device_kind = m.group(2) or "keyboard"
        self.device_modifications.append({
            "type": m.group(3),
            "vendor_id": vendor_id,
            "product_id": product_id,
            "is_pointing_device": device_kind == "keyboard+pointing",
        })

    def _parse_device_alias(self, line):
        m = re.match(r"^device\s+([A-Za-z_][A-Za-z0-9_-]*)\s+(\d+:\d+)$",
                     line)
        if not m:
            return
        self.device_aliases[m.group(1)] = m.group(2)
        self.device_definitions.setdefault(m.group(1), {
            "name": m.group(1),
            "id": m.group(2),
            "keys": {},
            "rows": {},
        })

    def _parse_devices(self, i, block_indent):
        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)

            if not line:
                i += 1
                continue
            if child_indent <= block_indent:
                break

            if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*:$", line):
                i = self._parse_device_definition(i, child_indent)
            else:
                i += 1
        return i

    def _parse_device_definition(self, i, block_indent):
        header = self._strip_comment(self.lines[i]).strip()
        name = header[:-1]
        device = self.device_definitions.setdefault(
            name, {
                "name": name,
                "id": None,
                "keys": {},
                "rows": {},
            })
        current_kind = device.get("kind", "keyboard")
        modifications = []

        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)

            if not line:
                i += 1
                continue
            if child_indent <= block_indent:
                break

            if line.startswith("id "):
                device["id"] = line[3:].strip()
                self.device_aliases[name] = device["id"]
                i += 1
                continue

            if line.startswith("kind "):
                current_kind = line[5:].strip()
                device["kind"] = current_kind
                i += 1
                continue

            if line == "ignores modifiers":
                device["ignores_modifiers"] = True
                i += 1
                continue

            kind_enabled = re.match(r"^(keyboard(?:\+pointing)?)\s+enabled$",
                                    line)
            if kind_enabled:
                current_kind = kind_enabled.group(1)
                modifications.append(("enabled", current_kind))
                i += 1
                continue

            kind_swap = re.match(
                r"^(keyboard(?:\+pointing)?)\s+swaps?\s+(\S+)\s+(\S+)$",
                line)
            if kind_swap:
                current_kind = kind_swap.group(1)
                modifications.append(("swap", current_kind,
                                      kind_swap.group(2), kind_swap.group(3)))
                i += 1
                continue

            if line in ("enabled", "ignored"):
                modifications.append((line, None))
                i += 1
                continue

            swap = re.match(r"^swaps?\s+(\S+)\s+(\S+)$", line)
            if swap:
                modifications.append(("swap", None, swap.group(1), swap.group(2)))
                i += 1
                continue

            if line == "keys:":
                i = self._parse_device_key_aliases(i, child_indent, device)
                continue

            if line == "rows:":
                i = self._parse_device_rows(i, child_indent, device)
                continue

            self.warnings.append(f"line {i + 1}: unrecognized device property: {line}")
            i += 1

        device["kind"] = current_kind
        for kind, explicit_kind, *keys in modifications:
            self.device_modifications.append({
                "type": kind,
                "device_name": name,
                "device_kind": explicit_kind,
                **({"first": keys[0], "second": keys[1]} if keys else {}),
            })
        return i

    def _parse_actions(self, i, block_indent):
        i += 1
        while i < len(self.lines):
            line = self._strip_comment(self.lines[i]).strip()
            indent = self._get_indent(self.lines[i])
            if not line:
                i += 1
                continue
            if indent <= block_indent:
                break
            match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_-]*):", line)
            if not match:
                raise ValueError(f"line {i + 1}: expected an action name: {line}")
            name = match.group(1)
            if name in self.actions:
                raise ValueError(f"Duplicate action: {name}")
            definition = self.action_definitions[name] = self._location(i + 1)
            steps = self.actions[name] = []
            i += 1
            while i < len(self.lines):
                step = self._strip_comment(self.lines[i]).strip()
                if not step:
                    i += 1
                    continue
                if self._get_indent(self.lines[i]) <= indent:
                    break
                action = self._mark_action(self._parse_advanced_action_line(step), i + 1, definition)
                if not action or action["field"] != "to":
                    raise ValueError(f"line {i + 1}: invalid action step: {step}")
                steps.append(action)
                i += 1
        return i

    def _add_device_enabled(self, device_id, device_kind="keyboard"):
        if not device_id:
            return
        vendor_id, product_id = parse_device_ids(device_id)
        self.device_modifications.append({
            "type": "enabled",
            "vendor_id": vendor_id,
            "product_id": product_id,
            "is_pointing_device": device_kind == "keyboard+pointing",
        })

    def _add_device_swap(self, device_id, device_kind, first, second):
        if not device_id:
            return
        vendor_id, product_id = parse_device_ids(device_id)
        self.device_modifications.append({
            "type": "swap",
            "vendor_id": vendor_id,
            "product_id": product_id,
            "is_pointing_device": device_kind == "keyboard+pointing",
            "first": first,
            "second": second,
        })

    def _parse_device_key_aliases(self, i, block_indent, device):
        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)
            if not line:
                i += 1
                continue
            if child_indent <= block_indent:
                break
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(\S+)$",
                             line)
            if match:
                device["keys"][match.group(1)] = match.group(2)
            i += 1
        return i

    def _parse_device_rows(self, i, block_indent, device):
        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)
            if not line:
                i += 1
                continue
            if child_indent <= block_indent:
                break
            match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*(.+)$",
                             line)
            if match:
                device["rows"][match.group(1)] = match.group(2).split()
            i += 1
        return i

    def _parse_numpad(self, i, block_indent):
        header = self._strip_comment(self.lines[i]).strip()
        m = re.match(r"^numpad\s+(\S+):$", header)
        if not m:
            return i + 1

        header_line = i + 1
        device_ref = m.group(1)
        device_definition = self.device_definitions.get(device_ref, {})
        numpad = {
            "device": self.device_aliases.get(device_ref, device_ref),
            "name": device_ref,
            "key_aliases": dict(device_definition.get("keys", {})),
            "rows": dict(device_definition.get("rows", {})),
            "hud": False,
            "base": {
                "default": [],
                "apps": [],
            },
            "layers": [],
            "modes": {},
        }

        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)

            if not line:
                i += 1
                continue
            if child_indent <= block_indent:
                break

            if line == "hud on":
                numpad["hud"] = True
                i += 1
            elif line == "base:":
                i = self._parse_numpad_mapping_section(
                    i, child_indent, numpad["base"])
            elif re.match(r"^\S+\s+\+\s+[A-Za-z_][A-Za-z0-9_-]*:$", line):
                match = re.match(r"^(\S+)\s+\+\s+([A-Za-z_][A-Za-z0-9_-]*):$",
                                 line)
                layer = {
                    "entries": [],
                    "default": [],
                    "apps": [],
                    "key": match.group(1),
                    "id": match.group(2),
                }
                layer["variable"] = f"numpad_{layer['id']}_layer"
                layer = self._mark(layer, i + 1, "numpad_layer_activation", f"g{header_line}")
                i = self._parse_numpad_layer_section(i, child_indent, layer)
                numpad["layers"].append(layer)
            elif re.match(r"^hold\s+\S+\s+as\s+[A-Za-z_][A-Za-z0-9_-]*:$",
                          line):
                layer = {
                    "entries": [],
                    "default": [],
                    "apps": [],
                }
                match = re.match(
                    r"^hold\s+(\S+)\s+as\s+([A-Za-z_][A-Za-z0-9_-]*):$",
                    line)
                layer["key"] = match.group(1)
                layer["id"] = match.group(2)
                layer["variable"] = f"numpad_{layer['id']}_layer"
                layer = self._mark(layer, i + 1, "numpad_layer_activation", f"g{header_line}")
                i = self._parse_numpad_layer_section(i, child_indent, layer)
                numpad["layers"].append(layer)
            elif re.match(r"^mode\s+[A-Za-z_][A-Za-z0-9_-]*:$", line):
                mode = re.match(r"^mode\s+([A-Za-z_][A-Za-z0-9_-]*):$",
                                line).group(1)
                actions = []
                i = self._parse_numpad_mode_section(i, child_indent, actions)
                numpad["modes"][mode] = actions
            else:
                i += 1

        self.numpads.append(self._mark(numpad, header_line, "numpad"))
        return i

    def _parse_numpad_mapping_section(self, i, section_indent, section):
        self._numpad_group_line = i + 1
        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)

            if not line:
                i += 1
                continue
            if child_indent <= section_indent:
                break

            if line == "default:":
                i = self._parse_numpad_mapping_lines(
                    i, child_indent, section["default"])
            elif line.startswith("when app "):
                app_text = line[len("when app "):].rstrip(":")
                app_values = self._parse_comma_list(app_text)
                app_section = {
                    "apps": app_values,
                    "mappings": [],
                }
                i = self._parse_numpad_mapping_lines(
                    i, child_indent, app_section["mappings"])
                section["apps"].append(app_section)
            else:
                i = self._parse_numpad_mapping_at(i, section["default"])
        return i

    def _parse_numpad_mapping_lines(self, i, parent_indent, target):
        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)

            if not line:
                i += 1
                continue
            if child_indent <= parent_indent:
                break
            i = self._parse_numpad_mapping_at(i, target)
        return i

    def _parse_numpad_mapping_at(self, i, target):
        start = len(target)
        end = self._parse_numpad_mapping_at_inner(i, target)
        header = self._strip_comment(self.lines[i]).strip()
        trigger = (header.removesuffix(":") if header.endswith(":") else
                   re.split(r"\s+(?:sends|opens|runs|triggers|enables|disables|holds|set|set_variable|wait|delay|scrolls|moves|track|tracks|(?:left-|right-)?clicks)\s+", header, maxsplit=1)[0])
        kind = ("comma" if len(self._parse_comma_list(trigger)) > 1 else
                "key_block" if header.endswith(":") else "inline")
        for index in range(start, len(target)):
            target[index] = self._mark(target[index], i + 1, kind, f"g{self._numpad_group_line}")
        return end

    def _parse_numpad_mapping_at_inner(self, i, target):
        raw = self.lines[i]
        line = self._strip_comment(raw).strip()
        indent = self._get_indent(raw)

        clean_line, passthrough = self._split_passthrough(line)
        inline = re.fullmatch(r'(?:basic\s+)?(.+?)\s+((?:sends|opens|runs|triggers|enables|disables|holds|set|set_variable|wait|delay|scrolls|moves|track|tracks|(?:left-|right-)?clicks)\s+.+)', clean_line)
        if inline:
            action = self._mark_action(self._parse_advanced_action_line(inline.group(2)), i + 1)
            if action:
                if action["kind"] == "event" and inline.group(1) in (MULTI_KEY_EXPANSION | ARROW_EXPANSION):
                    for mapping in self._parse_mapping_line(line, []):
                        target.append({"type": "event", "from": mapping["from"],
                                       "verb": mapping["verb"], "target": mapping["to"],
                                       "passthrough": mapping.get("passthrough", [])})
                    return i + 1
                for key in self._parse_comma_list(inline.group(1)):
                    target.append({"type": "sequence", "from": key.removeprefix("any+"),
                                   "passthrough": ["any"] if key.startswith("any+") else list(passthrough),
                                   "actions": [copy.deepcopy(action)]})
                return i + 1

        match = re.match(r"^(?:basic\s+)?(.+):$", line)
        if match:
            record = {
                "type": "sequence",
                "from": match.group(1).strip(),
                "actions": [],
            }
            i += 1
            while i < len(self.lines):
                child_raw = self.lines[i]
                child_line = self._strip_comment(child_raw).strip()
                child_indent = self._get_indent(child_raw)
                if not child_line:
                    i += 1
                    continue
                if child_indent <= indent:
                    break
                if child_line.startswith("optional "):
                    record["optional_explicit"] = True
                    record["passthrough"] = [] if child_line == "optional none" else [resolve_modifier(value) for value in child_line.split()[1:]]
                    i += 1
                    continue
                action = self._mark_action(self._parse_advanced_action_line(child_line), i + 1)
                if action and action["field"] == "to":
                    record["actions"].append(action)
                elif not action:
                    self.warnings.append(
                        f"line {i + 1}: unrecognized numpad action: {child_line}")
                i += 1
            for key in self._parse_comma_list(record["from"]):
                expanded = copy.deepcopy(record)
                expanded["from"] = key.removeprefix("any+")
                expanded["passthrough"] = ["any"] if key.startswith("any+") else record.get("passthrough", [])
                target.append(expanded)
            return i

        mappings = self._parse_mapping_line(line, [])
        if not mappings:
            self.warnings.append(
                f"line {i + 1}: unrecognized numpad mapping: {line}")
        for mapping in mappings:
            target.append({
                "type": "event",
                "from": mapping["from"],
                "verb": mapping["verb"],
                "target": mapping["to"],
                "passthrough": mapping.get("passthrough", []),
            })
        return i + 1

    def _parse_numpad_layer_section(self, i, section_indent, layer):
        self._numpad_group_line = i + 1
        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)

            if not line:
                i += 1
                continue
            if child_indent <= section_indent:
                break

            if line == "default:":
                i = self._parse_numpad_mapping_lines(
                    i, child_indent, layer["default"])
            elif line.startswith("when app "):
                app_text = line[len("when app "):].rstrip(":")
                app_values = self._parse_comma_list(app_text)
                app_section = {
                    "apps": app_values,
                    "mappings": [],
                }
                i = self._parse_numpad_mapping_lines(
                    i, child_indent, app_section["mappings"])
                layer["apps"].append(app_section)
            else:
                entry = self._parse_numpad_layer_entry(line)
                if entry:
                    layer["entries"].append(self._mark(entry, i + 1, "numpad_entry", f"g{self._numpad_group_line}"))
                    i += 1
                else:
                    i = self._parse_numpad_mapping_at(i, layer["default"])
        return i

    def _parse_numpad_layer_entry(self, line):
        match = re.match(r"^(\S+)\s+enters\s+t9$", line)
        if match:
            return {
                "key": match.group(1),
                "mode": "t9",
            }

        match = re.match(
            r"^(\S+)\s+enters\s+neru(?:\s+(left_click|right_click|middle_click))?$",
            line)
        if match:
            return {
                "key": match.group(1),
                "mode": "neru",
                "action": match.group(2) or "left_click",
            }
        return None

    def _parse_numpad_mode_section(self, i, section_indent, actions):
        group_id = f"g{i + 1}"
        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)

            if not line:
                i += 1
                continue
            if child_indent <= section_indent:
                break
            mode_actions = self._parse_numpad_mode_action(line)
            if not mode_actions:
                self.warnings.append(
                    f"line {i + 1}: unrecognized numpad mode action: {line}")
            actions.extend(self._mark(action, i + 1, "numpad_mode", group_id) for action in mode_actions)
            i += 1
        return i

    def _parse_numpad_mode_action(self, line):
        match = re.fullmatch(r"(.+?)\s+((?:exits|clears|commits|backtracks|backspaces|resets|selects|clicks|types)\b.*)", line)
        if match and "," in match.group(1):
            return [action for key in self._parse_comma_list(match.group(1))
                    for action in self._parse_numpad_mode_action(f"{key} {match.group(2)}")]
        match = re.match(r"^(\S+)\s+exits$", line)
        if match:
            return [{"key": match.group(1), "action": "exit"}]

        match = re.match(r"^(\S+)\s+clears$", line)
        if match:
            return [{"key": match.group(1), "action": "clear"}]

        match = re.match(r"^(\S+)\s+commits(?:\s+return)?$", line)
        if match:
            return [{
                "key": match.group(1),
                "action": "commit_return" if " return" in line else "commit",
            }]

        match = re.match(r"^(\S+)\s+(?:backtracks|backspaces)$", line)
        if match:
            return [{"key": match.group(1), "action": "backtrack"}]

        match = re.match(r"^(\S+)\s+resets$", line)
        if match:
            return [{"key": match.group(1), "action": "reset"}]

        match = re.match(r"^(\S+)\s+selects\s+center$", line)
        if match:
            return [{"key": match.group(1), "action": "select_center"}]

        match = re.match(r"^(\S+)\s+clicks\s+(left|right|middle)(?:\s+then\s+exits)?$",
                         line)
        if match:
            return [{
                "key": match.group(1),
                "action": "click",
                "button": match.group(2),
            }]

        match = re.match(r"^(\S+)\s+types\s+t9\s+(\S+)$", line)
        if match:
            return [{
                "key": match.group(1),
                "action": "t9_token",
                "token": match.group(2),
            }]

        match = re.match(r"^keys\s+(.+)\s+type\s+t9$", line)
        if match:
            return [{
                "key": key,
                "action": "t9_token",
                "token": key.removeprefix("keypad_"),
            } for key in expand_key_specs(match.group(1))]

        match = re.match(r"^keys\s+(.+)\s+pass\s+through$", line)
        if match:
            return [{
                "key": key,
                "action": "pass_through",
            } for key in expand_key_specs(match.group(1))]

        return []

    def _parse_hyper_combo(self, line, conditions):
        """Parse '$ + arrows sends ctrl+option+cmd+arrows'."""
        m = re.match(r'\$\s+\+\s+(\S+)\s+(sends|opens|runs)\s+(.+)', line)
        if m:
            self.standalone.append({
                "type": "combo",
                "trigger": "$",
                "keys": m.group(1),
                "verb": m.group(2),
                "target": m.group(3).strip(),
                "passthrough": [],
                "conditions": list(conditions),
            })

    def _parse_when(self, i, parent_indent, parent_conditions):
        raw = self.lines[i]
        line = self._strip_comment(raw).strip().removesuffix(":").strip()
        conditions = list(parent_conditions) + self._parse_conditions(line[5:])

        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            child_line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)

            if not child_line:
                i += 1
                continue

            if child_indent <= parent_indent:
                break

            if child_line.startswith("when "):
                i = self._parse_when(i, child_indent, conditions)
            elif child_line.endswith(":") and child_line != "$:":
                i = self._parse_advanced_basic(i, child_indent, conditions)
            elif child_line == "$:":
                i = self._parse_block(i, child_indent, self.hold_mappings,
                                      conditions)
            elif re.match(r'^\$:[a-zA-Z0-9_]+$', child_line):
                sub_key = child_line[2:]
                if sub_key not in self.sublayers:
                    self.sublayers[sub_key] = []
                i = self._parse_block(i, child_indent, self.sublayers[sub_key],
                                      conditions)
            else:
                starts = tuple(map(len, (self.standalone, self.tap_hold, self.advanced_basic)))
                self._parse_standalone(child_line, conditions,
                                       line_number=i + 1)
                self._mark_standalone(i + 1, starts)
                i += 1

        return i

    def _parse_conditions(self, text):
        # Separators inside quoted app names remain part of the name.
        parts = re.split(r'''\s+(and|unless|except)\s+(?=(?:[^"']|"[^"]*"|'[^']*')*$)''', text)
        result = []
        operator = "and"
        for index in range(0, len(parts), 2):
            if index:
                operator = parts[index - 1]
            value = parts[index].strip()
            if operator == "except":
                result.append({"type": "device_except", "values": self._parse_comma_list(value.removeprefix("device "))})
                continue
            values = self._parse_comma_list(value) if operator == "unless" else [value]
            for condition in values:
                match = re.fullmatch(r"(app|device|device_except)\s+(.+)", condition)
                if match:
                    if operator == "unless":
                        if match.group(1) != "device":
                            raise ValueError(f"Unsupported negative condition: {condition}")
                        kind = "device_except"
                    else:
                        kind = match.group(1)
                    result.append({"type": kind, "values": self._parse_comma_list(match.group(2))})
                    continue
                match = re.fullmatch(r"(?:(not|no)\s+|(!))?(@[A-Za-z0-9_]+)", condition)
                if match:
                    truth = not (match.group(1) or match.group(2))
                    result.append({"type": "variable", "name": parse_variable_name(match.group(3)),
                                   "value": int(not truth if operator == "unless" else truth)})
                    continue
                match = re.fullmatch(r"(?:var(?:iable)?\s+)?(@?[A-Za-z0-9_]+)(?:\s+is)?\s+(-?\d+)", condition)
                if match and operator != "unless":
                    result.append({"type": "variable", "name": parse_variable_name(match.group(1)), "value": int(match.group(2))})
                    continue
                raise ValueError(f"Unrecognized condition: {condition}")
        return result

    def _parse_advanced_basic(self, i, block_indent, conditions):
        raw = self.lines[i]
        header = self._strip_comment(raw).strip()
        m = re.match(r'^(?:basic\s+)?(.+):$', header)
        if not m:
            return i + 1

        block = {
            "type": "advanced_basic",
            "from": m.group(1).strip(),
            "mandatory": [],
            "optional": [],
            "actions": {},
            "parameters": {},
            "conditions": list(conditions),
            "source_line": i + 1,
        }

        i += 1
        while i < len(self.lines):
            raw = self.lines[i]
            line = self._strip_comment(raw).strip()
            child_indent = self._get_indent(raw)

            if not line:
                i += 1
                continue

            if child_indent <= block_indent:
                break

            if line.startswith("mandatory "):
                block["mandatory"].extend(line.split()[1:])
                i += 1
                continue

            if line.startswith("optional "):
                block["optional_explicit"] = True
                if line == "optional none":
                    block["optional"] = []
                else:
                    block["optional"].extend(line.split()[1:])
                i += 1
                continue

            parameter = self._parse_advanced_parameter_line(line)
            if parameter:
                block["parameters"][parameter["name"]] = parameter["value"]
                i += 1
                continue

            action = self._mark_action(self._parse_advanced_action_line(line), i + 1)
            if action:
                block["actions"].setdefault(action["field"], []).append(action)
            else:
                self.warnings.append(
                    f"line {i + 1}: unrecognized basic action: {line}")
            i += 1

        self._append_basic(block)
        return i

    def _append_basic(self, block):
        if not origin_of(block).unit_id and block.get("source_line"):
            line = block["source_line"]
            kind = ("simultaneous" if block["from"].endswith(" together") else
                    "comma" if len(self._parse_comma_list(block["from"])) > 1 else
                    "key_block" if self._strip_comment(self.lines[line - 1]).strip().endswith(":") else "inline")
            block = self._mark(block, line, kind)
        for key in self._parse_comma_list(block["from"]):
            expanded = copy.deepcopy(block)
            expanded["from"] = key.removeprefix("any+")
            if key.startswith("any+") and "any" not in expanded["optional"]:
                expanded["optional"].append("any")
            self.advanced_basic.append(expanded)

    def _parse_advanced_action_line(self, line):
        action_aliases = (
            ("to_delayed_action.to_if_canceled", "if canceled "),
            ("to_delayed_action.to_if_invoked", "if invoked "),
            ("to_if_alone", "alone "),
            ("to_if_held_down", "held "),
            ("to_after_key_up", "after up "),
            ("to", "to "),
            ("to", ""),
        )
        for field, prefix in action_aliases:
            if prefix and not line.startswith(prefix):
                continue
            rest = line[len(prefix):].strip() if prefix else line.strip()
            if not prefix and not re.match(
                    r'^(sends|opens|runs|triggers|enables|disables|holds|set|set_variable|wait|delay|scrolls|moves|track|tracks|(?:left-|right-)?clicks)\b',
                    rest):
                continue

            trigger = re.fullmatch(r'triggers\s+([A-Za-z_][A-Za-z0-9_-]*)', rest)
            if trigger:
                return {"field": field, "kind": "action_ref", "name": trigger.group(1)}

            boolean = re.fullmatch(r'(enables|disables|holds)\s+(@[A-Za-z0-9_]+)', rest)
            if boolean:
                return {"field": field, "kind": "set_variable",
                        "name": parse_variable_name(boolean.group(2)),
                        "value": 0 if boolean.group(1) == "disables" else 1,
                        "key_up_value": 0 if boolean.group(1) == "holds" else None}

            setvar_match = re.match(
                r'^(?:set|set_variable)\s+(@?[A-Za-z0-9_]+)\s+(-?\d+)(?:\s+(?:until\s+key\s+up|key_up_value)\s+(-?\d+))?$',
                rest)
            if setvar_match:
                return {
                    "field":
                    field,
                    "kind":
                    "set_variable",
                    "name":
                    parse_variable_name(setvar_match.group(1)),
                    "value":
                    int(setvar_match.group(2)),
                    "key_up_value":
                        (int(setvar_match.group(3))
                         if setvar_match.group(3) is not None else None),
                }

            wait_match = re.match(r'^(?:wait|delay)\s+(\d+)ms$', rest)
            if wait_match:
                return {
                    "field": field,
                    "kind": "wait",
                    "milliseconds": int(wait_match.group(1)),
                }

            scroll_match = re.match(r'^scrolls\s+(up|down|left|right)(?:\s+(\d+))?$',
                                    rest)
            if scroll_match:
                magnitude = int(scroll_match.group(2) or 32)
                direction = scroll_match.group(1)
                return {
                    "field": field,
                    "kind": "mouse_wheel",
                    "axis": "vertical_wheel" if direction in ("up", "down") else "horizontal_wheel",
                    "amount": -magnitude if direction in ("up", "left") else magnitude,
                }

            move_match = re.match(r'^moves\s+(up|down|left|right)(?:\s+(\d+))?$',
                                  rest)
            if move_match:
                speed = int(move_match.group(2) or 1536)
                direction = move_match.group(1)
                return {
                    "field": field,
                    "kind": "mouse_move",
                    "axis": "y" if direction in ("up", "down") else "x",
                    "amount": (-speed if direction in ("up", "left")
                               else speed),
                }

            pointer_click_match = re.match(r'^clicks\s+(left|right|middle)$', rest)
            if pointer_click_match:
                return {
                    "field": field,
                    "kind": "pointer_click",
                    "button": {"left": "button1", "right": "button2", "middle": "button3"}[pointer_click_match.group(1)],
                }

            ax_track_match = re.match(r'^(?:track|tracks)\s+(?:pointer|current)$',
                                      rest)
            if ax_track_match:
                return {
                    "field": field,
                    "kind": "numpad_ax",
                    "action": "sync",
                    "delay_ms": 90,
                }

            ax_click_match = re.match(
                r'^(?:(left|right)-)?clicks\s+current$', rest)
            if ax_click_match:
                return {
                    "field": field,
                    "kind": "numpad_ax",
                    "action": "click",
                    "button": ax_click_match.group(1) or "left",
                }

            event_match = re.match(
                r'^(sends|opens|runs)\s+(.+?)(?:\s+lazy)?(?:\s+halt)?$', rest)
            if event_match:
                return {
                    "field": field,
                    "kind": "event",
                    "verb": event_match.group(1),
                    "target": event_match.group(2).strip(),
                    "lazy": bool(re.search(r'\s+lazy(?:\s+halt)?$', rest)),
                    "halt": bool(re.search(r'\s+halt$', rest)),
                }
        return None

    def _parse_advanced_parameter_line(self, line):
        if line.startswith("parameter "):
            _, name, value = line.split(maxsplit=2)
            return {"name": name, "value": int(value)}

        aliases = (
            (r'^alone timeout (\d+)ms$',
             "basic.to_if_alone_timeout_milliseconds"),
            (r'^held threshold (\d+)ms$',
             "basic.to_if_held_down_threshold_milliseconds"),
            (r'^delay (\d+)ms$', "basic.to_delayed_action_delay_milliseconds"),
        )
        for pattern, name in aliases:
            match = re.match(pattern, line)
            if match:
                return {"name": name, "value": int(match.group(1))}
        return None

    def _parse_comma_list(self, text):
        result = []
        current = ""
        in_quote = False
        quote_char = None

        for ch in text:
            if ch in ('"', "'") and not in_quote:
                in_quote = True
                quote_char = ch
            elif ch == quote_char and in_quote:
                in_quote = False
            elif ch == ',' and not in_quote:
                val = current.strip().strip('"').strip("'")
                if val:
                    result.append(val)
                current = ""
                continue
            current += ch

        val = current.strip().strip('"').strip("'")
        if val:
            result.append(val)
        return result

    def _parse_standalone(self, line, conditions=None, line_number=None):
        if conditions is None:
            conditions = []
        location = f"line {line_number}: " if line_number else ""

        swap = re.fullmatch(r"swaps?\s+(\S+)\s+(\S+)", line)
        if swap:
            if not any(condition["type"] == "device" for condition in conditions):
                raise ValueError("Scoped swaps require a when device condition")
            first, second = (resolve_side_modifier_keys(key) for key in swap.groups())
            if len(first) != len(second):
                raise ValueError("Cannot swap modifiers with different side counts")
            for left, right in zip(first, second):
                for source, target in ((left, right), (right, left)):
                    self._append_basic({"type": "advanced_basic", "from": source,
                                        "mandatory": [], "optional": ["any"], "parameters": {},
                                        "conditions": list(conditions), "source_line": line_number,
                                        "actions": {"to": [{"field": "to", "kind": "event", "verb": "sends", "target": target}]}})
            return

        line, passthrough = self._split_passthrough(line)

        # tap-hold
        m = re.match(
            r'(\S+)\s+(tap|hold)\s+(sends|opens|runs)\s+(.+?)(?:\s+lazy)?$',
            line)
        if m:
            target = m.group(4).strip()
            lazy = bool(re.search(r'\s+lazy$', line))
            self.tap_hold.append({
                "from": m.group(1),
                "when": m.group(2),
                "verb": m.group(3),
                "target": target,
                "lazy": lazy,
                "conditions": list(conditions),
                "passthrough": passthrough,
            })
            return

        # together
        m = re.match(r'(\S+)\s+together\s+(sends|opens|runs)\s+(.+)', line)
        if m:
            self.standalone.append({
                "type": "together",
                "keys": m.group(1),
                "verb": m.group(2),
                "target": m.group(3).strip(),
                "passthrough": passthrough,
                "conditions": list(conditions),
            })
            return

        # combo with spaces around + (not modifier combo)
        m = re.match(r'(\S+)\s+\+\s+(\S+)\s+(sends|opens|runs)\s+(.+)', line)
        if m:
            self.standalone.append({
                "type": "combo",
                "trigger": m.group(1),
                "keys": m.group(2),
                "verb": m.group(3),
                "target": m.group(4).strip(),
                "passthrough": passthrough,
                "conditions": list(conditions),
            })
            return

        # The single-line and block forms share the same action parser.
        match = re.fullmatch(r'(?:basic\s+)?(.+?)\s+((?:sends|opens|runs|triggers|enables|disables|holds|set|set_variable|wait|delay|scrolls|moves|track|tracks|(?:left-|right-)?clicks)\s+.+)', line)
        if match:
            action = self._mark_action(self._parse_advanced_action_line(match.group(2)), line_number)
            if action:
                # Preserve the legacy single-line fallback tier regardless of
                # scope. Grouped chords and any+ are new implicit basic forms.
                if (action["kind"] == "event" and not action.get("lazy")
                        and not action.get("halt") and not line.startswith("basic ")
                        and len(self._parse_comma_list(match.group(1))) == 1
                        and not any(key.startswith("any+") for key in self._parse_comma_list(match.group(1)))):
                    for key in self._parse_comma_list(match.group(1)):
                        self.standalone.append({"type": "simple", "from": key,
                                                "verb": action["verb"], "target": action["target"],
                                                "passthrough": list(passthrough), "conditions": list(conditions),
                                                "source_line": line_number})
                    return
                self._append_basic({"type": "advanced_basic", "from": match.group(1),
                                    "mandatory": [], "optional": passthrough,
                                    "actions": {action["field"]: [action]}, "parameters": {},
                                    "conditions": list(conditions), "source_line": line_number})
                return

        self.warnings.append(f"{location}unrecognized line: {line}")
