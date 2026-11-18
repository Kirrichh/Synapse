//! Synthetic external byte peer for refusal and shutdown contract acceptance.
use serde_json::{json, Value};
use std::{
    collections::VecDeque,
    io::{self, Cursor, Read, Write},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc, Arc, Mutex,
    },
};
use synapse_desktop::{
    controller::Host,
    lifecycle::{ManagedCore, RunningProcess},
    protocol::Failure,
    transport::PipeConnection,
};

#[derive(Clone)]
pub enum Reply {
    Accept,
    Refuse(&'static str, bool),
    Foreign,
    Malformed,
    Eof,
}
pub struct Input {
    incoming: mpsc::Receiver<Vec<u8>>,
    pending: Cursor<Vec<u8>>,
}
impl Read for Input {
    fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
        if self.pending.position() == self.pending.get_ref().len() as u64 {
            self.pending = Cursor::new(self.incoming.recv().unwrap_or_default());
        }
        self.pending.read(bytes)
    }
}
fn send(sender: &mpsc::Sender<Vec<u8>>, value: Value) {
    let raw = serde_json::to_vec(&value).unwrap();
    let mut bytes = (raw.len() as u32).to_be_bytes().to_vec();
    bytes.extend(raw);
    let _ = sender.send(bytes);
}
pub struct Output {
    sender: mpsc::Sender<Vec<u8>>,
    buffer: Vec<u8>,
    replies: VecDeque<Reply>,
    exited: Arc<AtomicBool>,
}
impl Write for Output {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.buffer.extend_from_slice(bytes);
        while self.buffer.len() >= 4 {
            let length = u32::from_be_bytes(self.buffer[..4].try_into().unwrap()) as usize;
            if self.buffer.len() < length + 4 {
                break;
            }
            let value: Value = serde_json::from_slice(&self.buffer[4..length + 4]).unwrap();
            self.buffer.drain(..length + 4);
            if value["type"] == "select" {
                send(
                    &self.sender,
                    json!({"schema_version":"1.0","type":"ready","profile":"synapse.control.v1",
                    "protocol_version":1,"max_frame_bytes":value["max_frame_bytes"],"command_classes":["control"],"domain_ready":false}),
                );
            } else if value["type"] == "request" {
                let reply = match self.replies.pop_front().unwrap_or(Reply::Accept) {
                    Reply::Accept => {
                        let result = if value["method"] == "machine.shutdown" {
                            self.exited.store(true, Ordering::SeqCst);
                            json!({"status":"stopped","pending_commands":0})
                        } else {
                            json!({"status":"alive"})
                        };
                        json!({"schema_version":"1.0","type":"result","request_id":value["request_id"],"method":value["method"],"result":result})
                    }
                    Reply::Refuse(code, fatal) => {
                        json!({"schema_version":"1.0","type":"error","request_id":value["request_id"],"code":code,"fatal":fatal})
                    }
                    Reply::Foreign => {
                        json!({"schema_version":"1.0","type":"error","request_id":"foreign","code":"NOT_READY","fatal":false})
                    }
                    Reply::Malformed => {
                        let _ = self.sender.send(vec![0, 0, 0, 1, b'{']);
                        continue;
                    }
                    Reply::Eof => {
                        let _ = self.sender.send(vec![]);
                        continue;
                    }
                };
                send(&self.sender, reply);
            }
        }
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
pub fn pipes(replies: Vec<Reply>, exited: Arc<AtomicBool>) -> (Input, Output) {
    let (sender, incoming) = mpsc::channel();
    send(
        &sender,
        json!({"schema_version":"1.0","type":"hello","component":"core","component_version":"v1",
        "protocol_min":1,"protocol_max":1,"profile":"synapse.control.v1","max_frame_bytes":1024}),
    );
    (
        Input {
            incoming,
            pending: Cursor::new(vec![]),
        },
        Output {
            sender,
            buffer: vec![],
            replies: replies.into(),
            exited,
        },
    )
}
pub fn connection(replies: Vec<Reply>) -> PipeConnection {
    let (input, output) = pipes(replies, Arc::new(AtomicBool::new(false)));
    PipeConnection::new(input, output)
}
struct Process(Arc<AtomicBool>);
impl RunningProcess for Process {
    fn id(&self) -> u32 {
        123
    }
    fn exited(&mut self) -> Result<Option<bool>, Failure> {
        Ok(self.0.load(Ordering::SeqCst).then_some(true))
    }
}
pub struct PeerHost {
    pub replies: Vec<Reply>,
    pub exits: Arc<Mutex<Vec<i32>>>,
}
impl Host for PeerHost {
    fn launch(&mut self) -> Result<(ManagedCore, String), Failure> {
        let exited = Arc::new(AtomicBool::new(false));
        let (output, input) = pipes(self.replies.drain(..).collect(), exited.clone());
        Ok((
            ManagedCore {
                process: Box::new(Process(exited)),
                input: Box::new(input),
                output: Box::new(output),
                errors: Box::new(io::empty()),
            },
            "v1".into(),
        ))
    }
    fn exit(&self, code: i32) {
        self.exits.lock().unwrap().push(code);
    }
}
