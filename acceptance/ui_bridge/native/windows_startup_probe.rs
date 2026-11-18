//! External executable for debugger-driven faults in the real Windows launcher.
//! The child is an execution witness, not a replacement canonical machine entry.
#[cfg(windows)]
fn main() {
    use std::{io::{Read, Write}, path::PathBuf};
    use synapse_desktop::{bundle::CoreBundle, lifecycle};
    let executable = std::env::current_exe().unwrap();
    if std::env::args().any(|arg| arg == "-I") {
        std::fs::write(executable.with_extension("executed"), b"EXECUTED").unwrap();
        loop { std::thread::park(); }
    }
    let root: PathBuf = executable.parent().unwrap().into();
    match lifecycle::spawn(&CoreBundle { root, python: executable, runtime_version: "witness".into() }) {
        Ok(core) => {
            println!("{{\"result\":\"STARTED\",\"child\":{}}}", core.process.id());
            std::io::stdout().flush().unwrap();
            let mut stop = [0];
            let _ = std::io::stdin().read(&mut stop);
            drop(core);
        }
        Err(error) => println!("{{\"result\":\"{}\"}}", error.0),
    }
}

#[cfg(not(windows))]
fn main() { panic!("Windows-only external startup witness"); }
