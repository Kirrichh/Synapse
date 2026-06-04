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
