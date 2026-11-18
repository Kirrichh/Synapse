//! Raw stdin/stdout bytes: no text conversion or newline-oriented shell adapter.
use crate::protocol::{self, Failure, PROFILE};
use serde_json::Value;
use std::{
    io::{Read, Write},
    sync::{
        atomic::{AtomicUsize, Ordering},
        mpsc, Arc,
    },
    thread,
    time::{Duration, Instant},
};

pub fn read_frame(source: &mut impl Read, limit: usize) -> Result<Vec<u8>, Failure> {
    let mut header = [0u8; 4];
    source
        .read_exact(&mut header)
        .map_err(|_| Failure("CORE_EXITED"))?;
    let size = u32::from_be_bytes(header) as usize;
    if size == 0 || size > limit {
        return Err(Failure("PROTOCOL_ERROR"));
    }
    let mut payload = vec![0; size];
    source
        .read_exact(&mut payload)
        .map_err(|_| Failure("PROTOCOL_ERROR"))?;
    Ok(payload)
}

type Receipt = mpsc::SyncSender<Result<(), Failure>>;
type Outbound = (Vec<u8>, Receipt);

pub struct PipeConnection {
    incoming: mpsc::Receiver<Result<Vec<u8>, Failure>>,
    outgoing: mpsc::SyncSender<Outbound>,
    limit: Arc<AtomicUsize>,
}
impl PipeConnection {
    pub fn new(
        mut source: impl Read + Send + 'static,
        mut destination: impl Write + Send + 'static,
    ) -> Self {
        let (input, incoming) = mpsc::sync_channel(PROFILE.queue_capacity);
        let (outgoing, output) = mpsc::sync_channel::<Outbound>(PROFILE.queue_capacity);
        let limit = Arc::new(AtomicUsize::new(PROFILE.bootstrap_max_frame_bytes));
        let reading_limit = limit.clone();
        thread::spawn(move || loop {
            let value = read_frame(&mut source, reading_limit.load(Ordering::Acquire));
            let failed = value.is_err();
            if input.send(value).is_err() || failed {
                break;
            }
        });
        thread::spawn(move || {
            while let Ok((frame, receipt)) = output.recv() {
                let result = destination
                    .write_all(&frame)
                    .and_then(|_| destination.flush())
                    .map_err(|_| Failure("CORE_EXITED"));
                let failed = result.is_err();
                let _ = receipt.send(result);
                if failed {
                    break;
                }
            }
        });
        Self {
            incoming,
            outgoing,
            limit,
        }
    }

    pub fn set_limit(&self, limit: usize) {
        self.limit.store(limit, Ordering::Release);
    }

    pub fn receive(&self, timeout: Duration) -> Result<Value, Failure> {
        let payload = self
            .incoming
            .recv_timeout(timeout)
            .map_err(|error| match error {
                mpsc::RecvTimeoutError::Timeout => Failure("TRANSPORT_TIMEOUT"),
                mpsc::RecvTimeoutError::Disconnected => Failure("CORE_EXITED"),
            })??;
        if payload.len() > self.limit.load(Ordering::Acquire) {
            return Err(Failure("PROTOCOL_ERROR"));
        }
        protocol::decode(&payload)
    }

    pub fn send(&self, value: &Value) -> Result<(), Failure> {
        self.send_before(
            value,
            Instant::now() + Duration::from_secs(PROFILE.write_timeout_seconds),
        )
    }

    pub fn send_before(&self, value: &Value, deadline: Instant) -> Result<(), Failure> {
        let payload = serde_json::to_vec(value).map_err(|_| Failure("PROTOCOL_ERROR"))?;
        protocol::decode(&payload)?;
        if payload.len() > self.limit.load(Ordering::Acquire) {
            return Err(Failure("PROTOCOL_ERROR"));
        }
        let mut frame = (payload.len() as u32).to_be_bytes().to_vec();
        frame.extend_from_slice(&payload);
        let (receipt, completion) = mpsc::sync_channel(1);
        let remaining = deadline
            .checked_duration_since(Instant::now())
            .ok_or(Failure("TRANSPORT_TIMEOUT"))?;
        self.outgoing
            .try_send((frame, receipt))
            .map_err(|error| match error {
                mpsc::TrySendError::Full(_) => Failure("BUSY"),
                mpsc::TrySendError::Disconnected(_) => Failure("CORE_EXITED"),
            })?;
        completion
            .recv_timeout(remaining.min(Duration::from_secs(PROFILE.write_timeout_seconds)))
            .map_err(|error| match error {
                mpsc::RecvTimeoutError::Timeout => Failure("TRANSPORT_TIMEOUT"),
                mpsc::RecvTimeoutError::Disconnected => Failure("CORE_EXITED"),
            })?
    }
}
