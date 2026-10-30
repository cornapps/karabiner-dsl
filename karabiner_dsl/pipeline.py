"""Compile, round-trip, and output plumbing shared by the CLI."""

import json
from pathlib import Path

from .keys import strip_inline_comment
from .parser import Parser
from .compiler import Compiler
from .decompile import reverse_json_file


def normalize_kb_text(text):
    normalized_lines = []
    for raw_line in text.splitlines():
        line = strip_inline_comment(raw_line).rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip())
        normalized_lines.append((" " * indent) + line.strip())
    return "\n".join(normalized_lines).rstrip() + "\n"
def canonical_json(data):
    return json.dumps(data, indent=2, sort_keys=True)
def test_artifact_paths(path):
    source_path = Path(path)
    json_path = source_path.with_name(f"{source_path.stem}.test.json")
    kb_path = source_path.with_name(f"{source_path.stem}.test.kb")
    return json_path, kb_path
def roundtrip_file(path):
    input_path = Path(path)
    json_path, kb_path = test_artifact_paths(input_path)

    original_text = input_path.read_text()
    original_json = compile_text(original_text)
    write_output(json_path, json.dumps(original_json, indent=2))

    reversed_kb = reverse_json_file(json_path)
    write_output(kb_path, reversed_kb)

    regenerated_json = compile_text(reversed_kb)

    return {
        "input_path":
        str(input_path),
        "json_path":
        str(json_path),
        "kb_path":
        str(kb_path),
        "matches":
        normalize_kb_text(original_text) == normalize_kb_text(reversed_kb),
        "semantic_matches":
        canonical_json(original_json) == canonical_json(regenerated_json),
        "original":
        normalize_kb_text(original_text),
        "reversed":
        normalize_kb_text(reversed_kb),
    }
def write_output(path, content):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(content)
def compile_file(path):
    source_path = Path(path).resolve()
    text = source_path.read_text()
    return compile_text(text, source_path=source_path)
def compile_text(text, source_path=None):
    parser = Parser(text, source_path=source_path)
    compiler = Compiler(parser)
    return compiler.compile()
