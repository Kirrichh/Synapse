//! Platform containment is established before the core can receive commands.
use crate::{bundle::CoreBundle, protocol::Failure};
use std::io::{Read, Write};

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "linux")]
pub use linux::{inspect_service, Process};
#[cfg(target_os = "windows")]
mod windows;
#[cfg(target_os = "windows")]
pub use windows::Process;

pub struct ManagedCore {
    pub process: Box<dyn RunningProcess>,
    pub input: Box<dyn Write + Send>,
    pub output: Box<dyn Read + Send>,
    pub errors: Box<dyn Read + Send>,
}

pub trait RunningProcess: Send {
    fn id(&self) -> u32;
    fn exited(&mut self) -> Result<Option<bool>, Failure>;
}
impl RunningProcess for Process {
    fn id(&self) -> u32 {
        self.id()
    }
    fn exited(&mut self) -> Result<Option<bool>, Failure> {
        self.exited()
    }
}

pub fn spawn(bundle: &CoreBundle) -> Result<ManagedCore, Failure> {
    Process::spawn(bundle)
}
