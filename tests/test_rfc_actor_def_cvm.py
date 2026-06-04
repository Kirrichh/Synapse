"""RFC contract tests for Actor Definition structural CVM wrapper.

These tests validate the architecture contract only. They do not require runtime
implementation and intentionally keep actor messaging out of scope.
"""
from pathlib import Path

RFC_PATH = Path("docs/RFC-ACTOR-DEF-CVM.md")
ROADMAP_PATH = Path("docs/ROADMAP.md")
CHANGELOG_PATH = Path("docs/CHANGELOG.md")


def _rfc_text() -> str:
    return RFC_PATH.read_text(encoding="utf-8")


def test_actor_def_rfc_sections_present():
    text = _rfc_text()
    required_sections = [
        "# RFC: Actor Definition Structural CVM Wrapper",
        "## 1. Motivation and corpus evidence",
        "## 2. Goals",
        "## 3. Non-goals",
        "## 4. Primitive classification",
        "## 5. Structural wrapper shape",
        "## 6. VM state extension",
        "## 7. CallFrame RAII snapshot",
        "## 8. Bridge/runtime parity",
        "## 9. Snapshot and restore invariants",
        "## 10. Corpus coverage target",
        "## 11. Parse-error precondition",
        "## 12. Runtime coverage follow-up",
        "## 13. Deferred messaging RFC",
        "## 14. Acceptance checklist for implementation",
        "## 15. Decision",
    ]
    for section in required_sections:
        assert section in text


def test_actor_def_rfc_uses_corpus_evidence_and_defers_habitstmt():
    text = _rfc_text()
    assert "AgentDef`: 29 fallbacks" in text
    assert "SubAgentDef`: 10 fallbacks" in text
    assert "HabitStmt` appears only 3 times" in text
    assert "HabitStmt or cognitive primitive compilation" in text


def test_actor_def_rfc_preserves_separation_of_concerns():
    text = _rfc_text()
    assert "CVM must not learn actor registry internals" in text
    assert "actor runtime remains authoritative for registry state" in text
    assert "VM_STRUCTURAL_RUNTIME" in text
    assert "not capability-gated in this RFC" in text


def test_actor_messaging_is_explicitly_deferred():
    text = _rfc_text()
    deferred_terms = [
        "SendStmt",
        "ReceiveBlock",
        "ReceivePattern",
        "mailbox matching semantics",
        "actor wake/suspend semantics",
        "capability gates for actor send/receive",
    ]
    for term in deferred_terms:
        assert term in text
    assert "Proceed with **Track 1: Structural Agent Definitions** first" in text
    assert "Do not implement messaging in the same patch" in text


def test_roadmap_and_changelog_reference_actor_def_rfc():
    roadmap = ROADMAP_PATH.read_text(encoding="utf-8")
    changelog = CHANGELOG_PATH.read_text(encoding="utf-8")
    assert "Actor Definition Structural CVM Wrapper RFC" in roadmap
    assert "v2.2.0-alpha3d3-rfc" in changelog
