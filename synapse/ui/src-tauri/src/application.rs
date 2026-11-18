//! Native application commands: trusted identity, selected grants and intents.
//! The core rechecks every operation. Local intent records grant no authority.
use crate::{bridge::Session, client_identity::ClientIdentity, command_recovery::{self, RecoveryDecision},
    commands::Scope, intent_store::{IntentStore, PreparedCommand}, protected_data::UserProtectedData, protocol::{Failure, PROFILE}};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{collections::HashMap, path::PathBuf, time::{Duration, Instant}};

#[derive(Deserialize)]
#[serde(tag="action", rename_all="snake_case", deny_unknown_fields)]
pub enum ApplicationOperation {
    Grants {},
    Read { grant_id: String, method: String, params: Value },
    Delivery { method: String, params: Value },
    Issue { grant_id: String },
    Pending { grant_id: String, after_intent: Option<String> },
    Submit { grant_id: String, intent_id: String, draft: String },
    Recover { grant_id: String, intent_id: String, resend_if_absent: bool },
}

pub struct Application {
    identity: ClientIdentity,
    root: PathBuf,
    principal: Option<String>,
    intents: HashMap<String, IntentStore<UserProtectedData>>,
    delivery_context: Option<String>,
}

impl Application {
    pub fn new(identity: ClientIdentity, root: PathBuf) -> Self {
        Self { identity, root, principal: None, intents: HashMap::new(), delivery_context: None }
    }
    pub fn public_key(&self) -> String { self.identity.public_key() }
    pub fn disconnected(&mut self) { self.principal = None; self.intents.clear(); self.delivery_context = None; }

    /// A native Channel replacement discards old projections, never intents.
    pub fn handle_for_delivery(&mut self, session: &mut Session, context: &str,
                               operation: ApplicationOperation) -> Result<Value, Failure> {
        if self.delivery_context.as_deref() != Some(context) {
            self.grants(session)?;
            Self::exchange(session, "delivery.reset", json!({}), None)?;
            self.delivery_context = Some(context.to_owned());
        }
        self.handle(session, operation)
    }

    fn exchange(session: &mut Session, method: &str, params: Value, grant: Option<&Value>) -> Result<Value, Failure> {
        let reference = grant.map(|value| Ok((value["grant_id"].as_str().ok_or(Failure("PROTOCOL_ERROR"))?,
            value["version"].as_u64().ok_or(Failure("PROTOCOL_ERROR"))?))).transpose()?;
        session.domain_exchange(method, params, reference,
            Instant::now() + Duration::from_secs(PROFILE.write_timeout_seconds))?.map_err(Failure)
    }

    fn grants(&mut self, session: &mut Session) -> Result<Value, Failure> {
        if self.principal.is_none() {
            self.principal = Some(self.identity.authenticate(session)?.map_err(Failure)?);
        }
        Self::exchange(session, "access.grants", json!({}), None)
    }

    fn store(&mut self, grant: &Value, create: bool) -> Result<&mut IntentStore<UserProtectedData>, Failure> {
        let id = grant["grant_id"].as_str().ok_or(Failure("PROTOCOL_ERROR"))?;
        let parsed = uuid::Uuid::parse_str(id).map_err(|_| Failure("PROTOCOL_ERROR"))?;
        if parsed.to_string() != id { return Err(Failure("PROTOCOL_ERROR")); }
        let version = grant["version"].as_u64().ok_or(Failure("PROTOCOL_ERROR"))?;
        let key = format!("{id}-{version}");
        if !self.intents.contains_key(&key) {
            let directory = self.root.join(&key);
            if !create && !directory.try_exists().map_err(|_| Failure("INTENT_STORE_FAILED"))? {
                return Err(Failure("INTENT_UNKNOWN"));
            }
            if self.intents.len() >= 128 { return Err(Failure("INTENT_STORE_FULL")); }
            let principal = self.principal.clone().ok_or(Failure("AUTHENTICATION_FAILED"))?;
            let scope = Scope { principal, workspace: grant["workspace"].as_str().ok_or(Failure("PROTOCOL_ERROR"))?.into() };
            let store = IntentStore::open(&directory, scope, UserProtectedData, &[])?;
            self.intents.insert(key.clone(), store);
        }
        self.intents.get_mut(&key).ok_or(Failure("INTENT_STORE_FAILED"))
    }

    fn send_prepared(session: &mut Session, grant: &Value, prepared: PreparedCommand) -> Result<Value, Failure> {
        let result = Self::exchange(session, "invocation.cancel", json!({"command":prepared.command()}), Some(grant))?;
        let mut historical = result.clone();
        historical["replayed"] = json!(true);
        match command_recovery::reconcile(prepared, &serde_json::to_vec(&historical).map_err(|_| Failure("RECORD_INVALID"))?)? {
            RecoveryDecision::Completed(_) => Ok(result),
            RecoveryDecision::Resend(_) => Err(Failure("RECORD_INVALID")),
        }
    }

    fn recover_prepared(session: &mut Session, grant: &Value, prepared: PreparedCommand, resend: bool) -> Result<Value, Failure> {
        let intent_id = prepared.intent_id().to_owned();
        let mut params = command_recovery::status_request(&prepared);
        params.as_object_mut().ok_or(Failure("COMMAND_INVALID"))?.remove("owner");
        let status = Self::exchange(session, "command.status", params, Some(grant))?;
        match command_recovery::reconcile(prepared, &serde_json::to_vec(&status).map_err(|_| Failure("RECORD_INVALID"))?)? {
            RecoveryDecision::Completed(value) => Ok(value),
            RecoveryDecision::Resend(prepared) if resend => Self::send_prepared(session, grant, prepared),
            RecoveryDecision::Resend(_) => Ok(json!({"state":"ABSENT", "intent_id":intent_id})),
        }
    }

    pub fn handle(&mut self, session: &mut Session, operation: ApplicationOperation) -> Result<Value, Failure> {
        let grants = self.grants(session)?;
        if let ApplicationOperation::Delivery { method, params } = operation {
            if !["delivery.snapshot", "delivery.collect", "delivery.next", "delivery.unsubscribe"].contains(&method.as_str()) {
                return Err(Failure("COMMAND_INVALID"));
            }
            return Self::exchange(session, &method, params, None);
        }
        let grant_id = match &operation {
            ApplicationOperation::Grants {} => return Ok(grants),
            ApplicationOperation::Delivery { .. } => unreachable!(),
            ApplicationOperation::Read { grant_id, .. } | ApplicationOperation::Issue { grant_id }
            | ApplicationOperation::Pending { grant_id, .. } | ApplicationOperation::Submit { grant_id, .. }
            | ApplicationOperation::Recover { grant_id, .. } => grant_id,
        };
        let grant = grants["grants"].as_array().ok_or(Failure("PROTOCOL_ERROR"))?.iter()
            .find(|value| value["grant_id"].as_str() == Some(grant_id)).ok_or(Failure("ACCESS_DENIED"))?;
        match operation {
            ApplicationOperation::Grants {} | ApplicationOperation::Delivery { .. } => unreachable!(),
            ApplicationOperation::Read { method, params, .. } => {
                if !["invocation.inspect", "invocation.snapshot", "invocation.changes", "command.status", "delivery.subscribe"].contains(&method.as_str()) {
                    return Err(Failure("COMMAND_INVALID"));
                }
                Self::exchange(session, &method, params, Some(grant))
            }
            ApplicationOperation::Issue { .. } => {
                if !grant["methods"].as_array().ok_or(Failure("PROTOCOL_ERROR"))?.contains(&json!("invocation.cancel")) {
                    return Err(Failure("ACCESS_DENIED"));
                }
                Ok(json!({"intent_id":self.store(grant, true)?.issue()?}))
            }
            ApplicationOperation::Pending { after_intent, .. } => {
                let intents = match self.store(grant, false) {
                    Ok(store) => store.pending()?,
                    Err(Failure("INTENT_UNKNOWN")) if after_intent.is_none() => Vec::new(),
                    Err(error) => return Err(error),
                };
                let start = match after_intent {
                    None => 0,
                    Some(id) => intents.iter().position(|intent| intent.intent_id == id)
                        .ok_or(Failure("CURSOR_UNAVAILABLE"))? + 1,
                };
                let page = intents.iter().skip(start).take(32).map(|intent| json!({
                    "intent_id":intent.intent_id, "command_id":intent.command_id,
                    "prepared":intent.command.is_some(),
                    "recipient":intent.command.as_ref().map(|value| value["recipient"].clone())
                })).collect::<Vec<_>>();
                let next = if start + page.len() < intents.len() { page.last().map(|value| value["intent_id"].clone()) } else { None };
                Ok(json!({"intents":page, "next_after":next}))
            }
            ApplicationOperation::Submit { intent_id, draft, .. } => {
                let previously_prepared = self.store(grant, false)?.recover(&intent_id)?.is_some();
                let prepared = self.store(grant, false)?.prepare(&intent_id, &draft)?;
                if previously_prepared { Self::recover_prepared(session, grant, prepared, true) }
                else { Self::send_prepared(session, grant, prepared) }
            }
            ApplicationOperation::Recover { intent_id, resend_if_absent, .. } => {
                let Some(prepared) = self.store(grant, false)?.recover(&intent_id)? else {
                    return Ok(json!({"state":"UNPREPARED", "intent_id":intent_id}));
                };
                Self::recover_prepared(session, grant, prepared, resend_if_absent)
            }
        }
    }
}
