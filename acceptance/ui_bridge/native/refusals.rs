#[path = "support/control_peer.rs"]
mod peer;
use peer::{connection, PeerHost, Reply};
use serde_json::json;
use std::{
    sync::{Arc, Mutex},
    time::{Duration, Instant},
};
use synapse_desktop::{
    bridge::Session,
    controller::{Controller, Operation},
    protocol::{self, Failure},
};

#[test]
fn refusal_is_correlated_and_nonfatal_session_can_continue() {
    for code in ["NOT_READY", "METHOD_UNSUPPORTED", "PARAMS_INVALID"] {
        let mut session = Session::connect(
            connection(vec![Reply::Refuse(code, false), Reply::Accept]),
            "v1",
        )
        .unwrap();
        assert_eq!(
            session.exchange("machine.ping", Instant::now() + Duration::from_secs(1)),
            Ok(Some(code))
        );
        assert_eq!(session.last["code"], code);
        assert_eq!(
            session.exchange("machine.ping", Instant::now() + Duration::from_secs(1)),
            Ok(None)
        );
    }
    let mut session =
        Session::connect(connection(vec![Reply::Refuse("PIPE_CLOSED", true)]), "v1").unwrap();
    assert_eq!(
        session.exchange("machine.ping", Instant::now() + Duration::from_secs(1)),
        Err(Failure("PIPE_CLOSED"))
    );
    assert_eq!(session.last["fatal"], true);
    let mut session = Session::connect(connection(vec![Reply::Foreign]), "v1").unwrap();
    assert_eq!(
        session.exchange("machine.ping", Instant::now() + Duration::from_secs(1)),
        Err(Failure("PROTOCOL_ERROR"))
    );
    assert_eq!(session.last["type"], "ready");
}

#[test]
fn handshake_refusals_and_unbound_request_errors_keep_their_distinct_meaning() {
    for code in protocol::ERROR_CODES.iter() {
        let error = json!({"schema_version":"1.0","type":"error","code":code,"fatal":true});
        assert_eq!(protocol::check_ready(&error, 1024).unwrap_err().0, code);
        assert_eq!(protocol::check_hello(&error, "v1").unwrap_err().0, code);
        assert_eq!(
            protocol::check_refusal(&error, Some("r1")),
            Err(Failure("PROTOCOL_ERROR"))
        );
    }
    let error = json!({"schema_version":"1.0","type":"error","code":"NOT_READY","fatal":false,"request_id":"r1"});
    assert_eq!(
        protocol::check_ready(&error, 1024),
        Err(Failure("PROTOCOL_ERROR"))
    );
    let invalid =
        json!({"schema_version":"1.0","type":"error","code":"PRIVATE_VALUE","fatal":false});
    assert_eq!(
        protocol::check_refusal(&invalid, None),
        Err(Failure("PROTOCOL_ERROR"))
    );
}

fn wait(controller: &Controller, state: &str, code: Option<&str>) {
    let deadline = Instant::now() + Duration::from_secs(4);
    loop {
        let snapshot = controller.publication.snapshot();
        if snapshot.state == state && snapshot.code == code {
            return;
        }
        assert!(
            Instant::now() < deadline,
            "observed {}/{:?}",
            snapshot.state,
            snapshot.code
        );
        std::thread::sleep(Duration::from_millis(5));
    }
}
fn controller(replies: Vec<Reply>) -> (Controller, Arc<Mutex<Vec<i32>>>) {
    let exits = Arc::new(Mutex::new(vec![]));
    let controller = Controller::start(PeerHost {
        replies,
        exits: exits.clone(),
    });
    controller.request(Operation::Start).unwrap();
    wait(&controller, "ready", None);
    (controller, exits)
}
#[test]
fn close_refusal_does_not_exit_or_discard_session_and_retry_requires_real_ack() {
    let (controller, exits) = controller(vec![Reply::Refuse("NOT_READY", false), Reply::Accept]);
    controller.request(Operation::Close).unwrap();
    wait(&controller, "ready", Some("NOT_READY"));
    assert!(exits.lock().unwrap().is_empty());
    let last: serde_json::Value = serde_json::from_str(
        controller
            .publication
            .snapshot()
            .protocol_message
            .as_ref()
            .unwrap(),
    )
    .unwrap();
    assert_eq!(last["code"], "NOT_READY");
    controller.request(Operation::Close).unwrap();
    wait(&controller, "stopped", None);
    let deadline = Instant::now() + Duration::from_secs(1);
    while exits.lock().unwrap().is_empty() {
        assert!(Instant::now() < deadline);
        std::thread::yield_now();
    }
    assert_eq!(*exits.lock().unwrap(), vec![0]);
}
#[test]
fn shutdown_keeps_terminal_failure_taxonomy_and_never_invents_an_ack() {
    for (reply, code) in [
        (Reply::Eof, "CORE_EXITED"),
        (Reply::Malformed, "PROTOCOL_ERROR"),
        (Reply::Refuse("PIPE_CLOSED", true), "PIPE_CLOSED"),
    ] {
        let (controller, exits) = controller(vec![reply]);
        controller.request(Operation::Stop).unwrap();
        wait(&controller, "fail", Some(code));
        controller.request(Operation::Close).unwrap();
        wait(&controller, "fail", Some(code));
        assert!(exits.lock().unwrap().is_empty());
    }
}
#[test]
fn heartbeat_refusal_is_visible_without_tearing_down_the_control_session() {
    let (controller, exits) = controller(vec![Reply::Refuse("NOT_READY", false), Reply::Accept]);
    wait(&controller, "ready", Some("NOT_READY"));
    controller.request(Operation::Close).unwrap();
    wait(&controller, "stopped", None);
    assert!(exits.lock().unwrap().iter().all(|code| *code == 0));
}
