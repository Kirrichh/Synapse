//! Reconcile a persisted command with its owner's scoped read-only answer.
//! No timeout, malformed response or missing delivery grants retry authority.
use crate::{commands, intent_store::PreparedCommand, jcs, protocol::Failure};
use serde_json::{json, Value};
use std::sync::LazyLock;

pub const SCHEMA: &str = include_str!("../../../commands/reconciliation.schema.json");
static VALIDATOR: LazyLock<jsonschema::Validator> = LazyLock::new(|| {
    jsonschema::options().should_validate_formats(true)
        .build(&serde_json::from_str(SCHEMA).expect("reconciliation schema"))
        .expect("reconciliation validator")
});

pub enum RecoveryDecision {
    /// Only the original persisted command can be retried after scoped absence.
    Resend(PreparedCommand),
    /// A historical receipt is displayed; no execution is scheduled here.
    Completed(Value),
}

pub fn status_request(prepared: &PreparedCommand) -> Value {
    json!({"command_id":prepared.command()["command_id"],
        "owner":"agent.invocations", "recipient":prepared.command()["recipient"]})
}

pub fn reconcile(prepared: PreparedCommand, raw: &[u8]) -> Result<RecoveryDecision, Failure> {
    let reply = jcs::decode(raw).map_err(|_| Failure("RECORD_INVALID"))?;
    if !VALIDATOR.is_valid(&reply) { return Err(Failure("RECORD_INVALID")); }
    let scope = prepared.scope();
    let command = prepared.command();
    if reply["state"] == "ABSENT" {
        let expected = json!({"schema_version":"synapse.command.status/v1", "state":"ABSENT",
            "command_id":command["command_id"], "owner":"agent.invocations",
            "principal":scope.principal, "workspace":scope.workspace, "recipient":command["recipient"]});
        if reply != expected { return Err(Failure("SCOPE_REFUSED")); }
        return Ok(RecoveryDecision::Resend(prepared));
    }
    let binding = commands::binding(scope, command)?;
    let expected = json!({"schema_version":"synapse.invocation.cancel-result/v1",
        "command_id":command["command_id"], "binding_sha256":prepared.binding_digest(),
        "params_hash":binding["params_hash"], "state":"COMPLETED", "outcome":reply["outcome"],
        "invocation_id":command["recipient"], "replayed":true});
    if reply != expected { return Err(Failure("RECORD_INVALID")); }
    Ok(RecoveryDecision::Completed(reply))
}
