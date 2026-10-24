//! OS-owned lifetime and serialized admission for a desktop session's core.
use crate::protocol::Failure;
use std::{
    mem::{size_of, zeroed},
    os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle},
};
use windows_sys::Win32::{
    Foundation::{HANDLE, WAIT_ABANDONED, WAIT_OBJECT_0},
    System::{
        JobObjects::*,
        Threading::{CreateMutexW, ReleaseMutex, WaitForSingleObject},
    },
};

pub fn wide(value: &std::ffi::OsStr) -> Vec<u16> {
    use std::os::windows::ffi::OsStrExt;
    value.encode_wide().chain(Some(0)).collect()
}

pub fn own(handle: HANDLE) -> Result<OwnedHandle, Failure> {
    if handle.is_null() {
        Err(Failure("CONTAINMENT_FAILED"))
    } else {
        Ok(unsafe { OwnedHandle::from_raw_handle(handle) })
    }
}

pub struct Admission(OwnedHandle);
impl Admission {
    pub fn acquire() -> Result<Self, Failure> {
        let name = wide(std::ffi::OsStr::new("Local\\Synapse.Desktop.Admission.v1"));
        let handle = own(unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) })?;
        match unsafe { WaitForSingleObject(handle.as_raw_handle(), 0) } {
            WAIT_OBJECT_0 | WAIT_ABANDONED => Ok(Self(handle)),
            _ => Err(Failure("BUSY")),
        }
    }
}
impl Drop for Admission {
    fn drop(&mut self) {
        unsafe {
            ReleaseMutex(self.0.as_raw_handle());
        }
    }
}

pub fn empty_job() -> Result<OwnedHandle, Failure> {
    let name = wide(std::ffi::OsStr::new("Local\\Synapse.Desktop.Core.v1"));
    let job = own(unsafe { CreateJobObjectW(std::ptr::null(), name.as_ptr()) })?;
    let mut accounting: JOBOBJECT_BASIC_ACCOUNTING_INFORMATION = unsafe { zeroed() };
    if unsafe {
        QueryInformationJobObject(
            job.as_raw_handle(),
            JobObjectBasicAccountingInformation,
            (&mut accounting as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
            size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
            std::ptr::null_mut(),
        )
    } == 0
    {
        return Err(Failure("CONTAINMENT_FAILED"));
    }
    if accounting.ActiveProcesses != 0 {
        return Err(Failure("RESTART_REQUIRED"));
    }
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
    // No breakaway flags. The parent owns the sole inheritable-lifetime boundary;
    // the job handle itself is never inherited or included in the child list.
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    if unsafe {
        SetInformationJobObject(
            job.as_raw_handle(),
            JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
    } == 0
    {
        return Err(Failure("CONTAINMENT_FAILED"));
    }
    Ok(job)
}
