use serde_json::{json, Value};
use std::{
    io::Cursor,
    time::{Duration, Instant},
};
use synapse_desktop::{bridge::Session, protocol::Failure, transport::PipeConnection};

fn connection(hello: Value, ready: Value, reply: Option<Value>) -> PipeConnection {
    let mut bytes = Vec::new();
    for value in [Some(hello), Some(ready), reply].into_iter().flatten() {
        let raw = serde_json::to_vec(&value).unwrap();
        bytes.extend((raw.len() as u32).to_be_bytes());
        bytes.extend(raw);
    }
    PipeConnection::new(Cursor::new(bytes), std::io::sink())
}
fn hello() -> Value {
    json!({"schema_version":"1.0","type":"hello","component":"core","component_version":"v1",
    "protocol_min":1,"protocol_max":1,"profile":"synapse.control.v1","max_frame_bytes":1024})
}
fn ready() -> Value {
    json!({"schema_version":"1.0","type":"ready","profile":"synapse.control.v1",
    "protocol_version":1,"max_frame_bytes":1024,"command_classes":["control"],"domain_ready":false})
}
#[test]
fn bridge_enforces_context_checks_after_schema_validation() {
    let mut wrong = hello();
    wrong["component_version"] = json!("v2");
    assert!(Session::connect(connection(wrong, ready(), None), "v1").is_err());
    let mut wrong = ready();
    wrong["max_frame_bytes"] = json!(2048);
    assert!(Session::connect(connection(hello(), wrong, None), "v1").is_err());
    let forged = json!({"schema_version":"1.0","type":"result","method":"machine.ping",
        "request_id":"unrelated","result":{"status":"alive"}});
    let mut session = Session::connect(connection(hello(), ready(), Some(forged)), "v1").unwrap();
    assert_eq!(
        session.exchange("machine.ping", Instant::now() + Duration::from_secs(1)),
        Err(Failure("PROTOCOL_ERROR"))
    );
    assert_eq!(session.last["type"], "ready");
}
#[test]
fn expired_command_deadline_does_not_become_an_unbounded_wait() {
    let mut session = Session::connect(connection(hello(), ready(), None), "v1").unwrap();
    assert_eq!(
        session.exchange("machine.shutdown", Instant::now() - Duration::from_secs(1)),
        Err(Failure("TRANSPORT_TIMEOUT"))
    );
}
