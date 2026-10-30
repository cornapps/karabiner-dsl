"""Compile once with source provenance for diagnostics and static analysis."""

from dataclasses import dataclass
from pathlib import Path

from .compiler import Compiler
from .parser import Parser
from .origins import origin_of


@dataclass
class Analysis:
    parser: Parser
    compiler: Compiler
    config: dict

    @property
    def manipulators(self):
        return [m for profile in self.config["profiles"]
                for rule in profile["complex_modifications"]["rules"]
                for m in rule["manipulators"]]


def analyze_text(text, source_path=None):
    parser = Parser(text, source_path=source_path)
    compiler = Compiler(parser)
    return Analysis(parser, compiler, compiler.compile())


def analyze_file(path):
    path = Path(path).resolve()
    return analyze_text(path.read_text(), source_path=path)


def legacy_notices(analysis):
    """Inform about legacy mode without turning compatibility into an error."""
    parser = analysis.parser
    if parser.language.explicit:
        return []
    notices = [{"severity": "info", "code": "legacy-mode", "message":
                "Unversioned source uses language 1 and legacy compilation order; "
                "add language 1 to acknowledge this mode. Language 2 is not yet supported."}]
    reasons = {}
    units = {unit.id: unit for unit in parser.units}
    blocks = {origin_of(block).unit_id: block for block in parser.advanced_basic}
    seen = set()
    for origin in analysis.compiler.origins:
        if origin.generator != "advanced_basic" or origin.unit_id in seen:
            continue
        seen.add(origin.unit_id)
        unit = units.get(origin.unit_id)
        if unit is None:
            continue
        source = parser._strip_comment(parser.lines[unit.source.line - 1]).strip()
        block = blocks[unit.id]
        actions = [action for steps in block["actions"].values() for action in steps]
        if source.startswith("basic "):
            reason = "explicit basic"
        elif unit.kind == "comma":
            reason = "comma-separated triggers"
        elif unit.kind in ("key_block", "simultaneous"):
            reason = "key or simultaneous blocks"
        elif source.split()[0].startswith("any+"):
            reason = "any+ trigger prefix"
        elif source.split()[0] in ("swap", "swaps"):
            reason = "scoped swaps"
        elif any(action["kind"] == "action_ref" for action in actions):
            reason = "triggers verb"
        elif any(action.get("lazy") or action.get("halt") for action in actions):
            reason = "lazy/halt action attributes"
        elif actions and all(action["kind"] == "event" for action in actions):
            reason = "inherited ignores modifiers"
        else:
            # Inline structured actions follow the trigger. Never inspect
            # macro names or event payloads for attribute-like words.
            reason = source.split()[1] + " verb"
        reasons.setdefault(reason, []).append(unit.source.line)
    for reason, lines in reasons.items():
        notices.append({"severity": "info", "code": "legacy-promotion",
                        "source_lines": lines, "reason": reason,
                        "message": f"Legacy advanced-basic priority from {reason} at lines "
                        + ", ".join(map(str, lines)) + "."})
    return notices
