//! Create suspended inside the Job, inherit only the three pipe ends, then resume.
use super::job::{own, wide};
use crate::{bundle::CoreBundle, protocol::Failure};
use std::{
    fs::File,
    mem::{size_of, zeroed},
    os::windows::io::{AsRawHandle, OwnedHandle},
};
use windows_sys::Win32::{
    Foundation::{SetHandleInformation, HANDLE, HANDLE_FLAG_INHERIT},
    Security::SECURITY_ATTRIBUTES,
    System::{Pipes::CreatePipe, Threading::*},
};

fn pipe() -> Result<(OwnedHandle, OwnedHandle), Failure> {
    let attributes = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: std::ptr::null_mut(),
        bInheritHandle: 1,
    };
    let (mut read, mut write) = (std::ptr::null_mut(), std::ptr::null_mut());
    if unsafe { CreatePipe(&mut read, &mut write, &attributes, 0) } == 0 {
        return Err(Failure("SPAWN_FAILED"));
    }
    Ok((own(read)?, own(write)?))
}

struct Attributes {
    storage: Vec<usize>,
}
impl Attributes {
    fn new(handles: &[HANDLE], jobs: &[HANDLE]) -> Result<Self, Failure> {
        let mut size = 0;
        unsafe {
            InitializeProcThreadAttributeList(std::ptr::null_mut(), 2, 0, &mut size);
        }
        if size == 0 {
            return Err(Failure("CONTAINMENT_FAILED"));
        }
        let mut storage = vec![0usize; size.div_ceil(size_of::<usize>())];
        if unsafe {
            InitializeProcThreadAttributeList(storage.as_mut_ptr().cast(), 2, 0, &mut size)
        } == 0
        {
            return Err(Failure("CONTAINMENT_FAILED"));
        }
        let attributes = Self { storage };
        if unsafe {
            UpdateProcThreadAttribute(
                attributes.pointer(),
                0,
                PROC_THREAD_ATTRIBUTE_HANDLE_LIST as usize,
                handles.as_ptr().cast(),
                std::mem::size_of_val(handles),
                std::ptr::null_mut(),
                std::ptr::null(),
            )
        } == 0
        {
            return Err(Failure("CONTAINMENT_FAILED"));
        }
        // Bind lifetime during creation: owner death before a later assignment
        // must not leave an unowned suspended process. Both slices outlive us.
        if unsafe {
            UpdateProcThreadAttribute(
                attributes.pointer(),
                0,
                PROC_THREAD_ATTRIBUTE_JOB_LIST as usize,
                jobs.as_ptr().cast(),
                std::mem::size_of_val(jobs),
                std::ptr::null_mut(),
                std::ptr::null(),
            )
        } == 0
        {
            return Err(Failure("CONTAINMENT_FAILED"));
        }
        Ok(attributes)
    }
    fn pointer(&self) -> LPPROC_THREAD_ATTRIBUTE_LIST {
        self.storage.as_ptr().cast_mut().cast()
    }
}
impl Drop for Attributes {
    fn drop(&mut self) {
        unsafe {
            DeleteProcThreadAttributeList(self.pointer());
        }
    }
}

struct Suspended {
    process: OwnedHandle,
    armed: bool,
}
impl Drop for Suspended {
    fn drop(&mut self) {
        if self.armed {
            unsafe {
                TerminateProcess(self.process.as_raw_handle(), 2);
                WaitForSingleObject(self.process.as_raw_handle(), 5000);
            }
        }
    }
}

pub struct Launched {
    pub handle: OwnedHandle,
    pub id: u32,
    pub input: File,
    pub output: File,
    pub errors: File,
}

pub fn launch(bundle: &CoreBundle, job: &OwnedHandle) -> Result<Launched, Failure> {
    let (child_in, input) = pipe()?;
    let (output, child_out) = pipe()?;
    let (errors, child_err) = pipe()?;
    for handle in [&input, &output, &errors] {
        if unsafe { SetHandleInformation(handle.as_raw_handle(), HANDLE_FLAG_INHERIT, 0) } == 0 {
            return Err(Failure("CONTAINMENT_FAILED"));
        }
    }
    let handles = [
        child_in.as_raw_handle(),
        child_out.as_raw_handle(),
        child_err.as_raw_handle(),
    ];
    let jobs = [job.as_raw_handle()];
    let attributes = Attributes::new(&handles, &jobs)?;
    let mut startup: STARTUPINFOEXW = unsafe { zeroed() };
    startup.StartupInfo.cb = size_of::<STARTUPINFOEXW>() as u32;
    startup.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
    startup.StartupInfo.hStdInput = handles[0];
    startup.StartupInfo.hStdOutput = handles[1];
    startup.StartupInfo.hStdError = handles[2];
    startup.lpAttributeList = attributes.pointer();
    let executable = wide(bundle.python.as_os_str());
    let mut command = wide(std::ffi::OsStr::new(&format!(
        "\"{}\" -I -B -m synapse machine",
        bundle.python.display()
    )));
    let directory = wide(bundle.root.as_os_str());
    let system_root = std::env::var_os("SystemRoot").ok_or(Failure("ENVIRONMENT_UNSUPPORTED"))?;
    let mut environment = wide(std::ffi::OsStr::new(&format!(
        "SystemRoot={}",
        system_root.to_string_lossy()
    )));
    environment.push(0);
    let mut info: PROCESS_INFORMATION = unsafe { zeroed() };
    if unsafe {
        CreateProcessW(
            executable.as_ptr(),
            command.as_mut_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            1,
            CREATE_SUSPENDED
                | CREATE_NO_WINDOW
                | EXTENDED_STARTUPINFO_PRESENT
                | CREATE_UNICODE_ENVIRONMENT,
            environment.as_ptr().cast(),
            directory.as_ptr(),
            &startup.StartupInfo,
            &mut info,
        )
    } == 0
    {
        return Err(Failure("SPAWN_FAILED"));
    }
    let mut suspended = Suspended {
        process: own(info.hProcess)?,
        armed: true,
    };
    let thread = own(info.hThread)?;
    if unsafe { ResumeThread(thread.as_raw_handle()) } == u32::MAX {
        return Err(Failure("SPAWN_FAILED"));
    }
    suspended.armed = false;
    // Transfer the process handle without a second lifetime owner.
    let suspended = std::mem::ManuallyDrop::new(suspended);
    // The armed guard no longer owns cleanup; read transfers its only handle.
    let handle = unsafe { std::ptr::read(&suspended.process) };
    Ok(Launched {
        handle,
        id: info.dwProcessId,
        input: input.into(),
        output: output.into(),
        errors: errors.into(),
    })
}
