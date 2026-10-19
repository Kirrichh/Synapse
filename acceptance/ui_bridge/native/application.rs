#![cfg(windows)]
use serde_json::{json, Value};
use std::{path::{Path, PathBuf}, process::{Child, Command, Stdio}};
use synapse_desktop::{application::{Application, ApplicationOperation}, bridge::Session,
    client_identity::ClientIdentity, transport::PipeConnection, protocol::Failure};
use windows_sys::Win32::Security::Credentials::{CredDeleteW, CRED_TYPE_GENERIC};

struct Identity(String);
impl Drop for Identity { fn drop(&mut self) {
    let name: Vec<u16> = self.0.encode_utf16().chain(Some(0)).collect();
    unsafe { CredDeleteW(name.as_ptr(), CRED_TYPE_GENERIC, 0); }
} }
struct Peer(Child);
impl Drop for Peer { fn drop(&mut self) { let _ = self.0.kill(); let _ = self.0.wait(); } }

fn peer(root: &Path, public_key: &str, options: &[&str]) -> (Peer, Session) {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../..").canonicalize().unwrap();
    let python = std::env::var_os("SYNAPSE_ACCEPTANCE_PYTHON").map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("python"));
    let mut child = Peer(Command::new(python).args(["-B", "-m", "acceptance.ui_bridge.domain_peer", "--root"])
        .arg(root).args(["--public-key", public_key]).args(options).current_dir(repo)
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::inherit()).spawn().unwrap());
    let pipe = PipeConnection::new(child.0.stdout.take().unwrap(), child.0.stdin.take().unwrap());
    let session = Session::connect(pipe, "acceptance-peer").unwrap();
    (child, session)
}
fn operation(value: Value) -> ApplicationOperation { serde_json::from_value(value).unwrap() }
fn application(target: &Identity, root: &Path) -> Application {
    Application::new(ClientIdentity::open(&target.0).unwrap(), root.to_owned())
}

#[test]
fn native_intent_survives_lost_owner_reply_and_restart_without_a_renderer_draft() {
    let root = tempfile::tempdir().unwrap();
    let identity = Identity(format!("Synapse/Instance/v1/application-{}", root.path().file_name().unwrap().to_string_lossy()));
    let mut app = application(&identity, &root.path().join("intents"));
    let (child, mut session) = peer(root.path(), &app.public_key(), &["--lose-cancel-reply"]);
    let grants = app.handle(&mut session, ApplicationOperation::Grants {}).unwrap();
    let grant = grants["grants"][0]["grant_id"].as_str().unwrap().to_owned();
    let object = grants["grants"][0]["objects"][0].as_str().unwrap().to_owned();
    let intent = app.handle(&mut session, operation(json!({"action":"issue", "grant_id":grant}))).unwrap()["intent_id"].as_str().unwrap().to_owned();
    assert_eq!(app.handle(&mut session, operation(json!({"action":"recover", "grant_id":grant, "intent_id":intent, "resend_if_absent":true}))).unwrap()["state"], "UNPREPARED");
    let inspected = app.handle(&mut session, operation(json!({"action":"read", "grant_id":grant,
        "method":"invocation.inspect", "params":{"invocation_id":object}}))).unwrap();
    let draft = json!({"method":"invocation.cancel", "method_version":"1", "recipient":object,
        "params":{"invocation_id":object}, "expected_revision":inspected["revision"]});
    let mut forged = draft.clone();
    forged["command_id"] = json!("f1000000-0000-4000-8000-000000000008");
    assert_eq!(app.handle(&mut session, operation(json!({"action":"read", "grant_id":grant,
        "method":"invocation.cancel", "params":{"command":forged}}))), Err(Failure("COMMAND_INVALID")));
    let unchanged = app.handle(&mut session, operation(json!({"action":"read", "grant_id":grant,
        "method":"invocation.inspect", "params":{"invocation_id":object}}))).unwrap();
    assert_eq!(unchanged, inspected);
    assert_eq!(app.handle(&mut session, operation(json!({"action":"submit", "grant_id":grant,
        "intent_id":intent, "draft":draft.to_string()}))), Err(Failure("CORE_EXITED")));
    drop(session); drop(child); drop(app);
    let mut app = application(&identity, &root.path().join("intents"));
    let (child, mut session) = peer(root.path(), &app.public_key(), &[]);
    let pending = app.handle(&mut session, operation(json!({"action":"pending", "grant_id":grant}))).unwrap();
    let command_id = pending["intents"][0]["command_id"].clone();
    let recovered = app.handle(&mut session, operation(json!({"action":"recover", "grant_id":grant,
        "intent_id":intent, "resend_if_absent":true}))).unwrap();
    assert_eq!(recovered["command_id"], command_id);
    assert_eq!(recovered["outcome"], "CANCEL_REQUESTED");
    assert_eq!(recovered["replayed"], true);
    let replayed = app.handle(&mut session, operation(json!({"action":"submit", "grant_id":grant,
        "intent_id":intent, "draft":draft.to_string()}))).unwrap();
    assert_eq!(replayed, recovered);
    let mut changed = draft.clone();
    changed["expected_revision"] = json!("f".repeat(64));
    assert_eq!(app.handle(&mut session, operation(json!({"action":"submit", "grant_id":grant,
        "intent_id":intent, "draft":changed.to_string()}))), Err(Failure("BINDING_CONFLICT")));
    drop(session); drop(child); drop(app);
    let mut app = application(&identity, &root.path().join("intents"));
    let (child, mut session) = peer(root.path(), &app.public_key(), &["--deny-status"]);
    assert_eq!(app.handle(&mut session, operation(json!({"action":"submit", "grant_id":grant,
        "intent_id":intent, "draft":draft.to_string()}))), Err(Failure("ACCESS_DENIED")));
    drop(session); drop(child); drop(app);
    let mut app = application(&identity, &root.path().join("intents"));
    let (_child, mut session) = peer(root.path(), &app.public_key(), &["--revoke"]);
    assert_eq!(app.handle(&mut session, operation(json!({"action":"recover", "grant_id":grant,
        "intent_id":intent, "resend_if_absent":true}))), Err(Failure("ACCESS_DENIED")));
    assert!(root.path().join("intents").read_dir().unwrap().next().is_some());
}

#[test]
fn read_grant_cannot_issue_intents_or_use_the_read_surface_to_cancel() {
    let root = tempfile::tempdir().unwrap();
    let identity = Identity(format!("Synapse/Instance/v1/read-{}", root.path().file_name().unwrap().to_string_lossy()));
    let mut app = application(&identity, &root.path().join("intents"));
    let (_child, mut session) = peer(root.path(), &app.public_key(), &["--read-only"]);
    let grants = app.handle(&mut session, ApplicationOperation::Grants {}).unwrap();
    let grant = grants["grants"][0]["grant_id"].as_str().unwrap();
    assert_eq!(app.handle(&mut session, operation(json!({"action":"issue", "grant_id":grant}))), Err(Failure("ACCESS_DENIED")));
    assert!(!root.path().join("intents").exists());
    let pending = app.handle(&mut session, operation(json!({"action":"pending", "grant_id":grant}))).unwrap();
    assert_eq!(pending["intents"], json!([]));
    assert!(!root.path().join("intents").exists());
    assert_eq!(app.handle(&mut session, operation(json!({"action":"read", "grant_id":grant,
        "method":"invocation.cancel", "params":{}}))), Err(Failure("COMMAND_INVALID")));
    assert_eq!(app.handle(&mut session, operation(json!({"action":"pending",
        "grant_id":"f1000000-0000-4000-8000-000000000007"}))), Err(Failure("ACCESS_DENIED")));
}

#[test]
fn renderer_cannot_supply_principal_path_or_command_identity() {
    for value in [json!({"action":"grants", "principal":"administrator"}),
        json!({"action":"issue", "grant_id":"grant", "root":"untrusted"}),
        json!({"action":"sign", "data":"forged"})] {
        assert!(serde_json::from_value::<ApplicationOperation>(value).is_err());
    }
}

#[test]
fn native_delivery_uses_owner_classes_and_replacement_discards_only_projection_state() {
    let root = tempfile::tempdir().unwrap();
    let identity = Identity(format!("Synapse/Instance/v1/streams-{}", root.path().file_name().unwrap().to_string_lossy()));
    let mut app = application(&identity, &root.path().join("intents"));
    let (_child, mut session) = peer(root.path(), &app.public_key(), &[]);
    let grants = app.handle_for_delivery(&mut session, "first", ApplicationOperation::Grants {}).unwrap();
    let grant = grants["grants"][0]["grant_id"].as_str().unwrap();
    let object = grants["grants"][0]["objects"][0].as_str().unwrap();
    let mut previous = Value::Null;
    for (source, kind) in [("invocation.state", "StateSync"), ("invocation.record", "Evidential"), ("connection.health", "Live")] {
        let confirm = app.handle_for_delivery(&mut session, "first", operation(json!({"action":"read", "grant_id":grant,
            "method":"delivery.subscribe", "params":{"source":source, "invocation_ids":[object]}}))).unwrap();
        assert_eq!(confirm["identity"]["delivery_class"], kind);
        let params = json!({"subscription_id":confirm["identity"]["subscription_id"]});
        if kind == "StateSync" {
            let snapshot = app.handle_for_delivery(&mut session, "first", operation(json!({"action":"delivery", "method":"delivery.snapshot", "params":params}))).unwrap();
            assert_eq!(snapshot["identity"], confirm["identity"]);
            assert!(snapshot["snapshot"]["position"].as_u64().unwrap() >= confirm["deliver_from"].as_u64().unwrap());
        }
        app.handle_for_delivery(&mut session, "first", operation(json!({"action":"delivery", "method":"delivery.collect", "params":params}))).unwrap();
        let frame = app.handle_for_delivery(&mut session, "first", operation(json!({"action":"delivery", "method":"delivery.next", "params":{}}))).unwrap();
        assert_eq!(frame["identity"], confirm["identity"]);
        previous = params;
    }
    assert_eq!(app.handle_for_delivery(&mut session, "replacement", operation(json!({"action":"delivery", "method":"delivery.collect", "params":previous}))), Err(Failure("SUBSCRIPTION_UNKNOWN")));
    assert_eq!(app.handle(&mut session, operation(json!({"action":"delivery", "method":"invocation.cancel", "params":{}}))), Err(Failure("COMMAND_INVALID")));
    assert_eq!(app.handle(&mut session, operation(json!({"action":"delivery", "method":"access.close", "params":{}}))), Err(Failure("COMMAND_INVALID")));
    assert!(!root.path().join("intents").exists());
}

#[test]
fn pending_intents_are_bounded_paged_and_never_expose_saved_drafts() {
    let root=tempfile::tempdir().unwrap();
    let identity=Identity(format!("Synapse/Instance/v1/pages-{}",root.path().file_name().unwrap().to_string_lossy()));
    let mut app=application(&identity,&root.path().join("intents"));
    let (_child,mut session)=peer(root.path(),&app.public_key(),&[]);
    let grants=app.handle(&mut session,ApplicationOperation::Grants {}).unwrap();
    let grant=grants["grants"][0]["grant_id"].as_str().unwrap();
    for _ in 0..33 { app.handle(&mut session,operation(json!({"action":"issue","grant_id":grant}))).unwrap(); }
    let first=app.handle(&mut session,operation(json!({"action":"pending","grant_id":grant}))).unwrap();
    assert_eq!(first["intents"].as_array().unwrap().len(),32);
    assert_eq!(first["next_after"],first["intents"][31]["intent_id"]);
    let second=app.handle(&mut session,operation(json!({"action":"pending","grant_id":grant,"after_intent":first["next_after"]}))).unwrap();
    assert_eq!(second["intents"].as_array().unwrap().len(),1);
    assert!(second["next_after"].is_null());
    assert!(!first["intents"].as_array().unwrap().iter().any(|item|item["intent_id"]==second["intents"][0]["intent_id"]));
    for item in first["intents"].as_array().unwrap() {
        assert_eq!(item.as_object().unwrap().len(),4);
        assert!(item.get("command").is_none());
    }
    assert_eq!(app.handle(&mut session,operation(json!({"action":"pending","grant_id":grant,"after_intent":"f1000000-0000-4000-8000-000000000099"}))),Err(Failure("CURSOR_UNAVAILABLE")));
}
