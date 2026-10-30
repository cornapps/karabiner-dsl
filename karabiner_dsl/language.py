"""Read the language declaration before any compilation or output side effects."""

from dataclasses import dataclass
import re

from .keys import strip_inline_comment


SUPPORTED_LANGUAGE_VERSIONS = (1,)


class LanguageError(ValueError):
    """The source requests a language mode this compiler cannot safely use."""


@dataclass(frozen=True)
class LanguageMode:
    version: int = 1
    explicit: bool = False
    header_line: int | None = None


def read_language(text):
    first_content = None
    header = None
    for number, raw in enumerate(text.splitlines(), 1):
        line = strip_inline_comment(raw).strip()
        if not line:
            continue
        if first_content is None:
            first_content = number
        if not re.match(r"language\b", line, re.IGNORECASE):
            continue
        # An action named "language" is not a version declaration.
        if line == "language:" and raw[:1].isspace():
            continue
        if header is not None:
            raise LanguageError(f"line {number}: duplicate language declaration")
        if number != first_content or raw[:1].isspace():
            raise LanguageError(
                f"line {number}: language declaration must be the first "
                "unindented statement")
        match = re.fullmatch(r"language\s+([1-9][0-9]*)", line)
        if not match:
            raise LanguageError(
                f"line {number}: expected 'language <version>', for example language 1")
        version = int(match.group(1))
        header = LanguageMode(version, True, number)
    if header is None:
        return LanguageMode()
    if header.version not in SUPPORTED_LANGUAGE_VERSIONS:
        supported = ", ".join(map(str, SUPPORTED_LANGUAGE_VERSIONS))
        raise LanguageError(
            f"line {header.header_line}: unsupported language version {header.version}; "
            f"this compiler supports {supported}. Use a compiler supporting the "
            "requested version; do not remove the declaration to bypass this check.")
    return header
