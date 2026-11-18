use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use synapse_desktop::{jcs, protocol};

#[test]
fn shared_command_corpus_has_exact_canonical_bytes_and_refusals() {
    let cases: Vec<Value> = serde_json::from_str(include_str!("../jcs-corpus.json")).unwrap();
    for case in cases {
        let result = jcs::decode(case["raw"].as_str().unwrap().as_bytes());
        if let Some(expected) = case["canonical"].as_str() {
            let value = result.unwrap_or_else(|_| panic!("{}", case["case"]));
            assert_eq!(jcs::canonical_bytes(&value).unwrap(), expected.as_bytes(), "{}", case["case"]);
            assert_eq!(jcs::canonical_bytes(&jcs::decode(expected.as_bytes()).unwrap()).unwrap(), expected.as_bytes());
            if value.is_object() {
                assert_eq!(jcs::parameter_hash(&value).unwrap(), format!("{:x}", Sha256::digest(expected.as_bytes())));
            }
        } else {
            assert!(result.is_err(), "{}", case["case"]);
        }
    }
}

#[test]
fn identities_preserve_significance_and_bounds_precede_use() {
    assert_eq!(jcs::parameter_hash(&jcs::decode(br#"{"a":1.0,"b":[1,2]}"#).unwrap()).unwrap(),
        jcs::parameter_hash(&jcs::decode(br#"{"b":[1,2],"a":1e0}"#).unwrap()).unwrap());
    assert_ne!(jcs::parameter_hash(&json!({"a":1,"b":[1,2]})).unwrap(),
        jcs::parameter_hash(&json!({"a":1,"b":[2,1]})).unwrap());
    assert_ne!(jcs::parameter_hash(&json!({"text":"e\u{301}"})).unwrap(),
        jcs::parameter_hash(&json!({"text":"\u{e9}"})).unwrap());
    for value in [json!(9007199254740993u64), json!(9007199254740992u64), json!("x".repeat(65536))] {
        assert!(jcs::canonical_bytes(&value).is_err());
    }
    assert!(jcs::parameter_hash(&json!([])).is_err());
    for raw in [Vec::new(), b"\xff".to_vec(), vec![b' ';65537],
        format!("{}0{}", "[".repeat(33), "]".repeat(33)).into_bytes()] {
        assert!(jcs::decode(&raw).is_err());
    }
    let raw = format!("{}0{}", "[".repeat(32), "]".repeat(32));
    assert_eq!(jcs::canonical_bytes(&jcs::decode(raw.as_bytes()).unwrap()).unwrap(), raw.as_bytes());
    for raw in ["-0", "1.0", "1e0"] {
        assert!(protocol::decode(raw.as_bytes()).is_err());
        assert!(jcs::decode(raw.as_bytes()).is_ok());
    }
}
