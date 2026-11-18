use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{fs, sync::{Arc, atomic::{AtomicBool, Ordering}}};
use synapse_desktop::{commands::{self, Scope}, intent_store::IntentStore,
    protected_data::{Protector, UserProtectedData}, protocol::Failure};

// Acceptance-only storage cipher isolates interruption/refusal behavior. The
// independent Windows case below exercises the actual production DPAPI owner.
struct AcceptanceProtector { fail: Arc<AtomicBool> }
impl Protector for AcceptanceProtector {
    fn protect(&self, clear: &[u8]) -> Result<Vec<u8>, Failure> {
        if self.fail.load(Ordering::SeqCst) { return Err(Failure("PROTECTED_STORAGE_FAILED")); }
        let mut result = Sha256::digest(clear).to_vec();
        result.extend(clear.iter().map(|b| b ^ 0xa5));
        Ok(result)
    }
    fn unprotect(&self, cipher: &[u8]) -> Result<Vec<u8>, Failure> {
        if cipher.len() < 32 { return Err(Failure("PROTECTED_STORAGE_FAILED")); }
        let clear: Vec<u8> = cipher[32..].iter().map(|b| b ^ 0xa5).collect();
        if Sha256::digest(&clear)[..] != cipher[..32] { return Err(Failure("PROTECTED_STORAGE_FAILED")); }
        Ok(clear)
    }
}
fn scope() -> Scope { Scope { principal: "operator".into(), workspace: "workspace-one".into() } }
fn draft() -> Value {
    json!({"method":"invocation.cancel","method_version":"1","recipient":"synthetic-private-invocation",
        "expected_revision":"a".repeat(64),"params":{"invocation_id":"synthetic-private-invocation"}})
}
fn protector(fail: &Arc<AtomicBool>) -> AcceptanceProtector { AcceptanceProtector { fail: fail.clone() } }

#[test]
fn restart_preserves_identity_and_new_intent_is_deliberately_distinct() {
    let temp = tempfile::tempdir().unwrap();
    let fail = Arc::new(AtomicBool::new(false));
    let mut store = IntentStore::open(temp.path(), scope(), protector(&fail), &[]).unwrap();
    let intent = store.issue().unwrap();
    let prepared = store.prepare(&intent, &draft().to_string()).unwrap();
    let command = prepared.command().clone();
    let digest = prepared.binding_digest().to_owned();
    assert_eq!(prepared.intent_id(), intent);
    assert!(IntentStore::open(temp.path(), scope(), protector(&fail), &[]).is_err());
    drop(store);
    let mut store = IntentStore::open(temp.path(), scope(), protector(&fail), &[]).unwrap();
    assert_eq!(store.pending().unwrap()[0].command, Some(command.clone()));
    let retry = store.prepare(&intent, &draft().to_string()).unwrap();
    assert_eq!(retry.command(), &command);
    assert_eq!(retry.binding_digest(), digest);
    let new_intent = store.issue().unwrap();
    assert_ne!(new_intent, intent);
    assert_ne!(store.prepare(&new_intent, &draft().to_string()).unwrap().command()["command_id"], command["command_id"]);
    let mut changed = draft(); changed["expected_revision"] = json!("b".repeat(64));
    assert_eq!(store.prepare(&intent, &changed.to_string()).err(), Some(Failure("BINDING_CONFLICT")));
    let mut injected = draft(); injected["command_id"] = json!("11111111-1111-4111-8111-111111111111");
    assert_eq!(store.prepare(&new_intent, &injected.to_string()).err(), Some(Failure("COMMAND_INVALID")));
    assert_eq!(store.prepare("11111111-1111-4111-8111-111111111111", &draft().to_string()).err(), Some(Failure("INTENT_UNKNOWN")));
    for item in fs::read_dir(temp.path()).unwrap() {
        let path = item.unwrap().path();
        if path.extension().is_none_or(|extension| extension != "intent") { continue; }
        let bytes = fs::read(path).unwrap();
        assert!(!bytes.windows(b"synthetic-private".len()).any(|window| window == b"synthetic-private"));
    }
}

#[test]
fn storage_failure_never_returns_sendable_content_or_forgets_an_issued_id() {
    let temp = tempfile::tempdir().unwrap();
    let fail = Arc::new(AtomicBool::new(true));
    let mut store = IntentStore::open(temp.path(), scope(), protector(&fail), &[]).unwrap();
    assert_eq!(store.issue().err(), Some(Failure("PROTECTED_STORAGE_FAILED")));
    assert!(store.pending().unwrap().is_empty());
    fail.store(false, Ordering::SeqCst);
    let id = store.issue().unwrap();
    let command_id = store.pending().unwrap()[0].command_id.clone();
    fail.store(true, Ordering::SeqCst);
    assert_eq!(store.prepare(&id, &draft().to_string()).err(), Some(Failure("PROTECTED_STORAGE_FAILED")));
    assert!(store.pending().unwrap()[0].command.is_none());
    fail.store(false, Ordering::SeqCst);
    assert_eq!(store.prepare(&id, &draft().to_string()).unwrap().command()["command_id"], command_id);
}

#[test]
fn changed_scope_corrupt_records_and_agent_workspaces_fail_closed() {
    let temp = tempfile::tempdir().unwrap();
    let fail = Arc::new(AtomicBool::new(false));
    assert!(IntentStore::open(&temp.path().join("agent-data"), scope(), protector(&fail), &[temp.path().to_owned()]).is_err());
    assert!(!temp.path().join("agent-data").exists());
    let mut store = IntentStore::open(temp.path(), scope(), protector(&fail), &[]).unwrap();
    let id = store.issue().unwrap();
    drop(store);
    let foreign = Scope { workspace: "foreign".into(), ..scope() };
    let mut store = IntentStore::open(temp.path(), foreign, protector(&fail), &[]).unwrap();
    assert_eq!(store.prepare(&id, &draft().to_string()).err(), Some(Failure("SCOPE_REFUSED")));
    drop(store);
    fs::write(temp.path().join(format!("{id}.intent")), b"corrupt").unwrap();
    let mut store = IntentStore::open(temp.path(), scope(), protector(&fail), &[]).unwrap();
    assert_eq!(store.prepare(&id, &draft().to_string()).err(), Some(Failure("PROTECTED_STORAGE_FAILED")));
    assert!(store.issue().is_err());
    assert_eq!(fs::read(temp.path().join(format!("{id}.intent"))).unwrap(), b"corrupt");
}

#[test]
fn native_binding_covers_owner_scope_revision_and_rejects_extensions() {
    let mut command = draft(); command["command_id"] = json!("11111111-1111-4111-8111-111111111111");
    let expected = commands::binding(&scope(), &command).unwrap();
    assert_eq!(expected["owner"], "agent.invocations");
    assert_eq!(expected["params_hash"], format!("{:x}", Sha256::digest(br#"{"invocation_id":"synthetic-private-invocation"}"#)));
    for (key, value) in [("expected_revision", json!("b".repeat(64))), ("params", json!({"invocation_id":"other"}))] {
        let mut changed = command.clone(); changed[key] = value;
        assert!(commands::binding(&scope(), &changed).is_err() || commands::binding(&scope(), &changed).unwrap() != expected);
    }
    for (key, value) in [("method",json!("execute")),("method_version",json!("2")),
        ("principal",json!("operator")),("params_hash",json!("f".repeat(64))),
        ("command_id",json!("not-a-uuid"))] {
        let mut changed = command.clone(); changed[key] = value;
        assert!(commands::binding(&scope(), &changed).is_err());
    }
    assert_ne!(commands::binding(&Scope { principal:"foreign".into(), ..scope() }, &command).unwrap(), expected);
    assert_ne!(commands::binding(&Scope { workspace:"foreign".into(), ..scope() }, &command).unwrap(), expected);
    for suffix in ["\n", "\r", "\r\n", "\u{2028}", "\0"] {
        let mut changed = command.clone();
        changed["recipient"] = json!(format!("invocation-one{suffix}"));
        changed["params"]["invocation_id"] = changed["recipient"].clone();
        assert_eq!(commands::binding(&scope(), &changed).err(), Some(Failure("COMMAND_INVALID")));
        changed = command.clone(); changed["expected_revision"] = json!(format!("{}{suffix}", "a".repeat(64)));
        assert_eq!(commands::binding(&scope(), &changed).err(), Some(Failure("COMMAND_INVALID")));
    }
}

#[cfg(windows)]
#[test]
fn production_windows_protection_roundtrips_and_authenticates() {
    let clear = b"synthetic-private-command";
    let cipher = UserProtectedData.protect(clear).unwrap();
    assert!(!cipher.windows(clear.len()).any(|window| window == clear));
    assert_eq!(UserProtectedData.unprotect(&cipher).unwrap(), clear);
    let mut changed = cipher;
    let end = changed.len() - 1;
    changed[end] ^= 1;
    assert_eq!(UserProtectedData.unprotect(&changed).err(), Some(Failure("PROTECTED_STORAGE_FAILED")));
}
