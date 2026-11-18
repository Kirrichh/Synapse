//! Drain every stderr byte, retaining only bounded, declared diagnostic codes.
use crate::protocol::ERROR_CODES;
use std::{
    collections::VecDeque,
    io::Read,
    sync::{Arc, Mutex},
    thread,
};

const PREFIX: &str = "machine: ";

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
            // The extra byte prevents truncating an overlong line to a valid code.
            let limit = PREFIX.len() + ERROR_CODES.iter().map(String::len).max().unwrap_or(0) + 1;
            let mut line = Vec::with_capacity(limit);
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
                            } else if line.len() < limit {
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
    match text.strip_prefix(PREFIX) {
        Some(code) if ERROR_CODES.contains(code) => {
            ERROR_CODES.get(code).expect("declared code").as_str()
        }
        _ => "CORE_DIAGNOSTIC",
    }
}
