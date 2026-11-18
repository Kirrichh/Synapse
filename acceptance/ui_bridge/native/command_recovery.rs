//! Windows acceptance uses the production per-user protection boundary.
#![cfg(windows)]
use serde_json::{json, Value};
use synapse_desktop::{command_recovery::{self, RecoveryDecision}, commands::{self, Scope},
    intent_store::{IntentStore, PreparedCommand}, protected_data::UserProtectedData};

fn scope() -> Scope { Scope { principal:"operator".into(), workspace:"workspace-one".into() } }
fn draft() -> String {
    json!({"method":"invocation.cancel", "method_version":"1", "recipient":"invocation-one",
        "expected_revision":"a".repeat(64), "params":{"invocation_id":"invocation-one"}}).to_string()
}
fn absent(prepared: &PreparedCommand) -> Value {
    json!({"schema_version":"synapse.command.status/v1", "state":"ABSENT",
        "command_id":prepared.command()["command_id"], "principal":"operator",
        "workspace":"workspace-one", "owner":"agent.invocations", "recipient":"invocation-one"})
}
fn terminal(prepared: &PreparedCommand) -> Value {
    let binding = commands::binding(&scope(), prepared.command()).unwrap();
    json!({"schema_version":"synapse.invocation.cancel-result/v1", "state":"COMPLETED",
        "command_id":prepared.command()["command_id"], "binding_sha256":prepared.binding_digest(),
        "params_hash":binding["params_hash"], "invocation_id":"invocation-one",
        "outcome":"CANCEL_REQUESTED", "replayed":true})
}

#[test]
fn protected_restart_and_scoped_absence_preserve_the_exact_command() {
    let temp = tempfile::tempdir().unwrap();
    let mut store = IntentStore::open(temp.path(), scope(), UserProtectedData, &[]).unwrap();
    let id = store.issue().unwrap();
    assert!(store.recover(&id).unwrap().is_none());
    let first = store.prepare(&id, &draft()).unwrap();
    let command = first.command().clone();
    let binding = first.binding_digest().to_owned();
    // Independently serialized fixed contract vector, shared with Python owner
    // acceptance. Native-generated command identity is not part of the body.
    assert_eq!(binding, "2cff17b5eae19adc6869554c528581e186a4fc908357c16a58a1bc4f9387bdf0");
    drop(store);
    let reopened = IntentStore::open(temp.path(), scope(), UserProtectedData, &[]).unwrap();
    let before = std::fs::read(temp.path().join(format!("{id}.intent"))).unwrap();
    let restored = reopened.recover(&id).unwrap().unwrap();
    assert_eq!(std::fs::read(temp.path().join(format!("{id}.intent"))).unwrap(), before);
    assert_eq!(command_recovery::status_request(&restored), json!({
        "command_id":command["command_id"], "owner":"agent.invocations", "recipient":"invocation-one"}));
    let answer = absent(&restored);
    match command_recovery::reconcile(restored, answer.to_string().as_bytes()).unwrap() {
        RecoveryDecision::Resend(retry) => {
            assert_eq!(retry.intent_id(), id);
            assert_eq!(retry.command(), &command);
            assert_eq!(retry.binding_digest(), binding);
        }
        _ => panic!("scoped absence must retain the original command"),
    }
    assert_eq!(reopened.pending().unwrap().len(), 1);
}

#[test]
fn foreign_or_incomplete_absence_never_permits_resending() {
    let temp = tempfile::tempdir().unwrap();
    let mut store = IntentStore::open(temp.path(), scope(), UserProtectedData, &[]).unwrap();
    let id = store.issue().unwrap();
    for key in ["schema_version", "command_id", "owner", "principal", "workspace", "recipient"] {
        let prepared = store.prepare(&id, &draft()).unwrap();
        let mut reply = absent(&prepared); reply[key] = json!("foreign");
        assert!(command_recovery::reconcile(prepared, reply.to_string().as_bytes()).is_err(), "{key}");
    }
    for raw in [b"{}".as_slice(), br#"{"state":"ABSENT"}"#, br#"{"state":"EXECUTION_STATE_UNKNOWN"}"#,
        br#"{"state":"ABSENT","state":"COMPLETED"}"#, b"", b"not JSON"] {
        assert!(command_recovery::reconcile(store.prepare(&id, &draft()).unwrap(), raw).is_err());
    }
    let prepared = store.prepare(&id, &draft()).unwrap();
    let mut reply = absent(&prepared); reply["ignored_constraint"] = json!(true);
    assert!(command_recovery::reconcile(prepared, reply.to_string().as_bytes()).is_err());
    assert_eq!(store.pending().unwrap().len(), 1);
}

#[test]
fn historical_receipt_is_replayed_and_never_becomes_a_retry() {
    let temp = tempfile::tempdir().unwrap();
    let mut store = IntentStore::open(temp.path(), scope(), UserProtectedData, &[]).unwrap();
    let id = store.issue().unwrap();
    for outcome in ["CANCEL_REQUESTED", "ALREADY_TERMINAL"] {
        let prepared = store.prepare(&id, &draft()).unwrap();
        let mut receipt = terminal(&prepared); receipt["outcome"] = json!(outcome);
        match command_recovery::reconcile(prepared, receipt.to_string().as_bytes()).unwrap() {
            RecoveryDecision::Completed(result) => assert_eq!(result, receipt),
            _ => panic!("historical receipt must not resend a command"),
        }
    }
}

#[test]
fn malformed_or_substituted_receipts_leave_the_pending_intent_intact() {
    let temp = tempfile::tempdir().unwrap();
    let mut store = IntentStore::open(temp.path(), scope(), UserProtectedData, &[]).unwrap();
    let id = store.issue().unwrap();
    let command = store.prepare(&id, &draft()).unwrap().command().clone();
    for (key, value) in [("schema_version",json!("future")), ("state",json!("ACCEPTED")),
        ("command_id",json!("foreign")), ("binding_sha256",json!("f".repeat(64))),
        ("params_hash",json!("f".repeat(64))), ("invocation_id",json!("foreign")),
        ("replayed",json!(false)), ("outcome",json!("PROCESS_STOPPED")), ("extra",json!(true))] {
        let prepared = store.prepare(&id, &draft()).unwrap();
        let mut reply = terminal(&prepared); reply[key] = value;
        assert!(command_recovery::reconcile(prepared, reply.to_string().as_bytes()).is_err(), "{key}");
    }
    assert_eq!(store.pending().unwrap()[0].command.as_ref(), Some(&command));
}
