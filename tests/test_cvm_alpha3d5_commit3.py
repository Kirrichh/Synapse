"""Alpha.3-D5 Commit 3: corpus telemetry + final regression guards."""
from __future__ import annotations

import json
from pathlib import Path

from scripts.corpus_fallback_audit import build_report
from synapse.cvm import compute_message_consumed_id
from synapse.version import LANGUAGE_VERSION, RUNTIME_VERSION, SPEC_VERSION


def test_alpha3d5_corpus_report_hits_actor_messaging_target():
    report_path = Path("reports/corpus_fallback_alpha3e.json")
    assert report_path.exists(), "alpha3e corpus telemetry report must be committed"
    report = json.loads(report_path.read_text())

    assert report["version"] == "2.2.0-alpha3e"
    # P0 parse-fix (alpha3e stabilisation): full_demo.syn, math.syn and
    # memory_demo.syn were previously unparseable (3 files_parse_failed).
    # Fixing the parser adds their AST nodes to the corpus, raising the raw
    # fallback count from 112 -> 132 while coverage improves 90.34% -> 91.44%.
    assert report["total_fallback"] == 103
    assert report["corpus_coverage_ratio"] >= 0.9332
    # All 44 example files must now parse cleanly.
    assert report["files_parse_ok"] == report["files_scanned"]
    assert report["files_parse_failed"] == 0


def test_actor_messaging_nodes_removed_from_fallback_surface():
    report = json.loads(Path("reports/corpus_fallback_alpha3e.json").read_text())
    fallbacks = report["corpus_fallback_by_node_type"]

    assert "SendStmt" not in fallbacks
    assert "ReceiveBlock" not in fallbacks
    assert "ReceivePattern" not in fallbacks


def test_live_corpus_audit_matches_committed_alpha3d5_report():
    committed = json.loads(Path("reports/corpus_fallback_alpha3e.json").read_text())
    live = build_report(["examples", "tests"], base_dir=Path.cwd())

    assert live["version"] == committed["version"]
    assert live["total_fallback"] == committed["total_fallback"]
    assert live["corpus_coverage_ratio"] == committed["corpus_coverage_ratio"]
    assert live["corpus_fallback_by_node_type"] == committed["corpus_fallback_by_node_type"]


def test_message_consumed_id_is_content_addressed_not_positional():
    base = compute_message_consumed_id(
        receiver_id="actor-a",
        msg_type="ping",
        sender_id="actor-b",
        transition_hash="sha256:state",
        event_id="evt-0001",
        payload_hash="sha256:payload-a",
    )
    same = compute_message_consumed_id(
        receiver_id="actor-a",
        msg_type="ping",
        sender_id="actor-b",
        transition_hash="sha256:state",
        event_id="evt-0001",
        payload_hash="sha256:payload-a",
    )
    different_payload = compute_message_consumed_id(
        receiver_id="actor-a",
        msg_type="ping",
        sender_id="actor-b",
        transition_hash="sha256:state",
        event_id="evt-0001",
        payload_hash="sha256:payload-b",
    )
    different_event = compute_message_consumed_id(
        receiver_id="actor-a",
        msg_type="ping",
        sender_id="actor-b",
        transition_hash="sha256:state",
        event_id="evt-0002",
        payload_hash="sha256:payload-a",
    )

    assert base == same
    assert base.startswith("mc-")
    assert base != different_payload
    assert base != different_event


def test_alpha3d5_version_bump_is_consistent():
    assert LANGUAGE_VERSION == "2.2.0-alpha3e"
    assert RUNTIME_VERSION == "0.22.0-alpha3e"
    assert SPEC_VERSION == "2.2.0-alpha3e"
