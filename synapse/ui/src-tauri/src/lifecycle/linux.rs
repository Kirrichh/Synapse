use super::ManagedCore;
use crate::{bundle::CoreBundle, protocol::Failure};
use std::{
    collections::HashMap,
    fs,
    io::Read,
    process::{Child, Command, Stdio},
    sync::mpsc,
    thread,
    time::{Duration, Instant},
};

pub fn inspect_service(properties: &str, own_group: &str, pid: u32) -> Result<(), Failure> {
    let mut fields = HashMap::new();
    for line in properties.lines() {
        if let Some((key, value)) = line.split_once('=') {
            if fields.insert(key, value).is_some() {
                return Err(Failure("ENVIRONMENT_UNSUPPORTED"));
            }
        }
    }
    let expected_pid = pid.to_string();
    let required = [
        ("MainPID", expected_pid.as_str()),
        ("ActiveState", "active"),
        ("KillMode", "control-group"),
        ("ExitType", "main"),
        ("Restart", "no"),
        ("SendSIGKILL", "yes"),
        ("TimeoutStopUSec", "5s"),
        ("ControlGroup", own_group),
    ];
    if !own_group.starts_with('/')
        || !own_group.ends_with("/synapse-desktop.service")
        || own_group.split('/').any(|part| part == "..")
        || required
            .iter()
            .any(|(key, value)| fields.get(key) != Some(value))
    {
        return Err(Failure("ENVIRONMENT_UNSUPPORTED"));
    }
    Ok(())
}

fn environment() -> Result<(), Failure> {
    if !std::path::Path::new("/sys/fs/cgroup/cgroup.controllers").is_file() {
        return Err(Failure("ENVIRONMENT_UNSUPPORTED"));
    }
    let groups =
        fs::read_to_string("/proc/self/cgroup").map_err(|_| Failure("ENVIRONMENT_UNSUPPORTED"))?;
    let own = groups
        .lines()
        .find_map(|line| line.strip_prefix("0::"))
        .ok_or(Failure("ENVIRONMENT_UNSUPPORTED"))?;
    let mut child = Command::new("/usr/bin/systemctl").args(["--user", "show", "synapse-desktop.service",
        "--no-pager", "--property=MainPID,ActiveState,KillMode,ExitType,Restart,SendSIGKILL,TimeoutStopUSec,ControlGroup"])
        .stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null())
        .spawn().map_err(|_| Failure("ENVIRONMENT_UNSUPPORTED"))?;
    let output = child
        .stdout
        .take()
        .ok_or(Failure("ENVIRONMENT_UNSUPPORTED"))?;
    let (sender, receiver) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let mut bytes = Vec::new();
        let result = output.take(16385).read_to_end(&mut bytes).map(|_| bytes);
        let _ = sender.send(result);
    });
    let deadline = Instant::now() + Duration::from_secs(2);
    let success = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status.success(),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                break false;
            }
        }
    };
    let bytes = receiver
        .recv_timeout(Duration::from_millis(100))
        .map_err(|_| Failure("ENVIRONMENT_UNSUPPORTED"))?
        .map_err(|_| Failure("ENVIRONMENT_UNSUPPORTED"))?;
    if !success || bytes.len() > 16384 {
        return Err(Failure("ENVIRONMENT_UNSUPPORTED"));
    }
    let properties = std::str::from_utf8(&bytes).map_err(|_| Failure("ENVIRONMENT_UNSUPPORTED"))?;
    inspect_service(properties, own, std::process::id())
}

pub struct Process(Child);
impl Process {
    pub fn spawn(bundle: &CoreBundle) -> Result<ManagedCore, Failure> {
        environment()?;
        let mut child = Command::new(&bundle.python)
            .args(["-I", "-B", "-m", "synapse", "machine"])
            .env_clear()
            .env("LANG", "C.UTF-8")
            .env("PATH", "/usr/bin:/bin")
            .current_dir(&bundle.root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|_| Failure("SPAWN_FAILED"))?;
        Ok(ManagedCore {
            input: Box::new(child.stdin.take().ok_or(Failure("SPAWN_FAILED"))?),
            output: Box::new(child.stdout.take().ok_or(Failure("SPAWN_FAILED"))?),
            errors: Box::new(child.stderr.take().ok_or(Failure("SPAWN_FAILED"))?),
            process: Box::new(Process(child)),
        })
    }
    pub fn id(&self) -> u32 {
        self.0.id()
    }
    pub fn exited(&mut self) -> Result<Option<bool>, Failure> {
        self.0
            .try_wait()
            .map(|state| state.map(|status| status.success()))
            .map_err(|_| Failure("CORE_EXITED"))
    }
}
