//! Native delivery admission without a bundled process or resource dependency.
use std::{sync::{Arc,mpsc},time::Duration};
use synapse_desktop::{controller::{Controller,Host,Operation},lifecycle::ManagedCore,protocol::Failure};
struct IdleHost;
impl Host for IdleHost {
    fn launch(&mut self)->Result<(ManagedCore,String),Failure>{Err(Failure("ENVIRONMENT_UNSUPPORTED"))}
    fn exit(&self,_:i32){}
}

#[test]
fn replaced_application_ticket_is_refused_before_owner_dispatch() {
    let controller = Controller::start(IdleHost);
    let old = controller.application_delivery.subscribe(Arc::new(|_| Ok(())));
    let ticket = controller.application_delivery.reserve(&old).unwrap();
    controller.application_delivery.subscribe(Arc::new(|_| Ok(())));
    let response = controller.application(ticket, synapse_desktop::application::ApplicationOperation::Grants {}).unwrap();
    assert_eq!(response.recv_timeout(Duration::from_secs(2)).unwrap(), Err("SUBSCRIPTION_UNKNOWN"));
}

#[test]
fn a_full_application_queue_cannot_take_control_admission_capacity() {
    struct PausedHost { entered: mpsc::Sender<()>, release: mpsc::Receiver<()>, exited: mpsc::Sender<i32> }
    impl Host for PausedHost {
        fn launch(&mut self) -> Result<(ManagedCore, String), Failure> {
            self.entered.send(()).unwrap();
            self.release.recv_timeout(Duration::from_secs(5)).unwrap();
            Err(Failure("ENVIRONMENT_UNSUPPORTED"))
        }
        fn exit(&self, code:i32) { self.exited.send(code).unwrap(); }
    }
    let (entered, waiting) = mpsc::channel();
    let (release, blocked) = mpsc::channel();
    let (exited, exit) = mpsc::channel();
    let controller = Controller::start(PausedHost {entered, release:blocked, exited});
    controller.request(Operation::Start).unwrap();
    waiting.recv_timeout(Duration::from_secs(2)).unwrap();
    let mut replies=Vec::new();
    for _ in 0..synapse_desktop::protocol::PROFILE.application_request_capacity {
        let id=controller.application_delivery.subscribe(Arc::new(|_| Ok(())));
        let ticket=controller.application_delivery.reserve(&id).unwrap();
        replies.push(controller.application(ticket, synapse_desktop::application::ApplicationOperation::Grants {}).unwrap());
    }
    let id=controller.application_delivery.subscribe(Arc::new(|_| Ok(())));
    let ticket=controller.application_delivery.reserve(&id).unwrap();
    assert!(matches!(controller.application(ticket, synapse_desktop::application::ApplicationOperation::Grants {}),Err("BUSY")));
    controller.request(Operation::Close).unwrap();
    release.send(()).unwrap();
    assert_eq!(exit.recv_timeout(Duration::from_secs(2)).unwrap(),0);
    for reply in replies { assert!(reply.recv_timeout(Duration::from_secs(1)).is_err()); }
}
