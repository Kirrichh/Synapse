//! Durable native intent identity. These records are never execution authority.
use crate::{commands::{self, Scope}, jcs, protected_data::Protector, protocol::Failure};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{fs::{self, File, OpenOptions}, io::{Read, Write}, path::{Path, PathBuf}};
use uuid::Uuid;

const MAX_INTENTS: usize = 1024;
const MAX_CIPHER_BYTES: u64 = 131072;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct PendingIntent {
    pub schema_version: String,
    pub intent_id: String,
    pub command_id: String,
    pub scope: Scope,
    pub command: Option<Value>,
}

pub struct PreparedCommand {
    intent: PendingIntent,
    binding_digest: String,
}
impl PreparedCommand {
    pub fn intent_id(&self) -> &str { &self.intent.intent_id }
    pub fn scope(&self) -> &Scope { &self.intent.scope }
    pub fn command(&self) -> &Value { self.intent.command.as_ref().expect("prepared command") }
    pub fn binding_digest(&self) -> &str { &self.binding_digest }
}

pub struct IntentStore<P: Protector> {
    root: PathBuf,
    scope: Scope,
    protector: P,
    _lock: File,
}

fn ordinary(path: &Path) -> Result<(), Failure> {
    if let Ok(metadata) = fs::symlink_metadata(path) {
        if metadata.file_type().is_symlink() { return Err(Failure("INTENT_STORE_INVALID")); }
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if metadata.file_attributes() & 0x400 != 0 { return Err(Failure("INTENT_STORE_INVALID")); }
        }
    }
    Ok(())
}

impl<P: Protector> IntentStore<P> {
    pub fn open(root: &Path, scope: Scope, protector: P, agent_roots: &[PathBuf]) -> Result<Self, Failure> {
        scope.validate()?;
        if !root.is_absolute() || root.components().any(|part| matches!(part, std::path::Component::ParentDir)) {
            return Err(Failure("INTENT_STORE_INVALID"));
        }
        for ancestor in root.ancestors() { ordinary(ancestor)?; }
        // Check against execution roots before creating any application data.
        let mut ancestor = root;
        let mut tail = Vec::new();
        while !ancestor.exists() {
            tail.push(ancestor.file_name().ok_or(Failure("INTENT_STORE_INVALID"))?);
            ancestor = ancestor.parent().ok_or(Failure("INTENT_STORE_INVALID"))?;
        }
        let mut resolved = ancestor.canonicalize().map_err(|_| Failure("INTENT_STORE_INVALID"))?;
        for part in tail.iter().rev() { resolved.push(part); }
        for agent_root in agent_roots {
            let excluded = agent_root.canonicalize().map_err(|_| Failure("INTENT_STORE_INVALID"))?;
            if resolved.starts_with(&excluded) { return Err(Failure("INTENT_STORE_INVALID")); }
        }
        fs::create_dir_all(root).map_err(|_| Failure("INTENT_STORE_FAILED"))?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(root, fs::Permissions::from_mode(0o700)).map_err(|_| Failure("INTENT_STORE_FAILED"))?;
        }
        let root = root.canonicalize().map_err(|_| Failure("INTENT_STORE_FAILED"))?;
        ordinary(&root.join("owner.lock"))?;
        let lock = OpenOptions::new().create(true).truncate(false).read(true).write(true)
            .open(root.join("owner.lock")).map_err(|_| Failure("INTENT_STORE_FAILED"))?;
        lock.try_lock().map_err(|_| Failure("INTENT_STORE_BUSY"))?;
        Ok(Self { root, scope, protector, _lock: lock })
    }

    fn path(&self, id: &str) -> Result<PathBuf, Failure> {
        let uuid = Uuid::parse_str(id).map_err(|_| Failure("INTENT_UNKNOWN"))?;
        if uuid.to_string() != id { return Err(Failure("INTENT_UNKNOWN")); }
        Ok(self.root.join(format!("{id}.intent")))
    }

    fn load(&self, id: &str) -> Result<PendingIntent, Failure> {
        let path = self.path(id)?;
        ordinary(&path)?;
        let file = File::open(path).map_err(|_| Failure("INTENT_UNKNOWN"))?;
        if file.metadata().map_err(|_| Failure("INTENT_STORE_FAILED"))?.len() > MAX_CIPHER_BYTES {
            return Err(Failure("INTENT_STORE_INVALID"));
        }
        let mut cipher = Vec::new();
        file.take(MAX_CIPHER_BYTES + 1).read_to_end(&mut cipher).map_err(|_| Failure("INTENT_STORE_FAILED"))?;
        if cipher.len() as u64 > MAX_CIPHER_BYTES { return Err(Failure("INTENT_STORE_INVALID")); }
        let clear = self.protector.unprotect(&cipher)?;
        let value = jcs::decode(&clear).map_err(|_| Failure("INTENT_STORE_INVALID"))?;
        let intent: PendingIntent = serde_json::from_value(value).map_err(|_| Failure("INTENT_STORE_INVALID"))?;
        if intent.schema_version != "synapse.intent/v1" || intent.intent_id != id {
            return Err(Failure("INTENT_STORE_INVALID"));
        }
        if intent.scope != self.scope { return Err(Failure("SCOPE_REFUSED")); }
        if Uuid::parse_str(&intent.command_id).is_err() { return Err(Failure("INTENT_STORE_INVALID")); }
        if let Some(command) = &intent.command {
            commands::binding(&self.scope, command)?;
            if command["command_id"] != intent.command_id { return Err(Failure("INTENT_STORE_INVALID")); }
        }
        Ok(intent)
    }

    fn save(&self, intent: &PendingIntent) -> Result<(), Failure> {
        let clear = jcs::canonical_bytes(&serde_json::to_value(intent).map_err(|_| Failure("INTENT_STORE_INVALID"))?)
            .map_err(|_| Failure("INTENT_STORE_INVALID"))?;
        let cipher = self.protector.protect(&clear)?;
        if cipher.is_empty() || cipher.len() as u64 > MAX_CIPHER_BYTES { return Err(Failure("INTENT_STORE_INVALID")); }
        let destination = self.path(&intent.intent_id)?;
        ordinary(&destination)?;
        let temporary = self.root.join(format!("{}.pending", Uuid::new_v4()));
        let outcome = (|| {
            let mut file = File::create_new(&temporary).map_err(|_| Failure("INTENT_STORE_FAILED"))?;
            file.write_all(&cipher).map_err(|_| Failure("INTENT_STORE_FAILED"))?;
            file.sync_all().map_err(|_| Failure("INTENT_STORE_FAILED"))?;
            drop(file);
            #[cfg(windows)]
            {
                use std::os::windows::ffi::OsStrExt;
                use windows_sys::Win32::Storage::FileSystem::{MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH};
                let source: Vec<u16> = temporary.as_os_str().encode_wide().chain(Some(0)).collect();
                let target: Vec<u16> = destination.as_os_str().encode_wide().chain(Some(0)).collect();
                if unsafe { MoveFileExW(source.as_ptr(), target.as_ptr(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH) } == 0 {
                    return Err(Failure("INTENT_STORE_FAILED"));
                }
            }
            #[cfg(not(windows))]
            {
                fs::rename(&temporary, &destination).map_err(|_| Failure("INTENT_STORE_FAILED"))?;
                File::open(&self.root).and_then(|file| file.sync_all()).map_err(|_| Failure("INTENT_STORE_FAILED"))?;
            }
            Ok(())
        })();
        if outcome.is_err() { let _ = fs::remove_file(&temporary); }
        outcome
    }

    pub fn pending(&self) -> Result<Vec<PendingIntent>, Failure> {
        let mut ids = Vec::new();
        for item in fs::read_dir(&self.root).map_err(|_| Failure("INTENT_STORE_FAILED"))? {
            let path = item.map_err(|_| Failure("INTENT_STORE_FAILED"))?.path();
            if path.extension().is_some_and(|value| value == "intent") {
                ids.push(path.file_stem().and_then(|value| value.to_str()).ok_or(Failure("INTENT_STORE_INVALID"))?.to_owned());
                if ids.len() > MAX_INTENTS { return Err(Failure("INTENT_STORE_FULL")); }
            }
        }
        ids.sort();
        ids.iter().map(|id| self.load(id)).collect()
    }

    pub fn issue(&mut self) -> Result<String, Failure> {
        if self.pending()?.len() >= MAX_INTENTS { return Err(Failure("INTENT_STORE_FULL")); }
        let intent = PendingIntent { schema_version: "synapse.intent/v1".into(),
            intent_id: Uuid::new_v4().to_string(), command_id: Uuid::new_v4().to_string(),
            scope: self.scope.clone(), command: None };
        if self.path(&intent.intent_id)?.exists() { return Err(Failure("INTENT_STORE_INVALID")); }
        self.save(&intent)?;
        Ok(intent.intent_id)
    }

    /// Recover without a renderer draft or a write. An issued but unprepared
    /// intent has no command to send and cannot be mistaken for scoped absence.
    pub fn recover(&self, intent_id: &str) -> Result<Option<PreparedCommand>, Failure> {
        let intent = self.load(intent_id)?;
        let Some(command) = &intent.command else { return Ok(None); };
        let binding_digest = commands::binding_digest(&self.scope, command)?;
        Ok(Some(PreparedCommand { intent, binding_digest }))
    }

    pub fn prepare(&mut self, intent_id: &str, draft: &str) -> Result<PreparedCommand, Failure> {
        let mut intent = self.load(intent_id)?;
        let command = commands::issued_command(&self.scope, &intent.command_id, draft)?;
        if let Some(previous) = &intent.command {
            if jcs::canonical_bytes(previous).map_err(|_| Failure("INTENT_STORE_INVALID"))?
                != jcs::canonical_bytes(&command).map_err(|_| Failure("COMMAND_INVALID"))? {
                return Err(Failure("BINDING_CONFLICT"));
            }
        } else {
            intent.command = Some(command);
            // A write failure cannot return a sendable command. The original
            // identity remains recoverable even if the commit's reply is lost.
            self.save(&intent)?;
        }
        self.recover(intent_id)?.ok_or(Failure("INTENT_STORE_INVALID"))
    }
}
