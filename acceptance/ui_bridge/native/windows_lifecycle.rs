//! Actual Windows native launch and byte pipes; not descendant or installer closure.
#![cfg(target_os = "windows")]

use sha2::{Digest, Sha256};
use std::{fs, path::PathBuf, time::{Duration, Instant}};
use synapse_desktop::{bridge::Session, bundle::{self, CoreBundle}, lifecycle, transport::PipeConnection};

#[test]
fn native_launch_refuses_overlap_and_confirms_shutdown_before_replacement() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../synapse/ui/src-tauri/resources/core");
    let manifest = fs::read(root.join("bundle-manifest.json")).unwrap();
    let bundle = bundle::verify(&root, &format!("{:x}", Sha256::digest(&manifest))).unwrap();
    let missing = CoreBundle {
        root: bundle.root.clone(),
        python: bundle.root.join("missing-interpreter.exe"),
        runtime_version: bundle.runtime_version.clone(),
    };
    match lifecycle::spawn(&missing) {
        Err(error) => assert_eq!(error.0, "SPAWN_FAILED"),
        Ok(_) => panic!("missing interpreter unexpectedly launched a fallback"),
    }
    for _ in 0..2 {
        let mut core = lifecycle::spawn(&bundle).unwrap();
        let mut session = Session::connect(
            PipeConnection::new(core.output, core.input), &bundle.runtime_version
        ).unwrap();
        assert_eq!(session.last["domain_ready"], false);
        assert_eq!(core.process.exited().unwrap(), None);
        match lifecycle::spawn(&bundle) {
            Err(error) => assert_eq!(error.0, "RESTART_REQUIRED"),
            Ok(_) => panic!("replacement was admitted while the existing core was alive"),
        }
        assert_eq!(session.exchange("machine.ping", Instant::now() + Duration::from_secs(2)).unwrap(), None);
        assert_eq!(session.last["result"]["status"], "alive");
        assert_eq!(session.exchange("machine.shutdown", Instant::now() + Duration::from_secs(5)).unwrap(), None);
        let deadline = Instant::now() + Duration::from_secs(5);
        loop {
            if let Some(success) = core.process.exited().unwrap() {
                assert!(success);
                break;
            }
            assert!(Instant::now() < deadline, "native shutdown did not confirm process exit");
            std::thread::sleep(Duration::from_millis(10));
        }
        drop(core.process);
    }
}
