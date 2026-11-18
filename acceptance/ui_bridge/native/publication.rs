use std::{
    io::Cursor,
    sync::{mpsc, Arc, Mutex},
    time::{Duration, Instant},
};
use synapse_desktop::{
    diagnostics::{safe_code, Diagnostics},
    publication::Publication,
};

#[test]
fn slow_renderer_never_blocks_state_and_replaced_sink_failure_cannot_remove_new_sink() {
    let publication = Publication::default();
    let (entered, waiting) = mpsc::sync_channel(1);
    let (release, blocked) = mpsc::sync_channel(1);
    let blocked = Mutex::new(blocked);
    let first = publication.subscribe(Arc::new(move |_| {
        entered.send(()).unwrap();
        blocked.lock().unwrap().recv().unwrap();
        Err(())
    }));
    waiting.recv_timeout(Duration::from_secs(1)).unwrap();
    let start = Instant::now();
    for _ in 0..100 {
        publication
            .update(|snapshot| snapshot.state = "connecting")
            .unwrap();
    }
    assert!(start.elapsed() < Duration::from_secs(1));
    let (sent, received) = mpsc::channel();
    let latest = publication.subscribe(Arc::new(move |text| {
        sent.send(text).unwrap();
        Ok(())
    }));
    let initial: serde_json::Value = serde_json::from_str(&first).unwrap();
    let newest: serde_json::Value = serde_json::from_str(&latest).unwrap();
    assert_eq!(initial["session_id"], newest["session_id"]);
    assert_eq!(newest["state"], "connecting");
    assert_eq!(newest["sequence"], 100);
    release.send(()).unwrap();
    let observed: serde_json::Value =
        serde_json::from_str(&received.recv_timeout(Duration::from_secs(1)).unwrap()).unwrap();
    assert_eq!(observed, newest);
    publication
        .acknowledge(newest["subscription_id"].as_str().unwrap(), 100)
        .unwrap();
    publication
        .update(|snapshot| snapshot.state = "ready")
        .unwrap();
    let updated: serde_json::Value =
        serde_json::from_str(&received.recv_timeout(Duration::from_secs(1)).unwrap()).unwrap();
    assert_eq!(updated["state"], "ready");
    assert_eq!(updated["domain_ready"], false);
    let schema: serde_json::Value =
        serde_json::from_str(synapse_desktop::publication::SCHEMA).unwrap();
    assert!(jsonschema::is_valid(&schema, &updated));
}
#[test]
fn missing_ack_coalesces_without_feeding_an_unbounded_webview_queue() {
    let publication = Publication::default();
    let (sent, received) = mpsc::channel();
    publication.subscribe(Arc::new(move |text| {
        sent.send(text).unwrap();
        Ok(())
    }));
    let first: serde_json::Value =
        serde_json::from_str(&received.recv_timeout(Duration::from_secs(1)).unwrap()).unwrap();
    for _ in 0..100 {
        publication
            .update(|snapshot| snapshot.state = "connecting")
            .unwrap();
    }
    assert!(received.recv_timeout(Duration::from_millis(50)).is_err());
    assert!(publication.acknowledge("foreign", 0).is_err());
    assert!(publication
        .acknowledge(first["subscription_id"].as_str().unwrap(), 99)
        .is_err());
    publication
        .acknowledge(first["subscription_id"].as_str().unwrap(), 0)
        .unwrap();
    let latest: serde_json::Value =
        serde_json::from_str(&received.recv_timeout(Duration::from_secs(1)).unwrap()).unwrap();
    assert_eq!(latest["sequence"], 100);
    publication
        .acknowledge(first["subscription_id"].as_str().unwrap(), 100)
        .unwrap();
    assert!(received.recv_timeout(Duration::from_millis(50)).is_err());
}
#[test]
fn stderr_is_drained_bounded_and_redacted() {
    assert_eq!(safe_code(b"machine: INVALID_JSON"), "INVALID_JSON");
    for raw in [
        b"token=supersecret".as_slice(),
        b"machine: INVALID_JSON secret",
        b"\xff",
        b"machine: UNKNOWN",
    ] {
        assert_eq!(safe_code(raw), "CORE_DIAGNOSTIC");
    }
    let diagnostics = Diagnostics::default();
    let mut bytes = b"token=supersecret\n".repeat(10000);
    bytes.extend(b"machine: PIPE_CLOSED");
    diagnostics.drain(Cursor::new(bytes));
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        let events = diagnostics.snapshot();
        if events.last().is_some_and(|code| code == "PIPE_CLOSED") {
            assert_eq!(events.len(), 32);
            assert!(!format!("{events:?}").contains("supersecret"));
            break;
        }
        assert!(
            Instant::now() < deadline,
            "diagnostic reader did not reach EOF"
        );
        std::thread::yield_now();
    }
}
