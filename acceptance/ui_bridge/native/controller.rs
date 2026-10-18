//! Real controller + canonical core; the external host does not claim OS containment.
use std::{
    io::Cursor,
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicUsize, Ordering},
        mpsc, Arc,
    },
    time::{Duration, Instant},
};
use synapse_desktop::{
    controller::{Controller, Host, Operation},
    lifecycle::{ManagedCore, RunningProcess},
    protocol::Failure,
};

struct ObservedChild(Child);
impl RunningProcess for ObservedChild {
    fn id(&self) -> u32 {
        self.0.id()
    }
    fn exited(&mut self) -> Result<Option<bool>, Failure> {
        Ok(self.0.try_wait().unwrap().map(|status| status.success()))
    }
}
impl Drop for ObservedChild {
    fn drop(&mut self) {
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
struct ExternalHost {
    calls: Arc<AtomicUsize>,
    exits: mpsc::Sender<i32>,
    fail_first: bool,
    corrupt: bool,
}
impl Host for ExternalHost {
    fn launch(&mut self) -> Result<(ManagedCore, String), Failure> {
        let call = self.calls.fetch_add(1, Ordering::SeqCst);
        if call == 0 && self.fail_first {
            return Err(Failure("ENVIRONMENT_UNSUPPORTED"));
        }
        let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../../../synapse/ui/src-tauri/resources/core");
        let record: serde_json::Value =
            serde_json::from_slice(&std::fs::read(root.join("bundle-manifest.json")).unwrap())
                .unwrap();
        let mut child = Command::new(root.join(record["python"].as_str().unwrap()))
            .args(["-I", "-B", "-m", "synapse", "machine"])
            .current_dir(&root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        let input = Box::new(child.stdin.take().unwrap());
        let errors = Box::new(child.stderr.take().unwrap());
        let output: Box<dyn std::io::Read + Send> = if self.corrupt {
            Box::new(Cursor::new(vec![0, 0, 0, 1, b'{']))
        } else {
            Box::new(child.stdout.take().unwrap())
        };
        Ok((
            ManagedCore {
                process: Box::new(ObservedChild(child)),
                input,
                output,
                errors,
            },
            record["runtime_version"].as_str().unwrap().into(),
        ))
    }
    fn exit(&self, code: i32) {
        self.exits.send(code).unwrap();
    }
}
fn controller(
    fail_first: bool,
    corrupt: bool,
) -> (Controller, Arc<AtomicUsize>, mpsc::Receiver<i32>) {
    let calls = Arc::new(AtomicUsize::new(0));
    let (exits, exit) = mpsc::channel();
    (
        Controller::start(ExternalHost {
            calls: calls.clone(),
            exits,
            fail_first,
            corrupt,
        }),
        calls,
        exit,
    )
}
fn wait(controller: &Controller, state: &str, code: Option<&str>) {
    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        let snapshot = controller.publication.snapshot();
        if snapshot.state == state && snapshot.code == code {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "expected {state}/{code:?}, observed {}/{:?}",
            snapshot.state,
            snapshot.code
        );
        std::thread::sleep(Duration::from_millis(5));
    }
}
#[test]
fn ready_requires_negotiation_stop_requires_ack_and_restart_is_never_automatic() {
    let (controller, calls, exit) = controller(false, false);
    controller.request(Operation::Start).unwrap();
    wait(&controller, "ready", None);
    let initial = controller.publication.snapshot();
    assert!(!initial.domain_ready);
    assert!(initial.process_id.is_some());
    assert_eq!(initial.profile.as_deref(), Some("synapse.control.v1"));
    assert_eq!(
        serde_json::from_str::<serde_json::Value>(initial.protocol_message.as_ref().unwrap())
            .unwrap()["type"],
        "ready"
    );
    controller.request(Operation::Start).unwrap();
    controller.request(Operation::Stop).unwrap();
    wait(&controller, "stopped", None);
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    let last: serde_json::Value = serde_json::from_str(
        controller
            .publication
            .snapshot()
            .protocol_message
            .as_ref()
            .unwrap(),
    )
    .unwrap();
    assert_eq!(last["method"], "machine.shutdown");
    assert_eq!(last["result"]["status"], "stopped");
    controller.request(Operation::Start).unwrap();
    wait(&controller, "fail", Some("RESTART_REQUIRED"));
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert!(exit.try_recv().is_err());
    controller.request(Operation::Close).unwrap();
    assert_eq!(exit.recv_timeout(Duration::from_secs(2)).unwrap(), 0);
}
#[test]
fn failed_prelaunch_can_retry_but_corrupt_launched_session_requires_restart() {
    let (controller, calls, exit) = controller(true, false);
    controller.request(Operation::Start).unwrap();
    wait(&controller, "fail", Some("ENVIRONMENT_UNSUPPORTED"));
    assert!(controller.publication.snapshot().process_id.is_none());
    controller.request(Operation::Start).unwrap();
    wait(&controller, "ready", None);
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    controller.request(Operation::Close).unwrap();
    assert_eq!(exit.recv_timeout(Duration::from_secs(6)).unwrap(), 0);
    let (failed, calls, exit) = self::controller(false, true);
    failed.request(Operation::Start).unwrap();
    wait(&failed, "fail", Some("PROTOCOL_ERROR"));
    failed.request(Operation::Start).unwrap();
    wait(&failed, "fail", Some("RESTART_REQUIRED"));
    failed.request(Operation::Close).unwrap();
    wait(&failed, "fail", Some("SHUTDOWN_TIMEOUT"));
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert!(exit.try_recv().is_err());
}
#[test]
fn heartbeat_updates_real_liveness_without_a_renderer_and_unstarted_close_is_safe() {
    let (controller, _, exit) = controller(false, false);
    controller.request(Operation::Start).unwrap();
    wait(&controller, "ready", None);
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        let snapshot = controller.publication.snapshot();
        let reply: serde_json::Value =
            serde_json::from_str(snapshot.protocol_message.as_ref().unwrap()).unwrap();
        if reply["method"] == "machine.ping" {
            assert_eq!(reply["result"]["status"], "alive");
            break;
        }
        assert!(Instant::now() < deadline);
        std::thread::sleep(Duration::from_millis(10));
    }
    controller.request(Operation::Close).unwrap();
    assert_eq!(exit.recv_timeout(Duration::from_secs(6)).unwrap(), 0);
    let (unstarted, calls, exit) = self::controller(false, false);
    unstarted.request(Operation::Close).unwrap();
    assert_eq!(exit.recv_timeout(Duration::from_secs(1)).unwrap(), 0);
    assert_eq!(calls.load(Ordering::SeqCst), 0);
}
