//! One native owner for the core session; renderer reloads cannot spawn another.
use crate::{
    bridge::Session,
    diagnostics::Diagnostics,
    lifecycle::RunningProcess,
    protocol::{Failure, PROFILE},
    publication::Publication,
    transport::PipeConnection,
};
use std::{
    sync::mpsc,
    time::{Duration, Instant},
};

/// Platform host establishes containment and verified bundle identity before
/// returning a process. It also owns the application exit operation.
pub trait Host: Send + 'static {
    fn launch(&mut self) -> Result<(crate::lifecycle::ManagedCore, String), Failure>;
    fn exit(&self, code: i32);
}

pub enum Operation {
    Start,
    Stop,
    Close,
}
pub struct Controller {
    pub publication: Publication,
    operations: mpsc::SyncSender<Operation>,
}
impl Controller {
    pub fn start(host: impl Host) -> Self {
        let publication = Publication::default();
        let (operations, requests) = mpsc::sync_channel(8);
        let owner = publication.clone();
        std::thread::spawn(move || {
            Owner {
                host,
                publication: owner,
                process: None,
                session: None,
                diagnostics: Diagnostics::default(),
                launched: false,
                stopped: false,
            }
            .run(requests)
        });
        Self {
            publication,
            operations,
        }
    }
    pub fn request(&self, operation: Operation) -> Result<(), &'static str> {
        self.operations.try_send(operation).map_err(|_| "BUSY")
    }
}

struct Owner<H: Host> {
    host: H,
    publication: Publication,
    process: Option<Box<dyn RunningProcess>>,
    session: Option<Session>,
    diagnostics: Diagnostics,
    launched: bool,
    stopped: bool,
}
impl<H: Host> Owner<H> {
    fn state(&self, state: &'static str, code: Option<&'static str>) {
        let _ = self.publication.update(|snapshot| {
            snapshot.state = state;
            snapshot.code = code;
            snapshot.diagnostics = self.diagnostics.snapshot();
            if let Some(session) = &self.session {
                snapshot.protocol_message = Some(session.last.to_string());
            }
        });
    }
    fn fail(&mut self, failure: Failure) {
        self.session = None;
        self.state("fail", Some(failure.0));
    }
    fn connect(&mut self) -> Result<(), Failure> {
        if self.session.is_some() {
            return Ok(());
        }
        if self.launched {
            return Err(Failure("RESTART_REQUIRED"));
        }
        self.state("connecting", None);
        let (child, runtime_version) = self.host.launch()?;
        self.launched = true;
        self.diagnostics.drain(child.errors);
        self.publication.update(|snapshot| {
            snapshot.core_version = Some(runtime_version.clone());
            snapshot.process_id = Some(child.process.id());
        })?;
        self.process = Some(child.process);
        self.session = Some(Session::connect(
            PipeConnection::new(child.output, child.input),
            &runtime_version,
        )?);
        self.publication
            .update(|snapshot| snapshot.profile = Some(PROFILE.profile_id.clone()))?;
        self.state("ready", None);
        Ok(())
    }
    fn stop(&mut self) -> Result<(), Failure> {
        if !self.launched || self.stopped {
            return Ok(());
        }
        let deadline = Instant::now() + Duration::from_secs(PROFILE.shutdown_timeout_seconds);
        self.state("stopping", None);
        let session = self.session.as_mut().ok_or(Failure("SHUTDOWN_TIMEOUT"))?;
        session
            .exchange("machine.shutdown", deadline)
            .map_err(|_| Failure("SHUTDOWN_TIMEOUT"))?;
        loop {
            match self
                .process
                .as_mut()
                .ok_or(Failure("CORE_EXITED"))?
                .exited()?
            {
                Some(true) => break,
                Some(false) => return Err(Failure("CORE_EXITED")),
                None if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(10)),
                None => return Err(Failure("SHUTDOWN_TIMEOUT")),
            }
        }
        self.stopped = true;
        self.state("stopped", None);
        self.session = None;
        Ok(())
    }
    fn heartbeat(&mut self) -> Result<(), Failure> {
        if let Some(session) = &mut self.session {
            if self
                .process
                .as_mut()
                .ok_or(Failure("CORE_EXITED"))?
                .exited()?
                .is_some()
            {
                return Err(Failure("CORE_EXITED"));
            }
            session.exchange(
                "machine.ping",
                Instant::now() + Duration::from_secs(PROFILE.write_timeout_seconds),
            )?;
            self.state("ready", None);
        } else {
            // Drain diagnostics after a protocol failure without preserving raw stderr.
            let diagnostics = self.diagnostics.snapshot();
            if self.publication.snapshot().diagnostics != diagnostics {
                self.publication
                    .update(|snapshot| snapshot.diagnostics = diagnostics)?;
            }
        }
        Ok(())
    }
    fn run(mut self, operations: mpsc::Receiver<Operation>) {
        let interval = Duration::from_secs(2);
        let mut heartbeat = Instant::now() + interval;
        loop {
            if Instant::now() >= heartbeat {
                if let Err(failure) = self.heartbeat() {
                    self.fail(failure);
                }
                heartbeat = Instant::now() + interval;
            }
            let result = match operations
                .recv_timeout(heartbeat.saturating_duration_since(Instant::now()))
            {
                Ok(Operation::Start) => self.connect(),
                Ok(Operation::Stop) => self.stop(),
                Ok(Operation::Close) => {
                    let result = self.stop();
                    if result.is_ok() {
                        self.host.exit(0);
                        return;
                    }
                    result
                }
                Err(mpsc::RecvTimeoutError::Timeout) => continue,
                Err(mpsc::RecvTimeoutError::Disconnected) => return,
            };
            if let Err(failure) = result {
                self.fail(failure);
            }
        }
    }
}
