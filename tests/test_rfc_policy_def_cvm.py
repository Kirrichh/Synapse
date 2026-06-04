from pathlib import Path

RFC = Path('docs/RFC-POLICY-DEF-CVM.md')


def _text():
    return RFC.read_text(encoding='utf-8')


def test_policy_rfc_exists_and_status_implemented():
    text = _text()
    assert 'STATUS: IMPLEMENTED in alpha3d4' in text
    assert 'PolicyDef Structural Wrapper' in text


def test_policy_rfc_has_required_structural_sections():
    text = _text()
    for heading in [
        'Goals and Non-Goals',
        'PolicyDef as Structural Runtime Primitive',
        'PolicyRule Wrapper Semantics',
        'VMState.policy_stack',
        'CallFrame.policy_stack_snapshot',
        'Bridge Dispatch Contract',
        'Governance Runtime Parity',
        'Snapshot / Restore Invariants',
        'Exception and RETURN Unwind',
        'Coverage Target',
    ]:
        assert heading in text


def test_policy_rfc_forbids_governance_semantics_in_cvm():
    text = _text()
    assert 'No specialized `EVAL_POLICY_RULE` opcode exists' in text
    assert 'not capability-gated' in text
    assert 'never implemented as CVM-native governance logic' in text


def test_policy_rfc_lists_all_structural_symbols():
    text = _text()
    for symbol in ['SYS_POLICY_ENTER', 'SYS_POLICY_EXIT', 'SYS_POLICY_RULE_ENTER', 'SYS_POLICY_RULE_EXIT']:
        assert symbol in text


def test_policy_rfc_documents_hard_boundaries():
    text = _text()
    assert 'D4 does not add messaging' in text
    assert 'async/promise semantics' in text
    assert 'policy enforcement' in text
