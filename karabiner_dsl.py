#!/usr/bin/env python3
"""karabiner_dsl.py — CLI shim for compiling .kb files to Karabiner-Elements JSON.

Usage:
    python karabiner_dsl.py config.kb [-o output.json]
    python karabiner_dsl.py --reverse config.json [-o output.kb]
    python karabiner_dsl.py --roundtrip config.kb
    python karabiner_dsl.py --install config.kb [-n name]
    python karabiner_dsl.py --lint config.kb

The command surface lives in karabiner_dsl/cli.py.
"""

from karabiner_dsl.cli import main

if __name__ == "__main__":
    main()
