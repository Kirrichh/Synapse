//! External host for actual agent/MCP launch paths inside production containment.
//! It is not the canonical core and never ships with desktop resources.
#[cfg(windows)]
fn main() {
    use std::{io::{BufRead, Read, Write}, process::{Command, Stdio}};
    use synapse_desktop::{bundle::CoreBundle, lifecycle};
    let mut input = std::io::BufReader::new(std::io::stdin());
    let mut configuration = String::new();
    input.read_line(&mut configuration).unwrap();
    let config: serde_json::Value = serde_json::from_str(&configuration).unwrap();
    if std::env::args().any(|arg| arg == "-I") {
        let status = Command::new(config["python"].as_str().unwrap())
            .args(["-B", config["script"].as_str().unwrap(), "--role", "owners", "--scratch", config["scratch"].as_str().unwrap()])
            .stdin(Stdio::null()).stdout(Stdio::inherit()).stderr(Stdio::inherit())
            .status().unwrap();
        std::process::exit(status.code().unwrap_or(2));
    }
    let executable = std::env::current_exe().unwrap();
    let mut core = lifecycle::spawn(&CoreBundle {
        root: executable.parent().unwrap().into(), python: executable, runtime_version: "witness".into()
    }).unwrap();
    core.input.write_all(configuration.as_bytes()).unwrap();
    core.input.flush().unwrap();
    let mut report = String::new();
    std::io::BufReader::new(core.output).read_line(&mut report).unwrap();
    let mut report: serde_json::Value = serde_json::from_str(&report).unwrap();
    report["native_child"] = serde_json::json!(core.process.id());
    println!("{report}");
    std::io::stdout().flush().unwrap();
    let mut stop = [0];
    let _ = input.read(&mut stop);
    drop(core.process);
}

#[cfg(not(windows))]
fn main() { panic!("Windows-only external runtime witness"); }
