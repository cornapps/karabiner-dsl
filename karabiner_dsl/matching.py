"""Conservative static dispatch over generated Karabiner manipulators.

This module has no compiler dependencies or side effects. Events describe the
complex-modification input (after any simple modifications). Omitted facts are
unknown, including modifiers and device_enabled; [] means no modifiers. Device
identity describes one physical interface, not a registry name. Variables are
only the values explicitly supplied, with no assumed Karabiner defaults.

Results are JSON dictionaries: status is match/no_match/unknown, reasons is a
list of {path, status, message}, and missing_facts lists absent context/event
paths. Unsupported predicates have reasons but cannot be resolved by inventing
context. Action/lifecycle payloads are neither interpreted nor changed.

Simultaneous recognition always remains unknown in this initial static slice:
a list of keys does not establish timing, interruption, or release history.
Regex support is the common Python/ECMAScript subset used for bundle patterns;
extensions and alphabetic escapes are conservatively left unknown.
"""

import re


_EVENT_KEYS = {"key_code", "consumer_key_code", "pointing_button"}
_ALIASES = {
    "left_alt": "left_option", "right_alt": "right_option",
    "left_gui": "left_command", "right_gui": "right_command",
}
_FAMILIES = {
    name: {f"left_{name}", f"right_{name}"}
    for name in ("command", "control", "option", "shift")
}
_MODIFIERS = set().union(*_FAMILIES.values(), {"caps_lock", "fn"})
_DEVICE_TYPES = {
    "vendor_id": int, "product_id": int, "location_id": int,
    "device_address": str, "is_keyboard": bool, "is_pointing_device": bool,
    "is_game_pad": bool, "is_consumer": bool, "is_touch_bar": bool,
    "is_built_in_keyboard": bool,
}
_PASSIVE_FIELDS = {
    "description", "to", "to_if_alone", "to_if_held_down", "to_after_key_up",
    "to_if_other_key_pressed", "to_delayed_action", "parameters",
}


def _result(status, path, message, missing=False):
    return {"status": status,
            "reasons": [{"path": path, "status": status, "message": message}],
            "missing_facts": [path] if missing else []}


def _unknown(path, message="Unsupported predicate", missing=False):
    return _result("unknown", path, message, missing)


def _known(value, path, message):
    return _result("match" if value else "no_match", path, message)


def _combine(parts, any_of=False):
    parts = list(parts)
    statuses = [part["status"] for part in parts]
    decisive, identity = ("match", "no_match") if any_of else ("no_match", "match")
    status = decisive if decisive in statuses else (
        "unknown" if "unknown" in statuses else identity)
    return {"status": status,
            "reasons": [reason for part in parts for reason in part["reasons"]],
            "missing_facts": sorted({fact for part in parts for fact in part["missing_facts"]})}


def _negate(result, path):
    status = {"match": "no_match", "no_match": "match", "unknown": "unknown"}[result["status"]]
    return {**result, "status": status, "reasons": result["reasons"] + [
        {"path": path, "status": status, "message": "Negated condition"}]}


def _extras(value, supported, path):
    return [_unknown(f"{path}.{key}") for key in value if key not in supported]


def _equal_fact(facts, key, expected, path, expected_type):
    if type(expected) is not expected_type:
        return _unknown(path, "Unsupported predicate value")
    if key not in facts or type(facts[key]) is not expected_type:
        return _unknown(path, "Fact absent or not concrete", missing=True)
    return _known(facts[key] == expected, path, "Compared supplied value")


def _modifier_set(name):
    name = _ALIASES.get(name, name)
    return _FAMILIES.get(name, {name} if name in _MODIFIERS else set())


def _modifiers(spec, event):
    path = "from.modifiers"
    if not isinstance(spec, dict):
        return _unknown(path, "Expected modifier object")
    parts = _extras(spec, {"mandatory", "optional"}, path)
    mandatory, optional = spec.get("mandatory", []), spec.get("optional", [])
    if not all(isinstance(items, list) and all(isinstance(x, str) for x in items)
               for items in (mandatory, optional)):
        return _unknown(path, "Expected modifier lists")
    actual = event.get("modifiers")
    if not isinstance(actual, list) or not all(isinstance(x, str) for x in actual):
        return _combine(parts + [_unknown("event.modifiers", "Modifiers absent or not concrete", True)])
    actual = {_ALIASES.get(x, x) for x in actual}
    unknown_actual = actual - _MODIFIERS
    if unknown_actual:
        parts.append(_unknown("event.modifiers", "Expected physical left/right modifier names", True))
    allowed = set()
    unknown_spec = False
    for name in mandatory:
        family = _modifier_set(name)
        if not family:  # Includes mandatory:any, whose semantics are not modeled.
            unknown_spec = True
            parts.append(_unknown(path + ".mandatory", f"Unsupported modifier: {name}"))
        else:
            allowed.update(family)
            if actual & family:
                parts.append(_known(True, path + ".mandatory", f"Required modifier present: {name}"))
            elif unknown_actual:
                parts.append(_unknown(path + ".mandatory", f"Cannot establish modifier: {name}"))
            else:
                parts.append(_known(False, path + ".mandatory", f"Required modifier absent: {name}"))
    for name in optional:
        if name != "any":
            family = _modifier_set(name)
            allowed.update(family)
            if not family:
                unknown_spec = True
                parts.append(_unknown(path + ".optional", f"Unsupported modifier: {name}"))
    extras = (actual & _MODIFIERS) - allowed
    if "any" not in optional and extras:
        parts.append(_unknown(path, "Unsupported modifier may affect extra-modifier coverage")
                     if unknown_spec else _known(False, path, "Extra modifiers are not allowed"))
    if not parts:
        parts.append(_known(True, path, "Supplied modifiers are allowed"))
    return _combine(parts)


def _from(spec, event):
    if not isinstance(spec, dict):
        return _unknown("from", "Missing or invalid from object")
    parts = _extras(spec, _EVENT_KEYS | {"modifiers", "simultaneous"}, "from")
    triggers = _EVENT_KEYS.intersection(spec)
    if len(triggers) + ("simultaneous" in spec) != 1:
        parts.append(_unknown("from", "Expected one event selector"))
    for key in sorted(triggers):
        if key not in event and _EVENT_KEYS.intersection(event):
            parts.append(_known(False, "event." + key, "Different event kind"))
        else:
            parts.append(_equal_fact(event, key, spec[key], "event." + key, str))
    if "simultaneous" in spec:
        parts.append(_unknown("event.simultaneous", "Simultaneous timing, interruption and release history are not modeled",
                              missing="simultaneous" not in event))
    parts.append(_modifiers(spec.get("modifiers", {}), event))
    return _combine(parts)


def _device(condition, context, path):
    identifiers = condition.get("identifiers")
    if not isinstance(identifiers, list) or not identifiers:
        return _unknown(path + ".identifiers", "Missing or empty device alternatives")
    device = context.get("device", {})
    if not isinstance(device, dict):
        device = {}
    alternatives = []
    for identifier in identifiers:
        if not isinstance(identifier, dict) or not (set(identifier) - {"description"}):
            alternatives.append(_unknown(path + ".identifiers", "Empty or invalid device identifier"))
            continue
        fields = []
        for key, value in identifier.items():
            if key in _DEVICE_TYPES:
                fields.append(_equal_fact(device, key, value, "context.device." + key, _DEVICE_TYPES[key]))
            elif key != "description":
                fields.append(_unknown(path + ".identifiers." + key))
        alternatives.append(_combine(fields))
    return _combine(alternatives, any_of=True)


def _application(condition, context, path):
    alternatives = []
    patterns = condition.get("bundle_identifiers", [])
    if not isinstance(patterns, list):
        alternatives.append(_unknown(path + ".bundle_identifiers", "Expected pattern list"))
    else:
        for pattern in patterns:
            if not isinstance(pattern, str):
                alternatives.append(_unknown(path, "Expected regex string"))
                continue
            # C++ bracket classes/collating/equivalence elements and Python
            # extensions are outside the common subset evaluated here.
            if ("(?" in pattern or re.search(r"\\[A-Za-z0-9]", pattern)
                    or re.search(r"[+*?}]\+", pattern)
                    or any(marker in pattern for marker in ("[:", "[.", "[="))):
                alternatives.append(_unknown(path, "Regex outside supported common subset"))
                continue
            try:
                regex = re.compile(pattern, re.ASCII)
            except re.error:
                alternatives.append(_unknown(path, "Invalid regex"))
                continue
            if not isinstance(context.get("app_bundle"), str):
                alternatives.append(_unknown("context.app_bundle", "App bundle absent", True))
            else:
                alternatives.append(_known(bool(regex.search(context["app_bundle"])), path, "Compared app bundle regex"))
    # File paths are OR alternatives, not an additional AND constraint.
    if condition.get("file_paths") != [] and "file_paths" in condition:
        alternatives.append(_unknown(path + ".file_paths", "App file paths are not modeled"))
    return _combine(alternatives, any_of=True) if alternatives else _unknown(path, "No app alternatives")


def _condition(condition, context, path):
    if not isinstance(condition, dict):
        return _unknown(path, "Expected condition object")
    kind = condition.get("type")
    if kind in ("device_if", "device_unless"):
        result = _device(condition, context, path)
        supported = {"identifiers"}
    elif kind in ("frontmost_application_if", "frontmost_application_unless"):
        result = _application(condition, context, path)
        supported = {"bundle_identifiers", "file_paths"}
    elif kind in ("variable_if", "variable_unless"):
        supported = {"name", "value"}
        name, value = condition.get("name"), condition.get("value")
        variables = context.get("variables", {})
        if not isinstance(name, str) or type(value) not in (str, int, bool):
            result = _unknown(path, "Unsupported variable name/value")
        elif not isinstance(variables, dict) or name not in variables:
            result = _unknown("context.variables." + name, "Variable value absent", True)
        elif type(variables[name]) not in (str, int, bool):
            result = _unknown("context.variables." + name, "Unsupported variable value")
        else:
            result = _known(type(variables[name]) is type(value) and variables[name] == value,
                            path, "Compared variable value and type")
    else:
        return _unknown(path, f"Unsupported condition type: {kind}")
    if kind.endswith("_unless"):
        result = _negate(result, path)
    return _combine([result] + _extras(condition, supported | {"type", "description"}, path))


def match_manipulator(manipulator, event, context=None):
    """Return static eligibility, retaining missing facts and predicate reasons.

    context supports app_bundle (str), device (identifier dict), variables
    (supplied values only), and device_enabled (bool). The latter means this
    input is eligible for the complex stage, independently of rule emission.
    event supports native key_code/consumer_key_code/pointing_button selectors,
    modifiers (physical names), and simultaneous (recognized as temporal).
    """
    if not isinstance(manipulator, dict):
        return _unknown("manipulator", "Expected manipulator object")
    event = event if isinstance(event, dict) else {}
    context = context if isinstance(context, dict) else {}
    parts = [_equal_fact(context, "device_enabled", True, "context.device_enabled", bool)]
    if manipulator.get("type") != "basic":
        parts.append(_unknown("type", "Only basic manipulators are modeled"))
    parts += _extras(manipulator, _PASSIVE_FIELDS | {"type", "from", "conditions"}, "manipulator")
    parts.append(_from(manipulator.get("from"), event))
    conditions = manipulator.get("conditions", [])
    if isinstance(conditions, list):
        parts.extend(_condition(condition, context, f"conditions[{index}]")
                     for index, condition in enumerate(conditions))
    else:
        parts.append(_unknown("conditions", "Expected condition list"))
    return _combine(parts)


def pick_winner(manipulators, event, context=None):
    """Return the definite first winner, or conservative possible indices.

    candidates retain every index and its independent match status. A later
    candidate is ruled out by order after the first known matching predicate,
    even if an earlier unknown prevents naming that match as the winner.
    possible_indices remains a conservative set, not a reachability proof.
    winner_index is null unless definite; no_match excludes every candidate.
    """
    candidates, possible, winner, blocked = [], [], None, False
    for index, manipulator in enumerate(manipulators):
        result = match_manipulator(manipulator, event, context)
        can_win = not blocked and result["status"] != "no_match"
        if can_win:
            if not possible and result["status"] == "match":
                winner = index
            possible.append(index)
            if result["status"] == "match":
                blocked = True
        candidates.append({"index": index, **result, "possible": can_win})
    return {"status": "match" if winner is not None else "unknown" if possible else "no_match",
            "winner_index": winner, "winner_definitive": winner is not None,
            "possible_indices": possible, "candidates": candidates,
            "missing_facts": sorted({fact for candidate in candidates if candidate["possible"]
                                     for fact in candidate["missing_facts"]})}
