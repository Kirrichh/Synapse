//! Serial Windows native pipe fidelity with an external byte witness. The
//! witness is not a canonical core and is never copied into product resources.
#[cfg(windows)]
fn main() {
    use std::{io::{Read, Write}, time::Duration};
    use synapse_desktop::{bundle::CoreBundle, lifecycle, protocol::Failure, transport::{read_frame, PipeConnection}};
    if std::env::args().any(|arg| arg == "-I") {
        let mut bytes = Vec::new();
        std::io::stdin().read_to_end(&mut bytes).unwrap();
        std::io::stderr().write_all(b"separate diagnostic\r\n\t\\n").unwrap();
        let mut output = std::io::stdout().lock();
        for fragment in bytes.chunks(3) {
            if output.write_all(fragment).is_err() || output.flush().is_err() { break; }
        }
        return;
    }
    let executable = std::env::current_exe().unwrap();
    let bundle = CoreBundle { root: executable.parent().unwrap().into(), python: executable, runtime_version: "witness".into() };
    let payloads: Vec<Vec<u8>> = ["Привет🙂漢\n\r\n\t\\n", "{\"text\":\"\\n\\r\\n\\t\\\\n\"}"]
        .iter().map(|value| value.as_bytes().to_vec())
        .chain([10, 13, 26, 255, 256, 1024, 65536].map(|size| (0..size).map(|i| (i % 256) as u8).collect())).collect();
    let mut bytes = Vec::new();
    for payload in &payloads { bytes.extend((payload.len() as u32).to_be_bytes()); bytes.extend(payload); }
    for chunk in [1, 3, 4096] {
        let mut core = lifecycle::spawn(&bundle).unwrap();
        let mut errors = core.errors;
        let diagnostics = std::thread::spawn(move || {
            let mut bytes = Vec::new();
            errors.read_to_end(&mut bytes).unwrap();
            bytes
        });
        for fragment in bytes.chunks(chunk) { core.input.write_all(fragment).unwrap(); }
        core.input.flush().unwrap();
        drop(core.input);
        for payload in &payloads { assert_eq!(read_frame(&mut core.output, 65536).unwrap(), *payload); }
        let mut rest = Vec::new();
        core.output.read_to_end(&mut rest).unwrap();
        assert!(rest.is_empty(), "stdout has unframed contamination");
        assert_eq!(diagnostics.join().unwrap(), b"separate diagnostic\r\n\t\\n");
        drop(core.process);
    }
    let valid = br#"{"schema_version":"1.0","type":"request","request_id":"r1","method":"machine.ping","params":{}}"#;
    for (mut damage, expected, append) in [
        (0u32.to_be_bytes().to_vec(), "PROTOCOL_ERROR", true),
        (65537u32.to_be_bytes().to_vec(), "PROTOCOL_ERROR", true),
        (u32::MAX.to_be_bytes().to_vec(), "PROTOCOL_ERROR", true),
        (vec![0, 0], "CORE_EXITED", false),
        (vec![0, 0, 0, 10, b'{'], "PROTOCOL_ERROR", false),
    ] {
        if append { damage.extend((valid.len() as u32).to_be_bytes()); damage.extend(valid); }
        let mut core = lifecycle::spawn(&bundle).unwrap();
        core.input.write_all(&damage).unwrap();
        drop(core.input);
        let connection = PipeConnection::new(core.output, std::io::sink());
        assert_eq!(connection.receive(Duration::from_secs(5)), Err(Failure(expected)));
        assert_eq!(connection.receive(Duration::from_secs(1)), Err(Failure("CORE_EXITED")), "corrupted input was resynchronized");
        drop(core.process);
    }
    println!("{{\"fidelity_chunkings\":3,\"payloads_per_chunking\":9,\"damage_cases\":5,\"status\":\"PASS\"}}");
}

#[cfg(not(windows))]
fn main() { panic!("Windows-only native byte witness"); }
