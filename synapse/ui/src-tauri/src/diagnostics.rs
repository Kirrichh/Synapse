//! Drain every stderr byte, retaining only bounded, declared diagnostic codes.
use crate::protocol::SCHEMA;
use serde_json::Value;
use std::{
    collections::{HashSet, VecDeque},
    io::Read,
    sync::{Arc, LazyLock, Mutex},
    thread,
};

static CODES: LazyLock<HashSet<String>> = LazyLock::new(|| {
    let schema: Value = serde_json::from_str(SCHEMA).expect("envelope schema");
    schema["$defs"]["errorCode"]["enum"]
        .as_array()
        .expect("codes")
        .iter()
        .map(|value| value.as_str().expect("code").to_owned())
        .collect()
});

#[derive(Clone, Default)]
pub struct Diagnostics(Arc<Mutex<VecDeque<String>>>);
impl Diagnostics {
    pub fn record(&self, code: &str) {
        let mut events = self.0.lock().unwrap_or_else(|error| error.into_inner());
        if events.len() == 32 {
            events.pop_front();
        }
        events.push_back(code.to_owned());
    }
    pub fn snapshot(&self) -> Vec<String> {
        self.0
            .lock()
            .unwrap_or_else(|error| error.into_inner())
            .iter()
            .cloned()
            .collect()
    }
    pub fn drain(&self, mut source: impl Read + Send + 'static) {
        let log = self.clone();
        thread::spawn(move || {
            let mut bytes = [0u8; 1024];
            let mut line = Vec::with_capacity(97);
            loop {
                match source.read(&mut bytes) {
                    Ok(0) | Err(_) => {
                        if !line.is_empty() {
                            log.record(safe_code(&line));
                        }
                        break;
                    }
                    Ok(length) => {
                        for byte in &bytes[..length] {
                            if *byte == b'\n' {
                                log.record(safe_code(&line));
                                line.clear();
                            } else if line.len() < 97 {
                                line.push(*byte);
                            }
                        }
                    }
                }
            }
        });
    }
}

pub fn safe_code(line: &[u8]) -> &'static str {
    let text = std::str::from_utf8(line).unwrap_or("");
    match text.strip_prefix("machine: ") {
        Some(code) if CODES.contains(code) => CODES.get(code).expect("declared code").as_str(),
        _ => "CORE_DIAGNOSTIC",
    }
}
