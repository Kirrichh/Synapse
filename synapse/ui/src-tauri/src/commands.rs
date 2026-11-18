//! Trusted command binding. The Python owner independently computes this binding.
use crate::{jcs, protocol::Failure};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::sync::LazyLock;

pub const SCHEMA: &str = include_str!("../../../commands/binding.schema.json");
static VALIDATOR: LazyLock<jsonschema::Validator> = LazyLock::new(|| {
    jsonschema::options().should_validate_formats(true)
        .build(&serde_json::from_str(SCHEMA).expect("command schema")).expect("command validator")
});

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct Scope {
    pub principal: String,
    pub workspace: String,
}
impl Scope {
    pub fn validate(&self) -> Result<(), Failure> {
        for value in [&self.principal, &self.workspace] {
            if value.is_empty() || value.chars().count() > 256 { return Err(Failure("SCOPE_REFUSED")); }
        }
        Ok(())
    }
}

pub fn binding(scope: &Scope, command: &Value) -> Result<Value, Failure> {
    scope.validate()?;
    jcs::canonical_bytes(command).map_err(|_| Failure("COMMAND_INVALID"))?;
    if !VALIDATOR.is_valid(command) { return Err(Failure("COMMAND_INVALID")); }
    if command["recipient"] != command["params"]["invocation_id"] {
        return Err(Failure("BINDING_CONFLICT"));
    }
    Ok(json!({"schema_version":"synapse.command.binding/v1",
        "principal":scope.principal,"workspace":scope.workspace,
        "method":command["method"],"method_version":command["method_version"],
        "owner":"agent.invocations","recipient":command["recipient"],
        "expected_revision":command["expected_revision"],"params":command["params"],
        "params_hash":jcs::parameter_hash(&command["params"]).map_err(|_| Failure("COMMAND_INVALID"))?}))
}

pub fn binding_digest(scope: &Scope, command: &Value) -> Result<String, Failure> {
    let body = jcs::canonical_bytes(&binding(scope, command)?).map_err(|_| Failure("COMMAND_INVALID"))?;
    Ok(format!("{:x}", Sha256::digest(body)))
}

pub fn issued_command(scope: &Scope, command_id: &str, draft: &str) -> Result<Value, Failure> {
    let mut value = jcs::decode(draft.as_bytes()).map_err(|_| Failure("COMMAND_INVALID"))?;
    let object = value.as_object_mut().ok_or(Failure("COMMAND_INVALID"))?;
    if object.contains_key("command_id") { return Err(Failure("COMMAND_INVALID")); }
    object.insert("command_id".into(), json!(command_id));
    binding(scope, &value)?;
    Ok(value)
}
