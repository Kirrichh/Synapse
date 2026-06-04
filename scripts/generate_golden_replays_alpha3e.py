#!/usr/bin/env python3
"""Regenerate the alpha3e strict golden replay suite.

This script is intentionally small and deterministic. It should be run only when
updating the alpha3e golden baseline as part of an approved release-gate change.
"""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from synapse.golden_replay import record_source

OUT = ROOT / "tests" / "golden_replays_alpha3e" / "strict"
SRC = ROOT / "tests" / "golden_sources_alpha3e"

PROGRAMS = {
    "print_math": 'let x = 2 + 3\nprint(x)\n',
    "llm_cached": 'let a = llm "hello"\nprint(a)\n',
    "nested_context": 'context "work" {\n    print("inside")\n}\n',
    "inline_guard_pass": '''fn main() {
    try {
        memory.write("x") { guard true }
    } catch (GUARD_VIOLATION) {
        print("denied")
    }
}
''',
    "inline_guard_fail_recovery": '''fn main() {
    try {
        memory.write("x") { guard false }
    } catch (GUARD_VIOLATION) {
        print("denied")
    }
}
''',
    "actor_message": '''agent Worker {
    model "mock"
}
send Worker.process("job-42")
print("sent")
''',
}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    SRC.mkdir(parents=True, exist_ok=True)
    for name, source in PROGRAMS.items():
        source_path = SRC / f"{name}.syn"
        source_path.write_text(source, encoding="utf-8")
        record_source(source, OUT / name, source_path=str(source_path.relative_to(ROOT)), layer="strict")
        print(f"recorded {name}")


if __name__ == "__main__":
    main()
