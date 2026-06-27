"""Stage 3A raw baseline carry tests."""

from __future__ import annotations

from synapse.experiments.swebench.carry import RawCarryEntry, RawTranscriptCarry


def test_failed_oracle_output_is_appended_and_rendered_raw():
    carry = RawTranscriptCarry().append(
        RawCarryEntry(
            attempt_id=1,
            worker_summary="patched candidate",
            oracle_stdout="assertion failed",
            oracle_stderr="traceback",
            diagnostics=("scope ok",),
        )
    )

    rendered = carry.render_prompt_suffix()

    assert len(carry.entries) == 1
    assert "RAW BASELINE RETRY CONTEXT" in rendered
    assert "assertion failed" in rendered
    assert "traceback" in rendered
    assert "scope ok" in rendered


def test_carry_grows_without_distilled_evidence_objects():
    carry = RawTranscriptCarry()
    carry = carry.append(RawCarryEntry(1, "one", "stdout1", "stderr1", ("d1",)))
    carry = carry.append(RawCarryEntry(2, "two", "stdout2", "stderr2", ("d2",)))

    rendered = carry.render_prompt_suffix()

    assert len(carry.entries) == 2
    assert "previous attempt 1" in rendered
    assert "previous attempt 2" in rendered
    assert "DistilledEvidence" not in rendered
    assert not hasattr(carry, "distilled_evidence")
