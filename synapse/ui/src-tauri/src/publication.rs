//! Bounded, coalescing bootstrap snapshots. A slow sink never blocks the owner.
use crate::protocol::Failure;
use serde::Serialize;
use std::sync::{mpsc, Arc, Mutex};

pub const SCHEMA: &str = include_str!("../../contracts/bootstrap.schema.json");

#[derive(Clone, Serialize)]
pub struct Snapshot {
    pub schema_version: &'static str,
    pub session_id: String,
    pub subscription_id: String,
    pub sequence: u32,
    pub state: &'static str,
    pub domain_ready: bool,
    pub diagnostics: Vec<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub core_version: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub process_id: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub profile: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub protocol_message: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub code: Option<&'static str>,
}
impl Default for Snapshot {
    fn default() -> Self {
        Self {
            schema_version: "1.0",
            session_id: uuid::Uuid::new_v4().to_string(),
            subscription_id: uuid::Uuid::new_v4().to_string(),
            sequence: 0,
            state: "native",
            domain_ready: false,
            diagnostics: Vec::new(),
            core_version: None,
            process_id: None,
            profile: None,
            protocol_message: None,
            code: None,
        }
    }
}
pub type Sink = Arc<dyn Fn(String) -> Result<(), ()> + Send + Sync>;
struct Subscriber {
    id: String,
    sink: Sink,
    sent: Option<u32>,
    waiting: bool,
}
struct State {
    snapshot: Snapshot,
    subscriber: Option<Subscriber>,
}
#[derive(Clone)]
pub struct Publication {
    state: Arc<Mutex<State>>,
    notify: mpsc::SyncSender<()>,
}
impl Default for Publication {
    fn default() -> Self {
        let state = Arc::new(Mutex::new(State {
            snapshot: Snapshot::default(),
            subscriber: None,
        }));
        let (notify, pending) = mpsc::sync_channel(1);
        let publishing = state.clone();
        std::thread::spawn(move || {
            while pending.recv().is_ok() {
                let delivery = {
                    let mut state = publishing.lock().unwrap_or_else(|e| e.into_inner());
                    let snapshot = state.snapshot.clone();
                    state.subscriber.as_mut().and_then(|subscriber| {
                        if subscriber.waiting || subscriber.sent == Some(snapshot.sequence) {
                            return None;
                        }
                        subscriber.waiting = true;
                        subscriber.sent = Some(snapshot.sequence);
                        Some((
                            subscriber.id.clone(),
                            subscriber.sink.clone(),
                            serde_json::to_string(&snapshot).expect("snapshot"),
                        ))
                    })
                };
                if let Some((id, sink, json)) = delivery {
                    if sink(json).is_err() {
                        let mut state = publishing.lock().unwrap_or_else(|e| e.into_inner());
                        if state
                            .subscriber
                            .as_ref()
                            .is_some_and(|subscriber| subscriber.id == id)
                        {
                            state.subscriber = None;
                        }
                    }
                }
            }
        });
        Self { state, notify }
    }
}
impl Publication {
    pub fn subscribe(&self, sink: Sink) -> String {
        // Replace the previous subscriber and its ACK slot atomically. Only
        // sink deliveries set waiting/sent; the returned initial snapshot is
        // unacknowledged. A duplicate channel delivery must still be ACKed.
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let id = uuid::Uuid::new_v4().to_string();
        state.snapshot.subscription_id = id.clone();
        state.subscriber = Some(Subscriber {
            id,
            sink,
            sent: None,
            waiting: false,
        });
        let json = serde_json::to_string(&state.snapshot).expect("snapshot");
        let _ = self.notify.try_send(());
        json
    }
    pub fn snapshot(&self) -> Snapshot {
        self.state
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .snapshot
            .clone()
    }
    pub fn acknowledge(&self, subscription: &str, sequence: u32) -> Result<(), Failure> {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let subscriber = state.subscriber.as_mut().ok_or(Failure("CALLER_REFUSED"))?;
        if subscriber.id != subscription || subscriber.sent != Some(sequence) {
            return Err(Failure("CALLER_REFUSED"));
        }
        subscriber.waiting = false;
        drop(state);
        let _ = self.notify.try_send(());
        Ok(())
    }
    pub fn update(&self, change: impl FnOnce(&mut Snapshot)) -> Result<(), Failure> {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let sequence = state
            .snapshot
            .sequence
            .checked_add(1)
            .ok_or(Failure("INTERNAL_ERROR"))?;
        change(&mut state.snapshot);
        state.snapshot.sequence = sequence;
        drop(state);
        // Full bootstrap snapshots supersede earlier snapshots. This is not a
        // domain event/result queue and cannot acknowledge any domain effect.
        let _ = self.notify.try_send(());
        Ok(())
    }
}
