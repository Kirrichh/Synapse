use serde_json::{json, Value};
use synapse_desktop::protocol::{self, Failure};

#[test]
fn shared_raw_corpus_preserves_or_refuses_every_message() {
    let cases: Vec<Value> = serde_json::from_str(include_str!("../corpus.json")).unwrap();
    for case in cases {
        let result = protocol::decode(case["raw"].as_str().unwrap().as_bytes());
        if case["valid"] == true {
            assert_eq!(result.unwrap(), case["value"], "{}", case["behavior"]);
        } else {
            assert!(result.is_err(), "{}", case["behavior"]);
        }
    }
}

fn announcement() -> Value {
    json!({"schema_version":"1.0", "type":"hello", "component":"core", "component_version":"v1",
        "protocol_min":1,"protocol_max":1,"max_frame_bytes":1024,"profile":"synapse.control.v1"})
}
#[test]
fn negotiation_binds_role_version_profile_and_limit() {
    assert_eq!(protocol::check_hello(&announcement(), "v1"), Ok(1024));
    for (key, value) in [
        ("component", json!("shell")),
        ("component_version", json!("v2")),
        ("protocol_min", json!(2)),
        ("protocol_max", json!(0)),
        ("profile", json!("other")),
        ("max_frame_bytes", json!(1023)),
        ("max_frame_bytes", json!(65537)),
    ] {
        let mut peer = announcement();
        peer[key] = value;
        assert!(protocol::check_hello(&peer, "v1").is_err(), "{key}");
    }
    let ready = json!({"type":"ready","profile":"synapse.control.v1","protocol_version":1,
        "max_frame_bytes":1024,"command_classes":["control"],"domain_ready":false});
    assert_eq!(protocol::check_ready(&ready, 1024), Ok(()));
    for (key, value) in [
        ("type", json!("hello")),
        ("profile", json!("other")),
        ("protocol_version", json!(2)),
        ("max_frame_bytes", json!(2048)),
        ("command_classes", json!(["control", "domain"])),
        ("domain_ready", json!(true)),
    ] {
        let mut forged = ready.clone();
        forged[key] = value;
        assert!(protocol::check_ready(&forged, 1024).is_err(), "{key}");
    }
}

#[test]
fn reply_cannot_acknowledge_a_different_request_or_method() {
    let reply = json!({"type":"result","method":"machine.ping","request_id":"r1"});
    assert_eq!(protocol::check_result(&reply, "machine.ping", "r1"), Ok(()));
    assert_eq!(
        protocol::check_result(&reply, "machine.shutdown", "r1"),
        Err(Failure("PROTOCOL_ERROR"))
    );
    assert_eq!(
        protocol::check_result(&reply, "machine.ping", "r2"),
        Err(Failure("PROTOCOL_ERROR"))
    );
    assert!(protocol::check_result(
        &json!({"type":"error","method":"machine.ping","request_id":"r1"}),
        "machine.ping",
        "r1"
    )
    .is_err());
    for message in [
        protocol::hello("v1"),
        protocol::select(1024),
        protocol::request("machine.ping", "r1"),
    ] {
        assert_eq!(
            protocol::decode(&serde_json::to_vec(&message).unwrap()).unwrap(),
            message
        );
    }
}
