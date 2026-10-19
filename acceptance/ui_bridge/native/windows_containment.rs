//! Heavy, serial: the production admission mutex and Job are shared per session.
//! Synthetic descendants establish OS enforcement, not adapter/MCP integration.
#![cfg(windows)]
use std::{io::{BufRead, Write}, mem::size_of, os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle}, path::PathBuf, process::{Command, Stdio}, time::Duration};
use synapse_desktop::{bundle::CoreBundle, lifecycle};
use windows_sys::Win32::{Foundation::*, Security::SECURITY_ATTRIBUTES, System::Threading::*};

struct ObservedProcess(OwnedHandle);
impl ObservedProcess {
    fn open(pid: u32) -> Self {
        let handle = unsafe { OpenProcess(PROCESS_SYNCHRONIZE | PROCESS_TERMINATE, 0, pid) };
        assert!(!handle.is_null(), "cannot retain the witness process identity");
        Self(unsafe { OwnedHandle::from_raw_handle(handle) })
    }
    fn terminated(&self) -> bool {
        unsafe { WaitForSingleObject(self.0.as_raw_handle(), 5000) == WAIT_OBJECT_0 }
    }
}
impl Drop for ObservedProcess {
    fn drop(&mut self) {
        // Cleanup is separate from the assertion; a surviving witness is a failure.
        unsafe { TerminateProcess(self.0.as_raw_handle(), 2); WaitForSingleObject(self.0.as_raw_handle(), 5000); }
    }
}

fn bundle() -> CoreBundle {
    let python = PathBuf::from(env!("CARGO_BIN_EXE_windows_containment_probe"));
    CoreBundle { root: python.parent().unwrap().into(), python, runtime_version: "witness".into() }
}

fn read_report(output: impl std::io::Read + Send + 'static, mut errors: impl std::io::Read) -> serde_json::Value {
    let (send, receive) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut line = String::new();
        std::io::BufReader::new(output).read_line(&mut line).unwrap();
        let _ = send.send(serde_json::from_str::<serde_json::Value>(&line));
    });
    match receive.recv_timeout(Duration::from_secs(10)).expect("witness startup was not bounded") {
        Ok(report) => report,
        Err(error) => {
            let mut diagnostic = String::new();
            errors.read_to_string(&mut diagnostic).unwrap();
            panic!("witness failed: {error}; {diagnostic}");
        }
    }
}

#[test]
fn preparation_handles_owner_death_and_descendant_cleanup() {
    let release = bundle();
    let system_root = std::env::var_os("SystemRoot").unwrap();
    std::env::remove_var("SystemRoot");
    let failure = lifecycle::spawn(&release);
    std::env::set_var("SystemRoot", &system_root);
    match failure { Err(error) => assert_eq!(error.0, "ENVIRONMENT_UNSUPPORTED"), Ok(_) => panic!("missing environment was bypassed") }

    let attributes = SECURITY_ATTRIBUTES { nLength: size_of::<SECURITY_ATTRIBUTES>() as u32, lpSecurityDescriptor: std::ptr::null_mut(), bInheritHandle: 1 };
    let event = unsafe { CreateEventW(&attributes, 1, 0, std::ptr::null()) };
    assert!(!event.is_null());
    let event = unsafe { OwnedHandle::from_raw_handle(event) };
    let mut core = lifecycle::spawn(&release).unwrap();
    writeln!(core.input, "{}", event.as_raw_handle() as usize).unwrap();
    core.input.flush().unwrap();
    let report = read_report(core.output, core.errors);
    assert_eq!(report["in_product_job"], true, "witness executed outside the product Job");
    assert_eq!(report["inherited_event"], false, "an unrelated inheritable event escaped to the core");
    assert_eq!(unsafe { WaitForSingleObject(event.as_raw_handle(), 0) }, WAIT_TIMEOUT);
    let child = ObservedProcess::open(report["core"].as_u64().unwrap() as u32);
    let descendant = ObservedProcess::open(report["descendant"].as_u64().unwrap() as u32);
    drop(core.process);
    assert!(child.terminated(), "dropping the native owner did not terminate its core");
    assert!(descendant.terminated(), "dropping the native owner did not terminate its descendant");
    drop(core.input);

    let mut owner = Command::new(&release.python).arg("--owner")
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
    let owner_guard = ObservedProcess::open(owner.id());
    let report = read_report(owner.stdout.take().unwrap(), owner.stderr.take().unwrap());
    assert_eq!(report["in_product_job"], true);
    let child = ObservedProcess::open(report["core"].as_u64().unwrap() as u32);
    let descendant = ObservedProcess::open(report["descendant"].as_u64().unwrap() as u32);
    owner.kill().unwrap();
    owner.wait().unwrap();
    assert!(child.terminated(), "abrupt owner death left the core alive");
    assert!(descendant.terminated(), "abrupt owner death left a descendant alive");
    drop(owner_guard);

    // Admission must reopen only after the previous Job has no live executors.
    let replacement = lifecycle::spawn(&release).expect("cleanup did not permit a replacement");
    drop(replacement);
}
