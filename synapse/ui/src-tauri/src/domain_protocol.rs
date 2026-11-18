//! Closed application envelopes with the same owner contracts as the core.
use crate::{commands, command_recovery, protocol::Failure};
use serde_json::{json, Value};
use std::{collections::HashSet, sync::LazyLock};

pub const VERSION: &str = "synapse.machine.domain/v1";
pub const SCHEMA: &str = include_str!("../../../machine/schemas/domain.schema.json");
pub fn build_validator(source: &str) -> jsonschema::Validator {
    let mut registry = jsonschema::Registry::new();
    for raw in [commands::SCHEMA, command_recovery::SCHEMA, SCHEMA] {
        let schema: Value = serde_json::from_str(raw).expect("owner schema");
        let id = schema["$id"].as_str().expect("owner schema identity").to_owned();
        registry = registry.add(&id, schema).expect("local owner schema");
    }
    let registry = registry.prepare().expect("local schema registry");
    jsonschema::options().should_validate_formats(true).with_registry(&registry)
        .build(&serde_json::from_str(source).expect("application schema"))
        .expect("application validator")
}
static VALIDATOR: LazyLock<jsonschema::Validator> = LazyLock::new(|| build_validator(SCHEMA));
static ERRORS: LazyLock<HashSet<String>> = LazyLock::new(|| {
    let schema: Value = serde_json::from_str(SCHEMA).expect("application schema");
    schema["$defs"]["error"]["enum"].as_array().expect("error vocabulary")
        .iter().map(|value| value.as_str().expect("error code").to_owned()).collect()
});

pub fn valid(value: &Value) -> bool { VALIDATOR.is_valid(value) }

pub fn request(method: &str, params: Value, grant: Option<(&str, u64)>) -> Result<Value, Failure> {
    let mut value = json!({"schema_version":VERSION, "type":"request",
        "request_id":uuid::Uuid::new_v4().to_string(), "method":method, "params":params});
    if let Some((id, version)) = grant {
        value["grant_id"] = json!(id);
        value["grant_version"] = json!(version);
    }
    if !valid(&value) { return Err(Failure("COMMAND_INVALID")); }
    Ok(value)
}

/// A correlated refusal is distinct from transport loss. Neither is permission
/// to invent a new command or silently retry an unresolved effect.
pub fn result(request: &Value, reply: Value) -> Result<Result<Value, &'static str>, Failure> {
    if !valid(&reply) || reply["request_id"] != request["request_id"] {
        return Err(Failure("PROTOCOL_ERROR"));
    }
    if reply["type"] == "error" {
        let code = ERRORS.get(reply["code"].as_str().ok_or(Failure("PROTOCOL_ERROR"))?)
            .ok_or(Failure("PROTOCOL_ERROR"))?;
        return Ok(Err(code.as_str()));
    }
    if reply["type"] != "result" || reply["method"] != request["method"] {
        return Err(Failure("PROTOCOL_ERROR"));
    }
    Ok(Ok(reply["result"].clone()))
}
