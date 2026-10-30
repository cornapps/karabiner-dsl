"""Compiler: intermediate blocks into Karabiner-Elements JSON."""

import copy
import json
import re
import shlex

from .origins import (
    ActionOrigin,
    ManipulatorOrigin,
    MacroCall,
    Origin,
    carry_origin,
    origin_of,
)

from .keys import (
    ARROW_EXPANSION,
    HINT_DELAY_MILLISECONDS,
    HINT_HELPER_ASSETS,
    HINT_HELPER_BINARY,
    HINT_NOTIFICATION_ID,
    MULTI_KEY_EXPANSION,
    NUMPAD_AX_SCRIPT,
    NUMPAD_FALLBACK_PYTHON,
    NUMPAD_HUD_SCRIPT,
    NUMPAD_NERU_SCRIPT,
    NUMPAD_NERU_VARIABLE,
    NUMPAD_PENDING_NERU_VARIABLE,
    NUMPAD_PENDING_T9_VARIABLE,
    NUMPAD_PREFERRED_PYTHON,
    NUMPAD_T9_VARIABLE,
    helpers_root,
    app_name_to_bundles,
    dock_slot_shell_command,
    expand_modifier_sides,
    format_modifier_suffix,
    is_modifier_mapping,
    parse_device_ids,
    parse_key_combo,
    resolve_key,
    resolve_modifier,
    resolve_side_modifier_keys,
)
from .decompile import (
    decompile_mapping_manipulators,
    indent_lines,
    wrap_with_conditions,
)


class Compiler:

    def __init__(self, parser):
        self.p = parser
        self.rules = []
        self._explicit_optional = set()
        self.origins = []
        self._emissions = {}
        self._action_origins = {}
        for name in self.p.actions:
            self._expand_actions(self.p.actions[name], (name,))

    def _expand_actions(self, actions, stack=()):
        expanded = []
        for action in actions:
            if action["kind"] != "action_ref":
                expanded.append(copy.deepcopy(action))
                continue
            name = action["name"]
            if name not in self.p.actions:
                raise ValueError(f"Unknown action: {name}")
            if name in stack:
                raise ValueError(f"Recursive action: {' -> '.join(stack + (name,))}")
            for step in self._expand_actions(self.p.actions[name], stack + (name,)):
                step["field"] = action["field"]
                call, definition = origin_of(action), origin_of(step)
                related = tuple(location for location in
                                (*call.related, definition.source, *definition.related)
                                if location is not None)
                step = carry_origin(step, action)
                step.origin = Origin(call.source, call.unit_id, related,
                                     (MacroCall(name, call.source, self.p.action_definitions.get(name)),)
                                     + definition.macro_calls)
                expanded.append(step)
        return expanded

    def compile(self):
        self._compile_hyper_activation()
        self._compile_tap_hold()
        self._compile_advanced_basic()
        self._compile_sublayer_activations()
        self._compile_hold_mappings()
        self._compile_sublayer_mappings()
        self._compile_numpads()
        self._compile_standalone()
        for rule in self.rules:
            for manipulator in rule["manipulators"]:
                if is_modifier_mapping(manipulator["from"], manipulator.get("to", [])):
                    self._set_default_optional(manipulator["from"])

        profile = {
            "complex_modifications": {
                "rules": self.rules,
            },
            "name": "Default profile",
            "selected": True,
            "virtual_hid_keyboard": {
                "keyboard_type_v2": "ansi"
            },
        }
        devices = self._compile_devices()
        if devices:
            profile["devices"] = devices

        self._finish_origins()
        return {
            "profiles": [profile]
        }

    def _record(self, manipulator, source, generator, contributing_units=()):
        origin = origin_of(source)
        unit = self.p._units_by_id.get(origin.unit_id)
        if unit:
            related = tuple(location for location in unit.declarations
                            if location != origin.source)
            origin = Origin(origin.source, origin.unit_id, origin.related + related, origin.macro_calls)
        self._emissions[id(manipulator)] = (manipulator, origin, generator,
                                           tuple(contributing_units))
        return manipulator

    def _record_rule(self, rule, source, generator, contributing_units=()):
        for manipulator in rule["manipulators"]:
            self._record(manipulator, source, generator, contributing_units)

    def _finish_origins(self):
        self.origins = []
        unit_counts = {}
        for rule_index, rule in enumerate(self.rules):
            for manipulator_index, manipulator in enumerate(rule["manipulators"]):
                _, origin, generator, contributors = self._emissions.get(
                    id(manipulator), (manipulator, Origin(None), "unknown", ()))
                actions = []

                def visit(value, path):
                    if isinstance(value, list):
                        for index, event in enumerate(value):
                            if isinstance(event, dict):
                                entry = self._action_origins.get(id(event))
                                event_origin = entry[1] if entry and entry[1].source else origin
                                # An expanded action belongs to its call's unit.
                                if event_origin.unit_id is None:
                                    event_origin = Origin(event_origin.source, origin.unit_id,
                                                          event_origin.related, event_origin.macro_calls)
                                actions.append(ActionOrigin(path + (index,), event_origin,
                                                            "action" if entry else generator))
                    elif isinstance(value, dict):
                        for key, child in value.items():
                            visit(child, path + (key,))

                for key, value in manipulator.items():
                    if key == "to" or key.startswith("to_"):
                        visit(value, (key,))
                internal_order = unit_counts.get(origin.unit_id, 0)
                unit_counts[origin.unit_id] = internal_order + 1
                self.origins.append(ManipulatorOrigin(
                    len(self.origins), rule_index, manipulator_index, origin,
                    generator, internal_order, tuple(actions), contributors))

    def _set_default_optional(self, from_event):
        if id(from_event) in self._explicit_optional:
            return
        modifiers = from_event.setdefault("modifiers", {})
        if "optional" not in modifiers:
            modifiers["optional"] = ["any"]

    def _compile_devices(self):
        devices_by_id = {}
        for modification in self.p.device_modifications:
            if "device_name" in modification:
                device = self.p.device_definitions[modification["device_name"]]
                vendor_id, product_id = parse_device_ids(device["id"])
                kind = modification["device_kind"] or device.get("kind", "keyboard")
                if kind not in ("keyboard", "pointing", "keyboard+pointing"):
                    raise ValueError(f"Unknown device kind: {kind}")
                modification = {**modification, "vendor_id": vendor_id, "product_id": product_id,
                                "is_keyboard": "keyboard" in kind.split("+"),
                                "is_pointing_device": "pointing" in kind.split("+")}
            device_key = (modification["vendor_id"], modification["product_id"],
                          bool(modification.get("is_pointing_device")), modification.get("is_keyboard", True))
            device = devices_by_id.setdefault(
                device_key, {
                    "identifiers": {
                        "is_keyboard": modification.get("is_keyboard", True),
                        "product_id": modification["product_id"],
                        "vendor_id": modification["vendor_id"],
                    },
                    "simple_modifications": [],
                })
            if modification.get("is_pointing_device"):
                device["identifiers"]["is_pointing_device"] = True

            if modification["type"] == "enabled":
                device["ignore"] = False

            if modification["type"] == "ignored":
                device["ignore"] = True

            if modification["type"] == "swap":
                device["simple_modifications"].extend(
                    self._compile_device_swap(modification["first"],
                                              modification["second"]))

        return list(devices_by_id.values())

    def _compile_device_swap(self, first, second):
        first_keys = resolve_side_modifier_keys(first)
        second_keys = resolve_side_modifier_keys(second)
        if len(first_keys) != len(second_keys):
            raise ValueError(
                f"Cannot swap modifiers with different side counts: {first}, {second}"
            )

        simple_modifications = []
        for first_key, second_key in zip(first_keys, second_keys):
            simple_modifications.append(
                self._simple_modification(first_key, second_key))
            simple_modifications.append(
                self._simple_modification(second_key, first_key))
        return simple_modifications

    def _simple_modification(self, from_key, to_key):
        return {
            "from": {
                "key_code": from_key,
            },
            "to": [{
                "key_code": to_key,
            }],
        }

    def _from_events(self,
                     key_code,
                     mandatory=None,
                     optional=None,
                     is_consumer=False):
        events = []
        for mandatory_variant in expand_modifier_sides(mandatory or []):
            event = {
                "consumer_key_code" if is_consumer else "key_code": key_code
            }
            modifiers = {}
            if mandatory_variant:
                modifiers["mandatory"] = mandatory_variant
            if optional:
                modifiers["optional"] = optional
            if modifiers:
                event["modifiers"] = modifiers
            events.append(event)
        return events

    def _hyper_var(self):
        return f"{self.p.hyper_key}_held" if self.p.hyper_key else "hyper_held"

    def _sublayer_var(self, key):
        return f"sublayer_{key}_active"

    def _hint_notification(self, text):
        return {
            "set_notification_message": {
                "id": HINT_NOTIFICATION_ID,
                "text": text,
            }
        }

    def _hint_shell_command(self, action, header=None, fallback_text=None):
        root = helpers_root("`hints on`")
        command = [
            str(root / HINT_HELPER_BINARY),
            action,
            "--assets-dir",
            str(root / HINT_HELPER_ASSETS),
        ]

        if action == "show":
            if self.p.source_path and header:
                command.extend([
                    "--kb",
                    str(self.p.source_path),
                    "--layer",
                    header,
                ])
            elif header and fallback_text is not None:
                command.extend([
                    "--title",
                    self._hint_title(header),
                    "--text",
                    fallback_text,
                ])

        return " ".join(shlex.quote(part) for part in command)

    def _hint_shell_event(self, action, header=None, fallback_text=None):
        return {
            "shell_command":
            self._hint_shell_command(action,
                                     header=header,
                                     fallback_text=fallback_text)
        }

    def _mapping_to_hint_manipulator(self, mapping):
        from_event = {"key_code": resolve_key(mapping["from"])}
        if mapping.get("passthrough"):
            from_event["modifiers"] = {"optional": mapping["passthrough"]}
        return {
            "from": from_event,
            "to": [self._make_to_event(mapping["verb"], mapping["to"])],
        }

    def _format_hint_mapping_lines(self, manipulator_lines):
        parsed = []
        widths = {"from": 0, "verb": 0}

        for line in manipulator_lines:
            match = re.match(r"^(\S+)\s+(sends|opens|runs)\s+(.+)$", line)
            if not match:
                parsed.append((None, line))
                continue

            from_part, verb, target = match.groups()
            widths["from"] = max(widths["from"], len(from_part))
            widths["verb"] = max(widths["verb"], len(verb))
            parsed.append((from_part, verb, target))

        formatted = []
        for item in parsed:
            if item[0] is None:
                formatted.append(item[1])
                continue

            from_part, verb, target = item
            formatted.append(
                f"{from_part:<{widths['from']}}  {verb:<{widths['verb']}}  {target}"
            )
        return formatted

    def _hint_title(self, header):
        if header == "$:":
            return "Layer $:"
        return f"Layer {header}"

    def _render_hint_block(self, header, mappings):
        groups = self._group_by_conditions(mappings)
        blocks = []
        for cond_key, group_mappings in groups.items():
            manipulators = [
                self._mapping_to_hint_manipulator(mapping)
                for mapping in group_mappings
            ]
            mapping_lines = self._format_hint_mapping_lines(
                decompile_mapping_manipulators(manipulators))
            title = self._hint_title(header)
            divider = "=" * len(title)
            block_lines = [divider, title, divider, header
                           ] + indent_lines(mapping_lines)
            conditions = [] if cond_key == "[]" else group_mappings[0].get(
                "conditions", [])
            blocks.append(wrap_with_conditions(conditions, block_lines))
        return blocks

    def _layer_hint_text_for(self, header, mappings):
        blocks = self._render_hint_block(header, mappings)
        if not blocks:
            return ""

        lines = []
        for index, block in enumerate(blocks):
            if index:
                lines.append("")
            lines.extend(block)
        return "\n".join(lines)

    def _conditions_to_karabiner(self, conditions):
        result = []
        for c in conditions:
            if c["type"] == "app":
                bundles = []
                for value in c["values"]:
                    bundles.extend(app_name_to_bundles(value))
                result.append({
                    "type": "frontmost_application_if",
                    "bundle_identifiers": bundles,
                })
            elif c["type"] in ("device", "device_except"):
                identifiers = []
                for value in c["values"]:
                    value = self.p.device_aliases.get(value, value)
                    if value == "external_keyboard":
                        identifiers.append({
                            "is_keyboard": True,
                            "is_built_in_keyboard": False,
                        })
                    elif re.fullmatch(r"\d+:\d+", value):
                        vendor_id, product_id = parse_device_ids(value)
                        identifiers.append({
                            "vendor_id": vendor_id,
                            "product_id": product_id,
                        })
                    else:
                        identifiers.append({"product_name": value})
                result.append({
                    "type": "device_unless" if c["type"] == "device_except" else "device_if",
                    "identifiers": identifiers,
                })
            elif c["type"] == "variable":
                result.append({
                    "type": "variable_if",
                    "name": c["name"],
                    "value": c["value"],
                })
        return result

    def _compile_hyper_activation(self):
        if not self.p.hyper_key:
            return

        hyper_var = self._hyper_var()
        sublayer_vars = [self._sublayer_var(k) for k in self.p.sublayers]

        to_after_key_up = [{"set_variable": {"name": hyper_var, "value": 0}}]
        for sv in sublayer_vars:
            to_after_key_up.append({"set_variable": {"name": sv, "value": 0}})

        hint_text = ""
        if self.p.hints_enabled and self.p.hold_mappings:
            hint_text = self._layer_hint_text_for("$:", self.p.hold_mappings)
        if hint_text:
            to_after_key_up.append(self._hint_shell_event("hide"))

        manipulator = {
            "type": "basic",
            "from": {
                "key_code": resolve_key(self.p.hyper_key),
                "modifiers": {
                    "optional": ["any"]
                },
            },
            "to": [{
                "set_variable": {
                    "name": hyper_var,
                    "value": 1
                }
            }],
            "to_after_key_up": to_after_key_up,
            "parameters": {
                "basic.to_if_alone_timeout_milliseconds": 200,
            },
        }

        if hint_text:
            manipulator["to_delayed_action"] = {
                "to_if_invoked": [
                    self._hint_shell_event("show",
                                           header="$:",
                                           fallback_text=hint_text)
                ],
                "to_if_canceled": [self._hint_shell_event("hide")],
            }
            manipulator["parameters"][
                "basic.to_delayed_action_delay_milliseconds"] = HINT_DELAY_MILLISECONDS

        if self.p.hyper_tap:
            verb, target = self.p.hyper_tap
            event = self._make_to_event(verb, target)
            self._action_origins[id(event)] = (event, Origin(self.p.hyper_tap_origin))
            manipulator["to_if_alone"] = [event]

        desc = f"{self.p.hyper_key}"
        if self.p.hyper_tap:
            desc += f" | tap={self.p.hyper_tap[1]}"
        desc += " | hold=hyper"
        if hint_text:
            desc += " | hints"

        self.rules.append({
            "description": desc,
            "manipulators": [manipulator],
        })
        self._record_rule(self.rules[-1], self.p.hyper_binding, "hyper_activation")

    def _compile_tap_hold(self):
        if not self.p.tap_hold:
            return

        grouped = {}
        for mapping in self.p.tap_hold:
            key = json.dumps(
                {
                    "from": mapping["from"],
                    "conditions": mapping.get("conditions", []),
                    "passthrough": mapping.get("passthrough", []),
                },
                sort_keys=True,
            )
            grouped.setdefault(key, []).append(mapping)

        for mappings in grouped.values():
            tap = next((m for m in mappings if m["when"] == "tap"), None)
            hold = next((m for m in mappings if m["when"] == "hold"), None)
            if not tap or not hold:
                continue

            from_event = {
                "key_code": resolve_key(tap["from"]),
                "modifiers": {
                    "optional": tap.get("passthrough", []) or ["any"]
                },
            }

            hold_event = self._make_to_event(hold["verb"],
                                             hold["target"],
                                             lazy=hold.get("lazy", False))

            manipulator = {
                "type": "basic",
                "from": from_event,
                "to": [hold_event],
                "to_if_alone":
                [self._make_to_event(tap["verb"], tap["target"])],
                "parameters": {
                    "basic.to_if_alone_timeout_milliseconds": 200,
                    "basic.to_if_held_down_threshold_milliseconds": 200,
                },
            }

            self._action_origins[id(hold_event)] = (hold_event, origin_of(hold))
            tap_event = manipulator["to_if_alone"][0]
            self._action_origins[id(tap_event)] = (tap_event, origin_of(tap))
            conditions = self._conditions_to_karabiner(
                tap.get("conditions", []))
            if conditions:
                manipulator["conditions"] = conditions

            description = (
                f"{tap['from']} | tap={tap['target']} | hold={hold['target']}")
            if hold.get("lazy"):
                description += " | lazy"

            self.rules.append({
                "description": description,
                "manipulators": [manipulator],
            })
            self._record_rule(self.rules[-1], mappings[0], "tap_hold")

    def _compile_advanced_basic(self):
        for block in self.p.advanced_basic:
            from_spec = block["from"]
            is_simultaneous = from_spec.endswith(" together")
            if is_simultaneous:
                simultaneous_keys = from_spec[:-len(" together")].split("+")
                mandatory = [
                    resolve_modifier(m) for m in block.get("mandatory", [])
                ]
                optional = [resolve_modifier(m) for m in block.get("optional", [])]
                from_events = [{
                    "simultaneous": [{
                        "key_code": resolve_key(k)
                    } for k in simultaneous_keys],
                    "modifiers": {
                        "optional": optional if block.get("optional_explicit") else optional or ["any"]
                    },
                }]
                if mandatory:
                    from_events[0]["modifiers"]["mandatory"] = mandatory
                if not from_events[0]["modifiers"]["optional"]:
                    del from_events[0]["modifiers"]["optional"]
                if not from_events[0]["modifiers"]:
                    del from_events[0]["modifiers"]
            else:
                from_key, from_mods, from_is_consumer = parse_key_combo(
                    from_spec)
                mandatory = list(from_mods) + [
                    resolve_modifier(m) for m in block.get("mandatory", [])
                ]
                optional = [resolve_modifier(m) for m in block.get("optional", [])]
                from_events = self._from_events(from_key,
                                                mandatory,
                                                optional,
                                                is_consumer=from_is_consumer)

            manipulators = []
            for from_event in from_events:
                if block.get("optional_explicit"):
                    self._explicit_optional.add(id(from_event))
                manipulator = {
                    "type": "basic",
                    "from": from_event,
                }

                for field, actions in block.get("actions", {}).items():
                    events = [self._advanced_action_event(action)
                              for action in self._expand_actions(actions)]

                    if field.startswith("to_delayed_action."):
                        manipulator.setdefault("to_delayed_action", {})
                        manipulator["to_delayed_action"][field.split(
                            ".", 1)[1]] = events
                    else:
                        manipulator[field] = events

                if block.get("parameters"):
                    manipulator["parameters"] = dict(block["parameters"])

                conditions = self._conditions_to_karabiner(
                    block.get("conditions", []))
                if conditions:
                    manipulator["conditions"] = conditions

                manipulators.append(manipulator)
                self._record(manipulator, block, "advanced_basic")

            self.rules.append({
                "description": f"basic {block['from']}",
                "manipulators": manipulators,
            })

    def _compile_sublayer_activations(self):
        if not self.p.sublayers:
            return

        hyper_var = self._hyper_var()
        all_sub_vars = [self._sublayer_var(k) for k in self.p.sublayers]

        for sub_key in self.p.sublayers:
            sub_var = self._sublayer_var(sub_key)

            to_events = [{"set_variable": {"name": sub_var, "value": 1}}]
            for other_var in all_sub_vars:
                if other_var != sub_var:
                    to_events.append(
                        {"set_variable": {
                            "name": other_var,
                            "value": 0
                        }})
            if self.p.hints_enabled:
                hint_text = self._layer_hint_text_for(
                    f"$:{sub_key}", self.p.sublayers[sub_key])
                if hint_text:
                    to_events.append(
                        self._hint_shell_event(
                            "show",
                            header=f"$:{sub_key}",
                            fallback_text=hint_text,
                        ))

            self.rules.append({
                "description":
                f"$:{sub_key} activation",
                "manipulators": [{
                    "type":
                    "basic",
                    "from": {
                        "key_code": resolve_key(sub_key),
                        "modifiers": {
                            "optional": ["any"]
                        },
                    },
                    "to":
                    to_events,
                    "conditions": [
                        {
                            "type": "variable_if",
                            "name": hyper_var,
                            "value": 1
                        },
                    ],
                }],
            })
            sources = self.p.sublayer_units.get(sub_key, [])
            self._record_rule(self.rules[-1], sources[0] if sources else None,
                              "sublayer_activation", [origin_of(s).unit_id for s in sources])

    def _compile_hold_mappings(self):
        if not self.p.hold_mappings:
            return

        hyper_var = self._hyper_var()
        sublayer_vars = [self._sublayer_var(k) for k in self.p.sublayers]

        groups = self._group_by_conditions(self.p.hold_mappings)

        for cond_key, mappings in groups.items():
            manipulators = []
            for m in mappings:
                to_event = self._make_to_event(m["verb"], m["to"])

                from_event = {"key_code": resolve_key(m["from"])}
                if m.get("passthrough"):
                    from_event["modifiers"] = {"optional": m["passthrough"]}

                conditions = [
                    {
                        "type": "variable_if",
                        "name": hyper_var,
                        "value": 1
                    },
                ]
                for sv in sublayer_vars:
                    conditions.append({
                        "type": "variable_if",
                        "name": sv,
                        "value": 0
                    })
                conditions.extend(
                    self._conditions_to_karabiner(m.get("conditions", [])))

                manipulators.append({
                    "type": "basic",
                    "from": from_event,
                    "to": [to_event],
                    "conditions": conditions,
                })
                self._record(manipulators[-1], m, "hold_mapping")

            cond_label = ""
            if cond_key != "[]":
                cond_label = f" (conditional)"
            self.rules.append({
                "description": f"$: hold layer{cond_label}",
                "manipulators": manipulators,
            })

    def _compile_sublayer_mappings(self):
        hyper_var = self._hyper_var()

        for sub_key, mappings in self.p.sublayers.items():
            if not mappings:
                continue

            sub_var = self._sublayer_var(sub_key)
            groups = self._group_by_conditions(mappings)

            for cond_key, group_mappings in groups.items():
                manipulators = []
                for m in group_mappings:
                    to_event = self._make_to_event(m["verb"], m["to"])

                    from_event = {"key_code": resolve_key(m["from"])}
                    if m.get("passthrough"):
                        from_event["modifiers"] = {
                            "optional": m["passthrough"]
                        }

                    conditions = [
                        {
                            "type": "variable_if",
                            "name": hyper_var,
                            "value": 1
                        },
                        {
                            "type": "variable_if",
                            "name": sub_var,
                            "value": 1
                        },
                    ]
                    conditions.extend(
                        self._conditions_to_karabiner(m.get("conditions", [])))

                    manipulators.append({
                        "type": "basic",
                        "from": from_event,
                        "to": [to_event],
                        "conditions": conditions,
                    })
                    self._record(manipulators[-1], m, "sublayer_mapping")

                self.rules.append({
                    "description": f"$:{sub_key} mappings",
                    "manipulators": manipulators,
                })

    def _compile_numpads(self):
        for numpad in self.p.numpads:
            first_rule = len(self.rules)
            definition = self.p.device_definitions.get(numpad["name"], {})
            numpad["device"] = self.p.device_aliases.get(numpad["name"], numpad["device"])
            numpad["key_aliases"].update(definition.get("keys", {}))
            numpad["rows"].update(definition.get("rows", {}))
            self._compile_numpad_modes(numpad)

            for layer in numpad.get("layers", []):
                self._compile_numpad_layer_activation(numpad, layer)
                self._compile_numpad_layer_entries(numpad, layer)
                self._compile_numpad_mapping_section(
                    f"numpad {numpad['name']} {layer['id']} layer",
                    numpad,
                    layer,
                    self._numpad_idle_records(numpad) + [{
                        "type": "variable",
                        "name": layer["variable"],
                        "value": 1,
                    }],
                )

            self._compile_numpad_mapping_section(
                f"numpad {numpad['name']} base",
                numpad,
                numpad["base"],
                self._numpad_idle_records(numpad),
            )
            if numpad.get("ignores_modifiers"):
                for rule in self.rules[first_rule:]:
                    for manipulator in rule["manipulators"]:
                        self._set_default_optional(manipulator["from"])

    def _numpad_python(self):
        if NUMPAD_PREFERRED_PYTHON.exists():
            return NUMPAD_PREFERRED_PYTHON
        return NUMPAD_FALLBACK_PYTHON

    def _numpad_command(self, feature, script, *args):
        parts = [str(self._numpad_python()), str(helpers_root(feature) / script), *args]
        return " ".join(shlex.quote(part) for part in parts)

    def _numpad_hud_event(self, *args):
        return {
            "shell_command": self._numpad_command("A numpad block", NUMPAD_HUD_SCRIPT, *args),
        }

    def _numpad_neru_event(self, *args):
        return {
            "shell_command": self._numpad_command("A numpad block", NUMPAD_NERU_SCRIPT, *args),
        }

    def _numpad_ax_event(self, *args):
        return {
            "shell_command": self._numpad_command(
                "`tracks pointer` or `clicks current`", NUMPAD_AX_SCRIPT, *args),
        }

    def _numpad_set_var_event(self, name, value):
        return {
            "set_variable": {
                "name": name,
                "value": value,
            }
        }

    def _numpad_device_records(self, numpad):
        return [{"type": "device", "values": [numpad["device"]]}]

    def _numpad_idle_records(self, numpad):
        return self._numpad_device_records(numpad) + [
            {
                "type": "variable",
                "name": NUMPAD_T9_VARIABLE,
                "value": 0,
            },
            {
                "type": "variable",
                "name": NUMPAD_NERU_VARIABLE,
                "value": 0,
            },
        ]

    def _compile_numpad_modes(self, numpad):
        t9_actions = numpad.get("modes", {}).get("t9", [])
        if t9_actions:
            manipulators = []
            conditions = self._conditions_to_karabiner(
                self._numpad_device_records(numpad) + [{
                    "type": "variable",
                    "name": NUMPAD_T9_VARIABLE,
                    "value": 1,
                }])
            for action in t9_actions:
                for expanded_action in self._numpad_expand_action(
                        numpad, action):
                    manipulator = self._numpad_mode_manipulator(
                        numpad, expanded_action, conditions,
                        self._numpad_t9_action_events(expanded_action))
                    if manipulator:
                        manipulators.append(manipulator)
            if manipulators:
                self.rules.append({
                    "description": f"numpad {numpad['name']} T9 mode",
                    "manipulators": manipulators,
                })

        neru_actions = numpad.get("modes", {}).get("neru", [])
        if neru_actions:
            manipulators = []
            conditions = self._conditions_to_karabiner(
                self._numpad_device_records(numpad) + [{
                    "type": "variable",
                    "name": NUMPAD_NERU_VARIABLE,
                    "value": 1,
                }])
            for action in neru_actions:
                for expanded_action in self._numpad_expand_action(
                        numpad, action):
                    manipulator = self._numpad_mode_manipulator(
                        numpad, expanded_action, conditions,
                        self._numpad_neru_action_events(expanded_action))
                    if manipulator:
                        manipulators.append(manipulator)
            if manipulators:
                self.rules.append({
                    "description": f"numpad {numpad['name']} Neru mode",
                    "manipulators": manipulators,
                })

    def _numpad_expand_action(self, numpad, action):
        keys = self._numpad_expand_key_names(numpad, action["key"])
        return [carry_origin({**action, "key": key}, action) for key in keys]

    def _numpad_expand_key_names(self, numpad, key):
        rows = numpad.get("rows", {})
        if key in rows:
            return rows[key]
        return [key]

    def _numpad_key_name(self, numpad, key):
        return numpad.get("key_aliases", {}).get(key, key)

    def _numpad_key_code(self, numpad, key):
        return resolve_key(self._numpad_key_name(numpad, key))

    def _numpad_parse_key_combo(self, numpad, expr):
        parts = expr.strip().split("+")
        parts[-1] = self._numpad_key_name(numpad, parts[-1])
        return parse_key_combo("+".join(parts))

    def _numpad_mode_manipulator(self, numpad, action, conditions, events):
        if action.get("action") == "pass_through" or not events:
            return None
        manipulator = {
            "type": "basic",
            "from": {
                "key_code": self._numpad_key_code(numpad, action["key"]),
            },
            "to": events,
            "conditions": conditions,
        }
        return self._record(manipulator, action, "numpad_mode")

    def _numpad_t9_action_events(self, action):
        kind = action.get("action")
        if kind == "exit":
            return [
                self._numpad_set_var_event(NUMPAD_T9_VARIABLE, 0),
                self._numpad_hud_event("--t9-mode", "off"),
            ]
        if kind == "clear":
            return [self._numpad_hud_event("--t9", "reset")]
        if kind == "commit":
            return [self._numpad_hud_event("--t9", "commit")]
        if kind == "commit_return":
            return [self._numpad_hud_event("--t9", "enter-return")]
        if kind == "backtrack":
            return [self._numpad_hud_event("--t9", "backspace")]
        if kind == "t9_token":
            return [self._numpad_hud_event("--t9", action["token"])]
        return []

    def _numpad_neru_action_events(self, action):
        kind = action.get("action")
        if kind == "exit":
            return [
                self._numpad_set_var_event(NUMPAD_NERU_VARIABLE, 0),
                self._numpad_neru_event("idle"),
                self._numpad_hud_event("--neru-mode", "off"),
            ]
        if kind == "click":
            return [
                self._numpad_neru_event("selection-click", action["button"]),
            ]
        if kind == "backtrack":
            return [self._numpad_neru_event("action", "backspace")]
        if kind == "reset":
            return [self._numpad_neru_event("action", "reset")]
        if kind == "select_center":
            return [self._make_to_event("sends", "5")]
        return []

    def _compile_numpad_layer_activation(self, numpad, layer):
        conditions = self._conditions_to_karabiner(
            self._numpad_idle_records(numpad))
        manipulator = {
            "type": "basic",
            "from": {
                "key_code": self._numpad_key_code(numpad, layer["key"]),
                "modifiers": {
                    "optional": ["any"],
                },
            },
            "to": [
                self._numpad_set_var_event(layer["variable"], 1),
                self._numpad_set_var_event(NUMPAD_PENDING_T9_VARIABLE, 0),
                self._numpad_set_var_event(NUMPAD_PENDING_NERU_VARIABLE, 0),
                self._numpad_hud_event("--nav-down"),
            ],
            "to_after_key_up": [
                self._numpad_set_var_event(layer["variable"], 0),
                self._numpad_set_var_event(NUMPAD_PENDING_T9_VARIABLE, 0),
                self._numpad_set_var_event(NUMPAD_PENDING_NERU_VARIABLE, 0),
                self._numpad_hud_event("--nav-up"),
            ],
            "conditions": conditions,
        }
        self.rules.append({
            "description":
            f"numpad {numpad['name']} {self._numpad_key_name(numpad, layer['key'])} + {layer['id']}",
            "manipulators": [manipulator],
        })
        self._record_rule(self.rules[-1], layer, "numpad_layer_activation")

    def _compile_numpad_layer_entries(self, numpad, layer):
        manipulators = []
        conditions = self._conditions_to_karabiner(
            self._numpad_idle_records(numpad) + [{
                "type": "variable",
                "name": layer["variable"],
                "value": 1,
            }])
        for entry in layer.get("entries", []):
            events = self._numpad_layer_entry_events(entry)
            if not events:
                continue
            manipulators.append({
                "type": "basic",
                "from": {
                    "key_code": self._numpad_key_code(numpad, entry["key"]),
                },
                "to": events,
                "conditions": conditions,
            })
            self._record(manipulators[-1], entry, "numpad_entry")
        if manipulators:
            self.rules.append({
                "description":
                f"numpad {numpad['name']} {layer['id']} mode entries",
                "manipulators": manipulators,
            })

    def _numpad_layer_entry_events(self, entry):
        if entry["mode"] == "t9":
            return [
                self._numpad_set_var_event(NUMPAD_PENDING_T9_VARIABLE, 1),
                self._numpad_set_var_event(NUMPAD_PENDING_NERU_VARIABLE, 0),
                self._numpad_hud_event("--queue-mode", "t9"),
            ]

        if entry["mode"] == "neru":
            queue_mode = {
                "left_click": "neru-left",
                "right_click": "neru-right",
            }.get(entry.get("action"), "neru")
            return [
                self._numpad_set_var_event(NUMPAD_PENDING_NERU_VARIABLE, 1),
                self._numpad_set_var_event(NUMPAD_PENDING_T9_VARIABLE, 0),
                self._numpad_hud_event("--queue-mode", queue_mode),
            ]

        return []

    def _compile_numpad_mapping_section(self, description, numpad, section,
                                        base_conditions):
        for app_section in section.get("apps", []):
            conditions = base_conditions + [{
                "type": "app",
                "values": app_section["apps"],
            }]
            self._compile_numpad_mappings(
                f"{description} ({', '.join(app_section['apps'])})",
                numpad,
                app_section["mappings"],
                conditions,
            )

        self._compile_numpad_mappings(
            f"{description} default",
            numpad,
            section.get("default", []),
            base_conditions,
        )

    def _compile_numpad_mappings(self, description, numpad, mappings,
                                 condition_records):
        manipulators = []
        conditions = self._conditions_to_karabiner(condition_records)
        for mapping in mappings:
            manipulators.extend(
                self._numpad_mapping_manipulators(numpad, mapping, conditions))
        if manipulators:
            self.rules.append({
                "description": description,
                "manipulators": manipulators,
            })

    def _numpad_mapping_manipulators(self, numpad, mapping, conditions):
        if mapping["type"] == "event":
            events = [self._make_to_event(mapping["verb"], mapping["target"])]
        else:
            events = [
                self._advanced_action_event(action)
                for action in self._expand_actions(mapping.get("actions", []))
            ]
            events = [event for event in events if event]

        if not events:
            return []

        from_key, from_mods, from_is_consumer = self._numpad_parse_key_combo(
            numpad, mapping["from"])
        optional = list(mapping.get("passthrough", []))
        manipulators = []
        for from_event in self._from_events(from_key,
                                            from_mods,
                                            optional,
                                            is_consumer=from_is_consumer):
            if mapping.get("optional_explicit"):
                self._explicit_optional.add(id(from_event))
            manipulators.append({
                "type": "basic",
                "from": from_event,
                "to": events,
                "conditions": conditions,
            })
            self._record(manipulators[-1], mapping, "numpad_mapping")
        return manipulators

    def _advanced_action_event(self, action):
        event = self._advanced_action_payload(action)
        if event is not None:
            self._action_origins[id(event)] = (event, origin_of(action))
        return event

    def _advanced_action_payload(self, action):
        if action["kind"] == "set_variable":
            payload = {
                "name": action["name"],
                "value": action["value"],
            }
            if action.get("key_up_value") is not None:
                payload["key_up_value"] = action["key_up_value"]
            return {"set_variable": payload}
        if action["kind"] == "wait":
            return {
                "key_code": "vk_none",
                "hold_down_milliseconds": action["milliseconds"],
            }
        if action["kind"] == "mouse_wheel":
            return {
                "mouse_key": {
                    action["axis"]: action["amount"],
                }
            }
        if action["kind"] == "mouse_move":
            return {
                "mouse_key": {
                    action["axis"]: action["amount"],
                }
            }
        if action["kind"] == "pointer_click":
            return {"pointing_button": action["button"]}
        if action["kind"] == "numpad_ax":
            if action["action"] == "sync":
                return self._numpad_ax_event(
                    "sync", "--delay-ms", str(action.get("delay_ms", 90)))
            if action["action"] == "click":
                return self._numpad_ax_event(
                    "click", "--button", action.get("button", "left"))
        event = self._make_to_event(action["verb"],
                                    action["target"],
                                    lazy=action.get("lazy", False))
        if action.get("halt"):
            event["halt"] = True
        return event

    def _compile_standalone(self):
        for s in self.p.standalone:
            if s["type"] == "together":
                self._compile_together(s)
            elif s["type"] == "combo":
                self._compile_combo(s)
            elif s["type"] == "simple":
                self._compile_simple(s)

    def _compile_together(self, s):
        keys = s["keys"].split("+")
        to_event = self._make_to_event(s["verb"], s["target"])

        m = {
            "type": "basic",
            "from": {
                "simultaneous": [{
                    "key_code": resolve_key(k)
                } for k in keys],
                "modifiers": {
                    "optional": ["any"]
                },
            },
            "to": [to_event],
        }

        conditions = self._conditions_to_karabiner(s.get("conditions", []))
        if conditions:
            m["conditions"] = conditions

        self.rules.append({
            "description": f"{s['keys']} together {s['verb']} {s['target']}",
            "manipulators": [m],
        })
        self._record_rule(self.rules[-1], s, "together")

    def _compile_combo(self, s):
        from_keys = s["keys"]
        target = s["target"]

        from_expanded = (ARROW_EXPANSION.get(from_keys)
                         or MULTI_KEY_EXPANSION.get(from_keys) or [from_keys])

        to_parts = target.rsplit("+", 1)
        to_multi = to_parts[-1] if len(to_parts) > 1 else target
        to_prefix = "+".join(to_parts[:-1]) + "+" if len(to_parts) > 1 else ""

        to_expanded = (ARROW_EXPANSION.get(to_multi)
                       or MULTI_KEY_EXPANSION.get(to_multi) or [to_multi])

        if len(to_expanded) == 1:
            to_expanded = to_expanded * len(from_expanded)

        trigger = s["trigger"]
        trigger_is_hyper = trigger == "$" and self.p.hyper_key
        hyper_var = self._hyper_var() if trigger_is_hyper else None

        manipulators = []
        for fk, tk in zip(from_expanded, to_expanded):
            full_to = to_prefix + tk
            to_event = self._make_to_event(s["verb"], full_to)

            from_event = {"key_code": resolve_key(fk)}
            if s.get("passthrough"):
                from_event["modifiers"] = {"optional": s["passthrough"]}

            m = {
                "type": "basic",
                "from": from_event,
                "to": [to_event],
            }

            conditions = []
            if hyper_var:
                conditions.append({
                    "type": "variable_if",
                    "name": hyper_var,
                    "value": 1
                })
            conditions.extend(
                self._conditions_to_karabiner(s.get("conditions", [])))
            if conditions:
                m["conditions"] = conditions

            manipulators.append(m)

        desc_trigger = self.p.hyper_key if trigger_is_hyper else trigger
        passthrough = format_modifier_suffix(s.get("passthrough", []))
        self.rules.append({
            "description":
            f"{desc_trigger} + {from_keys} {s['verb']} {target}{passthrough}",
            "manipulators": manipulators,
        })
        self._record_rule(self.rules[-1], s, "combo")

    def _compile_simple(self, s):
        to_event = self._make_to_event(s["verb"], s["target"])

        from_key, from_mods, from_is_consumer = parse_key_combo(s["from"])

        optional = list(s.get("passthrough", []))
        conditions = self._conditions_to_karabiner(s.get("conditions", []))

        manipulators = []
        for from_event in self._from_events(from_key,
                                            from_mods,
                                            optional,
                                            is_consumer=from_is_consumer):
            m = {
                "type": "basic",
                "from": from_event,
                "to": [to_event],
            }
            if conditions:
                m["conditions"] = conditions
            manipulators.append(m)

        passthrough = format_modifier_suffix(optional)
        self.rules.append({
            "description":
            f"{s['from']} {s['verb']} {s['target']}{passthrough}",
            "manipulators": manipulators,
        })
        self._record_rule(self.rules[-1], s, "simple")

    def _make_to_event(self, verb, target, lazy=False):
        if verb == "opens":
            dock_match = re.fullmatch(r"dock\s+(\d+)", target, re.IGNORECASE)
            if dock_match:
                event = {
                    "shell_command":
                    dock_slot_shell_command(int(dock_match.group(1)))
                }
            else:
                event = {"shell_command": f"open -a {shlex.quote(target)}"}
        elif verb == "runs":
            event = {"shell_command": target}
        else:
            key, mods, is_consumer = parse_key_combo(target)
            event = {}
            if is_consumer:
                event["consumer_key_code"] = key
            else:
                event["key_code"] = key
            if mods:
                event["modifiers"] = mods
        if lazy:
            event["lazy"] = True
        return event

    def _group_by_conditions(self, mappings):
        groups = {}
        for m in mappings:
            key = json.dumps(m.get("conditions", []), sort_keys=True)
            if key not in groups:
                groups[key] = []
            groups[key].append(m)
        return groups
