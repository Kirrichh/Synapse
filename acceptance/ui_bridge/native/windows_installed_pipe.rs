//! External byte relay to the verified installed canonical core through the
//! production Windows launcher. No substituted peer or product test entry.
#[cfg(windows)]
fn main() {
    use std::{io::{Read, Write}, path::Path, sync::mpsc, thread, time::Duration};
    use synapse_desktop::{bundle, lifecycle};
    let arguments: Vec<_> = std::env::args().collect();
    assert_eq!(arguments.len(), 3, "expected installed resource root and manifest digest");
    let release = bundle::verify(Path::new(&arguments[1]), &arguments[2]).expect("installed inventory");
    let core = lifecycle::spawn(&release).expect("production native launch");
    let mut owner = core.process;
    let mut input = core.input;
    thread::spawn(move || {
        let mut source = std::io::stdin().lock();
        let mut bytes = [0; 4096];
        while let Ok(size) = source.read(&mut bytes) {
            if size == 0 || input.write_all(&bytes[..size]).is_err() || input.flush().is_err() { break; }
        }
    });
    let (finished, drained) = mpsc::channel();
    let mut output = core.output;
    thread::spawn(move || {
        let mut destination = std::io::stdout().lock();
        let mut bytes = [0; 4096];
        while let Ok(size) = output.read(&mut bytes) {
            if size == 0 || destination.write_all(&bytes[..size]).is_err() || destination.flush().is_err() { break; }
        }
        let _ = finished.send(());
    });
    let mut errors = core.errors;
    let diagnostics = thread::spawn(move || {
        let mut destination = std::io::stderr().lock();
        std::io::copy(&mut errors, &mut destination).expect("diagnostic relay");
        destination.flush().unwrap();
    });
    let success = loop {
        if let Some(success) = owner.exited().expect("core exit observation") { break success; }
        thread::sleep(Duration::from_millis(10));
    };
    diagnostics.join().unwrap();
    // The unread-output scenario deliberately blocks our outer pipe too.
    // Preserve normal replies without making a dead core depend on that reader.
    let _ = drained.recv_timeout(Duration::from_millis(500));
    drop(owner);
    std::process::exit(if success { 0 } else { 2 });
}

#[cfg(not(windows))]
fn main() { panic!("Windows-only installed native pipe acceptance"); }
