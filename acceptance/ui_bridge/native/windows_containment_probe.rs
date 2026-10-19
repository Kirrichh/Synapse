//! External witness for the real native launcher; never packaged with the product.
#[cfg(windows)]
fn main() {
    use std::{io::{BufRead, Write}, os::windows::io::{FromRawHandle, OwnedHandle, AsRawHandle}, process::{Command, Stdio}};
    use windows_sys::Win32::{Foundation::*, System::{JobObjects::*, Threading::*, SystemServices::JOB_OBJECT_QUERY}};
    let executable = std::env::current_exe().unwrap();
    let arguments: Vec<_> = std::env::args().collect();
    if arguments.iter().any(|argument| argument == "--descendant") {
        loop { std::thread::park(); }
    }
    if arguments.iter().any(|argument| argument == "--owner") {
        let bundle = synapse_desktop::bundle::CoreBundle {
            root: executable.parent().unwrap().into(), python: executable.clone(), runtime_version: "witness".into()
        };
        let core = synapse_desktop::lifecycle::spawn(&bundle).unwrap();
        let mut input = core.input;
        writeln!(input, "0").unwrap();
        input.flush().unwrap();
        let mut report = String::new();
        std::io::BufReader::new(core.output).read_line(&mut report).unwrap();
        print!("{report}");
        std::io::stdout().flush().unwrap();
        // Keep ownership alive until the external acceptance kills only this owner.
        loop { std::thread::park(); }
    }
    let name: Vec<u16> = "Local\\Synapse.Desktop.Core.v1\0".encode_utf16().collect();
    let job = unsafe { OpenJobObjectW(JOB_OBJECT_QUERY, 0, name.as_ptr()) };
    let mut in_product_job = 0;
    if !job.is_null() {
        let handle = unsafe { OwnedHandle::from_raw_handle(job) };
        assert_ne!(unsafe { IsProcessInJob(GetCurrentProcess(), handle.as_raw_handle(), &mut in_product_job) }, 0);
    }
    let mut message = String::new();
    std::io::stdin().read_line(&mut message).unwrap();
    let sentinel: usize = message.trim().parse().unwrap();
    let inherited_event = sentinel != 0 && unsafe { SetEvent(sentinel as HANDLE) } != 0;
    let descendant = Command::new(&executable).arg("--descendant")
        .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).spawn().unwrap();
    println!("{}", serde_json::json!({"core": std::process::id(), "descendant": descendant.id(),
        "in_product_job": in_product_job != 0, "inherited_event": inherited_event}));
    std::io::stdout().flush().unwrap();
    loop { std::thread::park(); }
}

#[cfg(not(windows))]
fn main() { panic!("Windows-only external containment witness"); }
