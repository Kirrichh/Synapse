#!/usr/bin/env python3
"""Synapse pre-commit corpus gate.

Usage (standalone):  python3 scripts/pre_commit_hook.py
Install as git hook: cp scripts/pre_commit_hook.py .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

Blocks commit if:
  1. Python < 3.10 is detected.
  2. Any .syn file in examples/ fails to parse.
  3. Corpus CVM coverage drops below COVERAGE_FLOOR.
"""
import sys
from pathlib import Path

# --- Gate 0: Python version ---
if sys.version_info < (3, 10):
    print(
        f'pre-commit FAILED: Python {sys.version_info.major}.{sys.version_info.minor} detected. '
        'Synapse requires Python >= 3.10 (match/case, strict typing).',
        file=sys.stderr,
    )
    sys.exit(1)

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from synapse.lexer import Lexer
from synapse.parser import Parser

COVERAGE_FLOOR = 0.9332
EXAMPLES_DIR = ROOT / "examples"

errors = []

# --- Gate 1: all .syn examples must parse ---
for syn_file in sorted(EXAMPLES_DIR.glob("*.syn")):
    try:
        src = syn_file.read_text()
        tokens = Lexer(src).scan_tokens()
        Parser(tokens).parse()
    except Exception as e:
        errors.append(f"  PARSE FAIL: {syn_file.name}: {e}")

if errors:
    print("pre-commit FAILED: example parse failures detected:", file=sys.stderr)
    for err in errors:
        print(err, file=sys.stderr)
    print("\nFix examples/ before committing.", file=sys.stderr)
    sys.exit(1)

print(f"Gate 1 passed: all {sum(1 for _ in EXAMPLES_DIR.glob('*.syn'))} .syn files parse OK")

# --- Gate 2: corpus coverage must not regress ---
try:
    from scripts.corpus_fallback_audit import build_report
    report = build_report(["examples", "tests"], base_dir=ROOT)
    ratio = report.get("corpus_coverage_ratio", 0.0)
    if ratio < COVERAGE_FLOOR:
        print(
            f"pre-commit FAILED: corpus coverage {ratio:.6f} < floor {COVERAGE_FLOOR}",
            file=sys.stderr,
        )
        print("Run scripts/corpus_fallback_audit.py and fix regressions.", file=sys.stderr)
        sys.exit(1)
    print(f"Gate 2 passed: coverage {ratio:.6f} >= {COVERAGE_FLOOR} "
          f"({report['files_parse_ok']}/{report['files_scanned']} files)")
except ImportError as e:
    print(f"Gate 2 skipped: corpus_fallback_audit not importable ({e})")

print("pre-commit: all gates passed.")
sys.exit(0)
