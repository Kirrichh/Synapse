//! Exercise the real bounded writer queue, completion channel, and dead worker.
use std::{
    io::{self, Cursor, Write},
    sync::mpsc,
    time::{Duration, Instant},
};
use synapse_desktop::{
    protocol::{self, Failure},
    transport::PipeConnection,
};

struct Broken;
impl Write for Broken {
    fn write(&mut self, _: &[u8]) -> io::Result<usize> {
        Err(io::ErrorKind::BrokenPipe.into())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
#[test]
fn failed_writer_remains_terminal_instead_of_advertising_retryable_overload() {
    let connection = PipeConnection::new(Cursor::new(Vec::new()), Broken);
    for _ in 0..3 {
        assert_eq!(
            connection.send(&protocol::request("machine.ping", "r1")),
            Err(Failure("CORE_EXITED"))
        );
    }
}

struct Blocked(mpsc::Receiver<()>, bool);
impl Write for Blocked {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        if !self.1 {
            self.0.recv_timeout(Duration::from_secs(5)).unwrap();
            self.1 = true;
        }
        Ok(bytes.len())
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
#[test]
fn full_queue_is_retryable_and_resumes_after_the_writer_drains() {
    let (release, blocked) = mpsc::channel();
    let connection = PipeConnection::new(Cursor::new(Vec::new()), Blocked(blocked, false));
    let request = protocol::request("machine.ping", "r1");
    let mut full = false;
    let fill_deadline = Instant::now() + Duration::from_secs(2);
    while Instant::now() < fill_deadline {
        match connection.send_before(&request, Instant::now() + Duration::from_millis(5)) {
            Err(Failure("BUSY")) => {
                full = true;
                break;
            }
            other => assert_eq!(other, Err(Failure("TRANSPORT_TIMEOUT"))),
        }
    }
    assert!(full);
    release.send(()).unwrap();
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        match connection.send(&request) {
            Ok(()) => break,
            Err(Failure("BUSY")) => {
                assert!(Instant::now() < deadline);
                std::thread::yield_now();
            }
            other => panic!("writer did not recover: {other:?}"),
        }
    }
}

struct Aborted;
impl Write for Aborted {
    fn write(&mut self, _: &[u8]) -> io::Result<usize> {
        panic!("synthetic writer abort")
    }
    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}
#[test]
fn lost_completion_is_terminal_not_a_timeout() {
    let connection = PipeConnection::new(Cursor::new(Vec::new()), Aborted);
    assert_eq!(
        connection.send(&protocol::request("machine.ping", "r1")),
        Err(Failure("CORE_EXITED"))
    );
}
