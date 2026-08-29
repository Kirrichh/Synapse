"""
Synapse Version Authority
Единый источник истины для версий языка, рантайма и спецификации.
Любое рассогласование блокируется тестом tests/test_version_sync.py

Release line: 2.2.0-alpha3e
  - Stabilisation patch over alpha3d5 (alpha3e-p0)
  - Track B: Guard Blocks in Bytecode (alpha3e-track-b)
  - Track B.1: Source-level inline guard lowering and strict lexical checked effects
  - Golden Replay Suite: deterministic Layer 1 strict baseline and Layer 2 corpus smoke gate
  - Fixes parse failures in examples/ (full_demo.syn, math.syn, memory_demo.syn)
  - Introduces contextual identifier (soft keyword) rules in parser
  - Host ABI bumped: MSG_SEND / MSG_RECEIVE opcodes added in alpha3d5
    confirm a VM-visible host-call surface change; b2 version was stale.
"""
LANGUAGE_VERSION = "2.2.0-alpha3e"
RUNTIME_VERSION  = "0.22.0-alpha3e"
SPEC_VERSION     = "2.2.0-alpha3e"

__version__ = RUNTIME_VERSION

# ---------------------------------------------------------------------------
# PEP 440 distribution version (derived; do not edit by hand).
# ---------------------------------------------------------------------------
# RUNTIME_VERSION uses a non-PEP-440 scheme ("0.22.0-alpha3e") that PyPI and
# setuptools reject. PEP440_VERSION is a faithful, standards-compliant mapping
# used only for packaging metadata; the canonical language/runtime/spec
# versions above remain the single source of truth and are enforced by
# tests/test_version_sync.py.
import re as _re


def _to_pep440(v: str) -> str:
    """Map the Synapse release line to a PEP 440 public version.

    "0.22.0-alpha3e" -> "0.22.0a3+e"  (alpha 3, patch "e" kept as a local
    version segment so the distribution version stays faithful to the
    canonical RUNTIME_VERSION).
    """
    m = _re.match(r"^(\d+(?:\.\d+)*?)-?alpha(\d+)(.*)$", v)
    if m:
        base, n, rest = m.group(1), m.group(2), m.group(3)
        local = ("+" + rest) if rest else ""
        return f"{base}a{n}{local}"
    return v


PEP440_VERSION = _to_pep440(RUNTIME_VERSION)
