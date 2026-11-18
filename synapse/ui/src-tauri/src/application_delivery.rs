//! One acknowledged application-result slot, separate from control publication.
//! Replacing a renderer subscription discards delivery, never owner completion.
use crate::{protocol::Failure, publication::Sink};
use serde_json::{json, Value};
use std::sync::{Arc, Mutex, LazyLock};

static VALIDATOR: LazyLock<jsonschema::Validator> = LazyLock::new(|| crate::domain_protocol::build_validator(
    include_str!("../../contracts/application-delivery.schema.json")));

#[derive(Clone)]
pub struct Ticket { subscription: String, request: String }
impl Ticket {
    pub fn request_id(&self) -> &str { &self.request }
    pub fn subscription_id(&self) -> &str { &self.subscription }
}

struct Subscriber { id: String, sink: Sink, pending: Option<String>, delivered: bool }
#[derive(Clone, Default)]
pub struct ApplicationDelivery { state: Arc<Mutex<Option<Subscriber>>> }

impl ApplicationDelivery {
    pub fn is_pending(&self, ticket: &Ticket) -> bool {
        self.state.lock().unwrap_or_else(|error| error.into_inner()).as_ref()
            .is_some_and(|item| item.id == ticket.subscription
                && item.pending.as_deref() == Some(ticket.request_id()) && !item.delivered)
    }
    pub fn subscribe(&self, sink: Sink) -> String {
        let id = uuid::Uuid::new_v4().to_string();
        *self.state.lock().unwrap_or_else(|error| error.into_inner()) =
            Some(Subscriber { id: id.clone(), sink, pending: None, delivered: false });
        id
    }

    pub fn reserve(&self, subscription: &str) -> Result<Ticket, Failure> {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        let current = state.as_mut().filter(|item| item.id == subscription).ok_or(Failure("SUBSCRIPTION_UNKNOWN"))?;
        if current.pending.is_some() { return Err(Failure("BUSY")); }
        let request = uuid::Uuid::new_v4().to_string();
        current.pending = Some(request.clone());
        current.delivered = false;
        Ok(Ticket { subscription: subscription.into(), request })
    }

    pub fn unsubscribe(&self, subscription: &str) {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        if state.as_ref().is_some_and(|item| item.id == subscription) { *state = None; }
    }

    /// The caller invokes this only when enqueueing was refused, before any
    /// owner can accept the operation. A lost result must wait for a new channel.
    pub fn refused_before_enqueue(&self, ticket: &Ticket) {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        if let Some(current) = state.as_mut().filter(|item| item.id == ticket.subscription
            && item.pending.as_deref() == Some(&ticket.request) && !item.delivered) {
            current.pending = None;
        }
    }

    pub fn deliver(&self, ticket: Ticket, result: Result<Value, &'static str>) -> Result<(), Failure> {
        let sink = {
            let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
            let current = state.as_mut().filter(|item| item.id == ticket.subscription
                && item.pending.as_deref() == Some(&ticket.request) && !item.delivered)
                .ok_or(Failure("SUBSCRIPTION_UNKNOWN"))?;
            current.delivered = true;
            current.sink.clone()
        };
        let mut envelope = json!({"schema_version":"synapse.application.delivery/v1",
            "subscription_id":ticket.subscription,"request_id":ticket.request});
        match result {
            Ok(value) => { envelope["type"] = json!("result"); envelope["result"] = value; }
            Err(code) => { envelope["type"] = json!("error"); envelope["code"] = json!(code); }
        }
        let encoded = match crate::jcs::canonical_bytes(&envelope) {
            Ok(bytes) if VALIDATOR.is_valid(&envelope) => String::from_utf8(bytes).map_err(|_| Failure("PROTOCOL_ERROR"))?,
            Ok(_) => json!({"schema_version":"synapse.application.delivery/v1", "subscription_id":ticket.subscription,
                "request_id":ticket.request,"type":"error","code":"PROTOCOL_ERROR"}).to_string(),
            Err(_) => json!({"schema_version":"synapse.application.delivery/v1", "subscription_id":ticket.subscription,
                "request_id":ticket.request,"type":"error","code":"RESPONSE_TOO_LARGE"}).to_string(),
        };
        sink(encoded).map_err(|_| Failure("DELIVERY_FAILED"))
    }

    pub fn acknowledge(&self, subscription: &str, request: &str) -> Result<(), Failure> {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        let current = state.as_mut().filter(|item| item.id == subscription && item.delivered
            && item.pending.as_deref() == Some(request)).ok_or(Failure("SUBSCRIPTION_UNKNOWN"))?;
        current.pending = None;
        current.delivered = false;
        Ok(())
    }
}
