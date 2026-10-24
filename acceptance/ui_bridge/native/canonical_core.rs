//! Real bundled Python interop, not proof of installed OS process containment.
use std::{
    path::PathBuf,
    process::{Command, Stdio},
    time::{Duration, Instant},
};
use synapse_desktop::{bridge::Session, transport::PipeConnection};
#[test]
fn native_bridge_negotiates_and_confirms_canonical_core_shutdown() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../synapse/ui/src-tauri/resources/core");
    let manifest: serde_json::Value =
        serde_json::from_slice(&std::fs::read(root.join("bundle-manifest.json")).unwrap()).unwrap();
    let mut child = Command::new(root.join(manifest["python"].as_str().unwrap()))
        .args(["-I", "-B", "-m", "synapse", "machine"])
        .current_dir(&root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let connection = PipeConnection::new(child.stdout.take().unwrap(), child.stdin.take().unwrap());
    let mut session =
        Session::connect(connection, manifest["runtime_version"].as_str().unwrap()).unwrap();
    assert_eq!(session.last["domain_ready"], false);
    session
        .exchange("machine.status", Instant::now() + Duration::from_secs(2))
        .unwrap();
    assert_eq!(session.last["result"]["domain_ready"], false);
    session
        .exchange("machine.ping", Instant::now() + Duration::from_secs(2))
        .unwrap();
    assert_eq!(session.last["result"]["status"], "alive");
    session
        .exchange("machine.shutdown", Instant::now() + Duration::from_secs(5))
        .unwrap();
    assert_eq!(session.last["result"]["pending_commands"], 0);
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            assert!(status.success());
            break;
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            panic!("shutdown exceeded deadline");
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

#[test]
fn canonical_core_preserves_declared_diagnostic_over_real_stderr() {
    use std::io::Write;
    use synapse_desktop::diagnostics::Diagnostics;
    struct ChildGuard(std::process::Child);
    impl Drop for ChildGuard {
        fn drop(&mut self) { let _ = self.0.kill(); let _ = self.0.wait(); }
    }
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../synapse/ui/src-tauri/resources/core");
    let manifest: serde_json::Value =
        serde_json::from_slice(&std::fs::read(root.join("bundle-manifest.json")).unwrap()).unwrap();
    let mut child = ChildGuard(Command::new(root.join(manifest["python"].as_str().unwrap()))
        .args(["-I", "-B", "-m", "synapse", "machine"])
        .current_dir(&root).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped())
        .spawn().unwrap());
    let diagnostics = Diagnostics::default();
    diagnostics.drain(child.0.stderr.take().unwrap());
    child.0.stdin.as_mut().unwrap().write_all(&[0, 0, 0, 0]).unwrap();
    let deadline = Instant::now() + Duration::from_secs(5);
    loop {
        if let Some(status) = child.0.try_wait().unwrap() {
            assert_eq!(status.code(), Some(2));
            if !diagnostics.snapshot().is_empty() { break; }
        }
        assert!(Instant::now() < deadline, "canonical diagnostic exceeded deadline");
        std::thread::sleep(Duration::from_millis(10));
    }
    assert_eq!(diagnostics.snapshot(), ["INVALID_LENGTH"]);
}
