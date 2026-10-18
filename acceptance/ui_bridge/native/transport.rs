use serde_json::json;
use std::{
    io::{self, Cursor, Read, Write},
    sync::{Arc, Mutex},
    time::Duration,
};
use synapse_desktop::{
    protocol::{self, Failure},
    transport::{read_frame, PipeConnection},
};

fn frame(payload: &[u8]) -> Vec<u8> {
    let mut bytes = (payload.len() as u32).to_be_bytes().to_vec();
    bytes.extend(payload);
    bytes
}
struct Fragmented(Cursor<Vec<u8>>);
impl Read for Fragmented {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        let limit = buffer.len().min(1);
        self.0.read(&mut buffer[..limit])
    }
}
struct HeaderOnly(Cursor<Vec<u8>>);
impl Read for HeaderOnly {
    fn read(&mut self, bytes: &mut [u8]) -> io::Result<usize> {
        assert!(self.0.position() < 4, "untrusted declared payload was read");
        self.0.read(bytes)
    }
}
#[test]
fn framed_bytes_survive_fragmentation_and_coalescing() {
    let unicode = "{\"text\":\"🙂Привет\"}".as_bytes();
    let mut bytes = frame(unicode);
    bytes.extend(frame(b"{}"));
    let mut source = Fragmented(Cursor::new(bytes));
    assert_eq!(read_frame(&mut source, 1024).unwrap(), unicode);
    assert_eq!(read_frame(&mut source, 1024).unwrap(), b"{}");
    assert!(read_frame(&mut source, 1024).is_err());
    assert_eq!(
        read_frame(&mut Cursor::new(frame(&[b'a'; 1024])), 1024)
            .unwrap()
            .len(),
        1024
    );
}
#[test]
fn oversize_and_zero_headers_are_refused_before_payload_read() {
    for size in [0u32, 1025, u32::MAX] {
        assert_eq!(
            read_frame(
                &mut HeaderOnly(Cursor::new(size.to_be_bytes().to_vec())),
                1024
            ),
            Err(Failure("PROTOCOL_ERROR"))
        );
    }
    for bytes in [vec![0, 0], vec![0, 0, 0, 4, b'{']] {
        assert!(read_frame(&mut Cursor::new(bytes), 1024).is_err());
    }
}
#[derive(Clone)]
struct ShortWriter(Arc<Mutex<Vec<u8>>>);
impl Write for ShortWriter {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        let size = bytes.len().min(3);
        self.0.lock().unwrap().extend(&bytes[..size]);
        Ok(size)
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
#[test]
fn complete_writes_and_prefetched_frames_obey_selected_limit() {
    let data = Arc::new(Mutex::new(Vec::new()));
    let message = protocol::request("machine.ping", "r1");
    let raw = serde_json::to_vec(&message).unwrap();
    let mut large = message.clone();
    large["params"] = json!({"padding":"x".repeat(2000)});
    let connection = PipeConnection::new(
        Cursor::new(frame(&serde_json::to_vec(&large).unwrap())),
        ShortWriter(data.clone()),
    );
    connection.send(&message).unwrap();
    assert_eq!(*data.lock().unwrap(), frame(&raw));
    connection.set_limit(1024);
    assert_eq!(
        connection.receive(Duration::from_secs(1)),
        Err(Failure("PROTOCOL_ERROR"))
    );
    assert_eq!(connection.send(&large), Err(Failure("PROTOCOL_ERROR")));
    assert_eq!(
        connection.send(&json!({"unexpected":"secret"})),
        Err(Failure("PROTOCOL_ERROR"))
    );
}
