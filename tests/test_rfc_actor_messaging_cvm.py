"""RFC contract tests for Alpha.3-D5 Actor Messaging in CVM.

These tests validate the architectural contract only. They deliberately do not
assert runtime implementation behavior. D5 implementation must satisfy this RFC
before adding messaging opcodes.
"""
from pathlib import Path

RFC_PATH = Path("docs/RFC-ACTOR-MESSAGING-CVM.md")


def _text() -> str:
    return RFC_PATH.read_text(encoding="utf-8")


def test_actor_messaging_rfc_exists_and_has_required_sections():
    text = _text()
    required = [
        "# RFC: Actor Messaging in CVM (Alpha.3-D5)",
        "## 1. Goals and Non-Goals",
        "## 2. Syntax Mapping: Current Grammar Is Preserved",
        "## 3. VM State Extensions",
        "## 4. Opcodes",
        "## 5. Bridge Dispatch and Actor Runtime Authority",
        "## 6. FIFO Replay and Message Consumption Identity",
        "## 7. Security Gates",
        "## 8. Snapshot and Restore",
        "## 9. Red Lines and Review Blockers",
        "## 10. Corpus Target and Acceptance Checklist",
    ]
    for heading in required:
        assert heading in text


def test_actor_messaging_rfc_preserves_current_receive_syntax():
    text = _text()
    assert "Variant A" in text
    assert "preserves the current parser" in text
    assert "ReceivePattern(sender_var=\"sender\", target_var=\"payload\"" in text
    assert "SendStmt.method` becomes `msg_type`" in text
    assert "ReceivePattern.sender_var` binds" in text
    assert "ReceivePattern.target_var` binds the complete message dictionary" in text
    assert "D5 does **not** add case-style header grammar" in text


def test_actor_messaging_rfc_separates_message_pause_from_host_call_pause():
    text = _text()
    assert "STATUS_PAUSED_MESSAGING" in text
    assert "pending_message_receive" in text
    assert "pending_host_call remains `None`" in text or "pending_host_call`," in text
    assert "pending_message_receive` must never be stored in `pending_host_call`" in text
    assert "BridgePromise may be reused" in text


def test_actor_messaging_rfc_defines_mailbox_authority_boundary():
    text = _text()
    assert "snapshot views" in text
    assert "actor_runtime remains the canonical authority" in text
    assert "VMBridge is the synchronization boundary" in text or "VMBridge                  = synchronization" in text
    assert "CognitiveVM` directly reading `interpreter.mailboxes`" in text
    assert "CognitiveVM` directly mutating `actor_runtime`" in text


def test_actor_messaging_rfc_requires_content_addressed_replay_identity():
    text = _text()
    assert "compute_message_consumed_id" in text
    assert "message_consumed_id" in text
    assert "payload_hash" in text
    assert "Replay lookup must use `message_consumed_id`" in text
    assert "It must not use" in text
    assert "execution-history list index" in text
    assert "Live queue fallback during replay is forbidden" in text


def test_actor_messaging_rfc_lists_red_lines_and_defers_llm():
    text = _text()
    blockers = [
        "MSG_UNPACK",
        "MSG_MATCH_PAYLOAD",
        "MSG_BIND_FIELD",
        "Reuse of `STATUS_PAUSED_HOST_CALL`",
        "Direct CVM access",
        "Positional replay",
        "New parser grammar",
        "LLMCall",
        "PromptExpr",
    ]
    for blocker in blockers:
        assert blocker in text
    assert "deferred to D6" in text


def test_actor_messaging_rfc_declares_corpus_targets():
    text = _text()
    assert "SendStmt" in text
    assert "ReceiveBlock" in text
    assert "ReceivePattern" in text
    assert "total_fallback: 135 -> 112" in text
    assert "corpus_coverage: ~0.9034" in text
