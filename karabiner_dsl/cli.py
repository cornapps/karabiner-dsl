"""Command line entry point for the Karabiner DSL.

The installed console script, module entry point, and repository script use
the same flags, diagnostics, and compiler.
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from ._version import __version__
from .keys import MissingHelpersError
from .language import LanguageError, SUPPORTED_LANGUAGE_VERSIONS
from .analysis import analyze_file
from .explain import explain_analysis, format_explanation, key_event
from .keys import parse_device_ids
from .lint import diagnostics_for

from .dsl import (
    compile_file,
    reverse_json_file,
    roundtrip_file,
    write_output,
)

# Karabiner-Elements configuration directory
KARABINER_CONFIG_DIR = Path.home() / ".config" / "karabiner" / "assets" / "complex_modifications"
KARABINER_CONFIG_FILE = Path.home() / ".config" / "karabiner" / "karabiner.json"


def report_lint(input_path, include_info=False, as_json=False):
    analysis = analyze_file(input_path)
    diagnostics = diagnostics_for(analysis, include_info=include_info)
    warnings = [item["message"] for item in diagnostics if item["severity"] == "warning"]
    if as_json:
        print(json.dumps({"language": asdict(analysis.parser.language),
                          "diagnostics": diagnostics}, indent=2))
    else:
        for item in diagnostics:
            code = f" [{item['code']}]" if item["severity"] == "info" else ""
            print(f"{item['severity']}{code}: {item['message']}", file=sys.stderr)
    return warnings


def install_config(input_path, name=None, lint=True):
    """Compile a .kb file and install it to Karabiner-Elements."""
    input_path = Path(input_path)

    print(f"Compiling {input_path}...", file=sys.stderr)
    if lint:
        report_lint(input_path)
    result = compile_file(input_path)

    if not isinstance(result, dict) or ("rules" not in result and "profiles" not in result):
        print("ERROR: Compilation failed or produced invalid output", file=sys.stderr)
        if lint:
            print(f"Result: {result}", file=sys.stderr)
        sys.exit(1)
    print("Compilation successful", file=sys.stderr)

    if KARABINER_CONFIG_FILE.exists():
        backup_base = KARABINER_CONFIG_FILE.with_suffix(".json.bak")
        counter = 1
        backup_path = backup_base
        while backup_path.exists():
            backup_path = Path(str(backup_base) + f".{counter}")
            counter += 1
        KARABINER_CONFIG_FILE.rename(backup_path)
        print(f"Backed up current config to {backup_path}", file=sys.stderr)

    with open(KARABINER_CONFIG_FILE, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Installed to {KARABINER_CONFIG_FILE}", file=sys.stderr)

    try:
        subprocess.run(
            ["open", "-g", "karabiner://localhost"],
            capture_output=True,
            timeout=5,
        )
        print("Karabiner-Elements reloaded", file=sys.stderr)
    except Exception:
        pass  # Best effort


def build_parser(legacy=False):
    parser = argparse.ArgumentParser(
        prog="karabiner-dsl",
        description="Compile Karabiner-DSL files to JSON or manage configurations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    versions = ", ".join(map(str, SUPPORTED_LANGUAGE_VERSIONS))
    parser.add_argument("--version", action="version",
                        version=f"karabiner-dsl {__version__} (languages: {versions})")
    parser.add_argument("-o", "--output", help="Output file")
    parser.add_argument("-n", "--name", help="Config name for --install/--apply")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--lint", action="store_true", help="Lint a KB file; exit 1 on warnings")
    group.add_argument("--explain", action="store_true", help="Explain generated order under supplied context")
    group.add_argument("--reverse", action="store_true", help="Decompile JSON to KB")
    group.add_argument("--roundtrip", action="store_true", help="Test roundtrip compilation")
    group.add_argument("--install", action="store_true", help="Install to Karabiner-Elements")
    group.add_argument(
        "--apply",
        action="store_true",
        help="Alias for --install (installs to Karabiner-Elements)",
    )

    parser.add_argument("--json", action="store_true", help="JSON diagnostics for --lint or --explain")
    parser.add_argument("--key", help="Supplied complex-stage input key for --explain")
    parser.add_argument("--filter-key", help="Display only this key; does not supply input facts")
    parser.add_argument("--modifiers", help="Supplied physical modifiers, comma-separated, or none")
    parser.add_argument("--device", help="Supplied device registry name or vendor:product ID")
    parser.add_argument("--device-enabled", choices=("yes", "no"), help="Supplied eligibility for complex modifications")
    parser.add_argument("--app", help="Supplied frontmost app bundle ID")
    parser.add_argument("--var", action="append", default=[], metavar="NAME=VALUE", help="Supplied variable value (repeatable)")
    parser.add_argument("--context", help="JSON file with supplied app_bundle, device, device_enabled, variables")
    parser.add_argument("input", nargs="?", help="Input .kb or .json file")
    return parser


def run(argv=None, legacy=False):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _execute(args, parser)
    except (LanguageError, MissingHelpersError) as error:
        parser.error(str(error))


def _execute(args, parser):
    if not args.input:
        parser.print_help()
        sys.exit(1)

    input_path = args.input

    context_options = (args.key, args.filter_key, args.modifiers, args.device,
                       args.device_enabled, args.app, args.context)
    if (any(value is not None for value in context_options) or args.var) and not args.explain:
        parser.error("context and key options require --explain")
    if args.json and not (args.lint or args.explain):
        parser.error("--json requires --lint or --explain")

    if args.explain:
        analysis = analyze_file(input_path)
        try:
            event, context = _explain_context(args, analysis)
            result = explain_analysis(analysis, event, context, args.filter_key)
        except (ValueError, OSError) as error:
            parser.error(str(error))
        output = json.dumps(result, indent=2) + "\n" if args.json else format_explanation(result)
        if args.output:
            write_output(args.output, output)
        else:
            print(output, end="")
        return

    if getattr(args, "lint", False):
        warnings = report_lint(input_path, include_info=True, as_json=args.json)
        if warnings:
            sys.exit(1)
        if not args.json:
            print(f"{input_path}: lint clean", file=sys.stderr)

    elif args.reverse:
        output = reverse_json_file(input_path)
        if args.output:
            write_output(args.output, output)
            print(f"Written to {args.output}", file=sys.stderr)
        else:
            print(output, end="")

    elif args.roundtrip:
        report_lint(input_path)
        result = roundtrip_file(input_path)
        if not result["semantic_matches"]:
            print("Round-trip failed", file=sys.stderr)
            print("--- normalized original kb ---", file=sys.stderr)
            print(result["original"], file=sys.stderr, end="")
            print("--- normalized reversed kb ---", file=sys.stderr)
            print(result["reversed"], file=sys.stderr, end="")
            sys.exit(1)
        print(json.dumps({
            "status": "ok",
            "input": result["input_path"],
            "json_output": result["json_path"],
            "kb_output": result["kb_path"],
            "kb_text_match": result["matches"],
            "semantic_match": result["semantic_matches"],
            "comparison": "normalized_kb_ignoring_comments",
        }, indent=2))

    elif args.install or args.apply:
        install_config(input_path, args.name)

    else:
        report_lint(input_path)
        result = compile_file(input_path)
        output = json.dumps(result, indent=2)
        if args.output:
            write_output(args.output, output)
            print(f"Written to {args.output}", file=sys.stderr)
        else:
            print(output)


def _explain_context(args, analysis):
    event = key_event(args.key) if args.key else {}
    context = json.loads(Path(args.context).read_text()) if args.context else {}
    allowed = {"device", "device_enabled", "app_bundle", "variables"}
    if not isinstance(context, dict) or set(context) - allowed:
        raise ValueError("Context must be an object with device, device_enabled, app_bundle, variables")
    for name, expected in (("device", dict), ("device_enabled", bool),
                           ("app_bundle", str), ("variables", dict)):
        if name in context and type(context[name]) is not expected:
            raise ValueError(f"Context {name} must be {expected.__name__}")

    def supply(name, value):
        if name in context and context[name] != value:
            raise ValueError(f"Conflicting supplied {name}")
        context[name] = value

    if args.modifiers is not None:
        modifiers = [] if args.modifiers == "none" else [value.strip() for value in args.modifiers.split(",")]
        if not all(modifier in {"fn", "caps_lock"} or
                   re.fullmatch(r"(?:left|right)_(?:command|control|option|shift)", modifier)
                   for modifier in modifiers):
            raise ValueError("Use physical modifier names such as left_shift, or none")
        event["modifiers"] = modifiers
    if args.device is not None:
        definition = analysis.parser.device_definitions.get(args.device, {})
        vendor, product = parse_device_ids(definition.get("id", args.device))
        device = dict(context.get("device", {}))
        for field, value in (("vendor_id", vendor), ("product_id", product)):
            if field in device and (type(device[field]) is not int or device[field] != value):
                raise ValueError(f"Conflicting supplied device {field}")
            device[field] = value
        context["device"] = device
    if args.device_enabled is not None:
        supply("device_enabled", args.device_enabled == "yes")
    if args.app is not None:
        supply("app_bundle", args.app)
    variables = dict(context.get("variables", {}))
    for assignment in args.var:
        if "=" not in assignment:
            raise ValueError("Variables require NAME=VALUE")
        name, raw = assignment.split("=", 1)
        name = name.removeprefix("@")
        if not name or not raw:
            raise ValueError("Variables require a name and value")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        if type(value) not in (str, int, bool):
            raise ValueError("Variable values must be integers, booleans, or strings")
        if name in variables and (type(variables[name]) is not type(value) or variables[name] != value):
            raise ValueError(f"Conflicting supplied variable {name}")
        variables[name] = value
    if args.var:
        context["variables"] = variables
    return event, context


def main(argv=None):
    """Command line entry point used by karabiner_dsl.py and python -m."""
    run(argv, legacy=False)


def legacy_main(argv=None):
    """Compatibility import for callers of the former narrower command."""
    return main(argv)


if __name__ == "__main__":
    main()
