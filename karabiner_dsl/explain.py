"""Static explanations of generated order under explicitly supplied context."""

from dataclasses import asdict
import json

from .analysis import analyze_file, analyze_text, legacy_notices
from .keys import KNOWN_KEY_CODES, parse_key_combo
from .matching import pick_winner
from .lint import diagnostics_for


def key_event(key):
    code, modifiers, consumer = parse_key_combo(key)
    if modifiers:
        raise ValueError("Supply one key with --key; use --modifiers for physical modifiers")
    if not consumer and code not in KNOWN_KEY_CODES:
        raise ValueError(f"Unknown supplied key: {key}")
    return {"consumer_key_code" if consumer else "key_code": code}


def explain_analysis(analysis, event=None, context=None, filter_key=None):
    event, context = dict(event or {}), dict(context or {})
    manipulators = analysis.manipulators
    result = pick_winner(manipulators, event, context)
    selector = key_event(filter_key) if filter_key else None
    candidates = []
    for candidate, manipulator, origin in zip(result["candidates"], manipulators,
                                               analysis.compiler.origins):
        if selector and not all(manipulator["from"].get(key) == value
                                for key, value in selector.items()):
            continue
        candidates.append({**candidate, "origin": json.loads(json.dumps(asdict(origin))),
                           "manipulator": manipulator})
    return {**result, "candidates": candidates,
            "language": {**asdict(analysis.parser.language), "ordering": "legacy"},
            "supplied": {"event": event, "context": context},
            "filter_key": filter_key,
            "scope": "Static complex-modification dispatch after simple modifications; "
                     "context is supplied, not observed live state. Timing is not simulated.",
            "notices": legacy_notices(analysis),
            "diagnostics": diagnostics_for(analysis, include_info=False)}


def explain_text(text, event=None, context=None, filter_key=None, source_path=None):
    return explain_analysis(analyze_text(text, source_path), event, context, filter_key)


def explain_file(path, event=None, context=None, filter_key=None):
    return explain_analysis(analyze_file(path), event, context, filter_key)


def format_explanation(result):
    mode = result["language"]
    header = "explicit" if mode["explicit"] else "unversioned"
    lines = [f"Language {mode['version']}: {mode['ordering']} order ({header})",
             result["scope"],
             "Supplied event: " + json.dumps(result["supplied"]["event"], sort_keys=True),
             "Supplied context: " + json.dumps(result["supplied"]["context"], sort_keys=True)]
    for diagnostic in result["diagnostics"]:
        lines.append(f"{diagnostic['severity']} [{diagnostic['code']}]: {diagnostic['message']}")
    if result["filter_key"]:
        lines.append(f"Display filter: {result['filter_key']} (does not supply event facts)")
    winner = str(result["winner_index"]) if result["winner_definitive"] else result["status"]
    lines.append(f"Winner: {winner}")
    lines.append("Possible indices: " + ", ".join(map(str, result["possible_indices"])))
    if result["missing_facts"]:
        lines.append("Missing facts: " + ", ".join(result["missing_facts"]))
    for candidate in result["candidates"]:
        origin = candidate["origin"]
        source = origin["origin"]["source"]
        location = f"line {source['line']}" if source else "unknown source"
        eligibility = "possible winner" if candidate["possible"] else "excluded from winning"
        lines.append(f"[{candidate['index']}] {candidate['status']}; {eligibility}; "
                     f"{location}; {origin['generator']}; unit {origin['origin']['unit_id']}")
        lines.append("  " + json.dumps(candidate["manipulator"], sort_keys=True))
        for reason in candidate["reasons"]:
            lines.append(f"  {reason['status']}: {reason['path']}: {reason['message']}")
        for action in origin["actions"]:
            path = ""
            for part in action["path"]:
                path += f"[{part}]" if isinstance(part, int) else ("." if path else "") + part
            source = action["origin"]["source"]
            location = f"line {source['line']}, column {source['column']}" if source else "unknown source"
            if source and source["path"]:
                location += f" ({source['path']})"
            lines.append(f"  action {path}: {location}")
            for call in action["origin"]["macro_calls"]:
                call_line = call["call"]["line"] if call["call"] else "unknown"
                definition = call["definition"]["line"] if call["definition"] else "unknown"
                lines.append(f"  action {call['name']}: call line {call_line}, definition line {definition}")
    return "\n".join(lines) + "\n"
