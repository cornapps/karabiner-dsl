"""Lint pass over compiled output and parser warnings."""

from pathlib import Path
from dataclasses import asdict
import re

from .keys import KNOWN_KEY_CODES
from .analysis import analyze_text, legacy_notices


def collect_key_codes(obj):
    found = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key == "key_code" and isinstance(value, str):
                found.add(value)
            else:
                found.update(collect_key_codes(value))
    elif isinstance(obj, list):
        for item in obj:
            found.update(collect_key_codes(item))
    return found
def lint_key_codes(config):
    return [
        f"unknown key_code in compiled output: {code}"
        for code in sorted(collect_key_codes(config))
        if code not in KNOWN_KEY_CODES
    ]
def diagnostics_for(analysis, include_info=True):
    from .shadow import find_shadowed

    diagnostics = legacy_notices(analysis) if include_info else []
    for message in analysis.parser.warnings:
        line = re.match(r"line (\d+):", message)
        source = asdict(analysis.parser._location(int(line.group(1)))) if line else None
        diagnostics.append({"severity": "warning", "code": "source-warning",
                            "message": message, "source": source})
    for code in sorted(collect_key_codes(analysis.config) - KNOWN_KEY_CODES):
        sources = []
        for manipulator, origin in zip(analysis.manipulators, analysis.compiler.origins):
            if code in collect_key_codes(manipulator) and origin.source:
                source = asdict(origin.source)
                if source not in sources:
                    sources.append(source)
        message = f"unknown key_code in compiled output: {code}"
        if sources:
            message += "; mapping lines " + ", ".join(str(source["line"]) for source in sources)
        diagnostics.append({"severity": "warning", "code": "source-warning",
                            "message": message, "sources": sources})
    for proof in find_shadowed(analysis.manipulators, analysis.compiler.origins):
        source = proof["source"]
        location = f"line {source['line']}" if source else f"generated index {proof['indices'][0]}"
        covering = []
        for entry in proof["covered_by"]:
            where = f"line {entry['source']['line']}" if entry["source"] else f"generated index {entry['index']}"
            if where not in covering:
                covering.append(where)
        diagnostics.append({**proof, "severity": "warning", "code": "shadowed-" + proof["scope"],
                            "message": f"{location}: fully shadowed {proof['scope']} under legacy order; "
                                       "covered by " + ", ".join(covering)})
    return diagnostics


def diagnose_text(text, source_path=None):
    return diagnostics_for(analyze_text(text, source_path))


def lint_text(text, source_path=None):
    return [diagnostic["message"] for diagnostic in
            diagnostics_for(analyze_text(text, source_path), include_info=False)
            if diagnostic["severity"] == "warning"]


def lint_file(path):
    source_path = Path(path).resolve()
    return lint_text(source_path.read_text(), source_path=source_path)
