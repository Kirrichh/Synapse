use serde_json::json;
use synapse_desktop::{domain_protocol as domain, protocol::{self, Failure}};

#[test]
fn owner_schema_and_correlation_are_enforced_at_the_native_pipe() {
    let request = domain::request("access.open", json!({"profile":"synapse.application/v1"}), None).unwrap();
    let reply = json!({"schema_version":domain::VERSION, "type":"result", "request_id":request["request_id"],
        "method":"access.open", "result":{"profile":"synapse.application/v1", "challenge":{
            "schema_version":"synapse.access.challenge/v1", "installation":"f1000000-0000-4000-8000-000000000001",
            "connection":"f1000000-0000-4000-8000-000000000002", "nonce":"a".repeat(64)}}});
    assert!(protocol::decode(&serde_json::to_vec(&reply).unwrap()).is_ok());
    assert_eq!(domain::result(&request, reply.clone()).unwrap().unwrap(), reply["result"]);
    let mut changed = reply.clone();
    changed["request_id"] = json!("f1000000-0000-4000-8000-000000000003");
    assert_eq!(domain::result(&request, changed), Err(Failure("PROTOCOL_ERROR")));
    for field in ["nonce", "installation", "connection"] {
        let mut changed = reply.clone();
        changed["result"]["challenge"][field] = json!("invalid");
        assert!(protocol::decode(&serde_json::to_vec(&changed).unwrap()).is_err());
        assert_eq!(domain::result(&request, changed), Err(Failure("PROTOCOL_ERROR")));
    }
    let command = json!({"command_id":"f1000000-0000-4000-8000-000000000004", "method":"invocation.cancel",
        "method_version":"1", "recipient":"invocation-one", "expected_revision":"b".repeat(64),
        "params":{"invocation_id":"invocation-one"}});
    let grant = Some(("f1000000-0000-4000-8000-000000000005", 1));
    assert!(domain::request("invocation.cancel", json!({"command":command}), grant).is_ok());
    let mut bad = command;
    bad["params_hash"] = json!("a".repeat(64));
    assert!(domain::request("invocation.cancel", json!({"command":bad}), grant).is_err());
    assert!(domain::request("invocation.future", json!({}), grant).is_err());
    assert!(domain::request("access.authenticate", json!({"application_id":"forged", "signature":"0".repeat(128)}), None).is_err());
}

#[test]
fn refusal_is_correlated_and_never_an_effect_receipt() {
    let request = domain::request("access.grants", json!({}), None).unwrap();
    let refusal = json!({"schema_version":domain::VERSION,"type":"error",
        "request_id":request["request_id"],"code":"ACCESS_DENIED"});
    assert_eq!(domain::result(&request, refusal.clone()), Ok(Err("ACCESS_DENIED")));
    let mut forged = refusal;
    forged["code"] = json!("FUTURE_ERROR");
    assert!(domain::result(&request, forged).is_err());
    let other = domain::request("access.open", json!({"profile":"synapse.application/v1"}), None).unwrap();
    let reply = json!({"schema_version":domain::VERSION,"type":"result", "method":"access.grants",
        "request_id":other["request_id"], "result":{"grants":[]}});
    assert_eq!(domain::result(&other, reply), Err(Failure("PROTOCOL_ERROR")));
}
