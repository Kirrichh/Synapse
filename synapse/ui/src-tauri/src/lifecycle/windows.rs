mod job;
mod launch;
use super::ManagedCore;
use crate::{bundle::CoreBundle, protocol::Failure};
use std::os::windows::io::{AsRawHandle, OwnedHandle};
use windows_sys::Win32::{
    Foundation::{WAIT_OBJECT_0, WAIT_TIMEOUT},
    System::Threading::{GetExitCodeProcess, WaitForSingleObject},
};

pub struct Process {
    handle: OwnedHandle,
    id: u32,
    _job: OwnedHandle,
}
impl Process {
    pub fn spawn(bundle: &CoreBundle) -> Result<ManagedCore, Failure> {
        let _admission = job::Admission::acquire()?;
        let job = job::empty_job()?;
        let launched = launch::launch(bundle, &job)?;
        Ok(ManagedCore {
            process: Box::new(Self {
                handle: launched.handle,
                id: launched.id,
                _job: job,
            }),
            input: Box::new(launched.input),
            output: Box::new(launched.output),
            errors: Box::new(launched.errors),
        })
    }
    pub fn id(&self) -> u32 {
        self.id
    }
    pub fn exited(&mut self) -> Result<Option<bool>, Failure> {
        match unsafe { WaitForSingleObject(self.handle.as_raw_handle(), 0) } {
            WAIT_TIMEOUT => Ok(None),
            WAIT_OBJECT_0 => {
                let mut code = 0;
                if unsafe { GetExitCodeProcess(self.handle.as_raw_handle(), &mut code) } == 0 {
                    return Err(Failure("CORE_EXITED"));
                }
                Ok(Some(code == 0))
            }
            _ => Err(Failure("CORE_EXITED")),
        }
    }
}
