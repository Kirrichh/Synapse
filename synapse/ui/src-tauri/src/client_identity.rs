//! Per-instance application proof. Keys never enter renderer arguments or replies.
use crate::{bridge::Session, jcs, protocol::{Failure, PROFILE}};
use ed25519_dalek::{Signer, SigningKey};
use serde_json::json;
use std::time::{Duration, Instant};

pub struct ClientIdentity { key: SigningKey }

impl ClientIdentity {
    /// The native host supplies this stable storage name, never the renderer.
    /// Credential protection is user-bound, not isolation from arbitrary code
    /// already executing with that user's native credential access.
    pub fn open(target: &str) -> Result<Self, Failure> {
        #[cfg(windows)]
        { Ok(Self { key: windows::open(target)? }) }
        #[cfg(not(windows))]
        { let _ = target; Err(Failure("PROTECTED_STORAGE_UNAVAILABLE")) }
    }

    pub fn public_key(&self) -> String {
        self.key.verifying_key().as_bytes().iter().map(|b| format!("{b:02x}")).collect()
    }

    /// Only a schema-checked challenge received from the owned canonical core
    /// can be signed. There is deliberately no renderer-accessible signing API.
    pub fn authenticate(&self, session: &mut Session) -> Result<Result<String, &'static str>, Failure> {
        let deadline = Instant::now() + Duration::from_secs(PROFILE.write_timeout_seconds);
        let opened = match session.domain_exchange("access.open", json!({"profile":"synapse.application/v1"}), None, deadline)? {
            Ok(value) => value,
            Err(code) => return Ok(Err(code)),
        };
        let challenge = jcs::canonical_bytes(&opened["challenge"]).map_err(|_| Failure("PROTOCOL_ERROR"))?;
        let signature = self.key.sign(&challenge).to_bytes().iter().map(|b| format!("{b:02x}")).collect::<String>();
        let result = session.domain_exchange("access.authenticate", json!({"public_key":self.public_key(),
            "signature":signature}), None, deadline)?;
        match result {
            Ok(value) => Ok(Ok(value["application_id"].as_str()
                .ok_or(Failure("PROTOCOL_ERROR"))?.to_owned())),
            Err(code) => {
                session.domain_exchange("access.close", json!({}), None, deadline)?.map_err(Failure)?;
                Ok(Err(code))
            },
        }
    }
}

#[cfg(windows)]
mod windows {
    use super::*;
    use sha2::{Digest, Sha256};
    use windows_sys::Win32::{Foundation::{CloseHandle, GetLastError, HANDLE, ERROR_NOT_FOUND,
        WAIT_OBJECT_0, WAIT_ABANDONED}, Security::{Credentials::{CredReadW, CredWriteW, CredFree,
        CREDENTIALW, CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE}, Cryptography::{BCryptGenRandom,
        BCRYPT_USE_SYSTEM_PREFERRED_RNG}}, System::Threading::{CreateMutexW, WaitForSingleObject, ReleaseMutex}};
    use zeroize::{Zeroize, Zeroizing};

    struct Lock(HANDLE);
    impl Drop for Lock { fn drop(&mut self) { unsafe { ReleaseMutex(self.0); CloseHandle(self.0); } } }

    fn read(target: &[u16]) -> Result<Option<SigningKey>, Failure> {
        let mut credential = std::ptr::null_mut::<CREDENTIALW>();
        if unsafe { CredReadW(target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut credential) } == 0 {
            return if unsafe { GetLastError() } == ERROR_NOT_FOUND { Ok(None) }
                else { Err(Failure("PROTECTED_STORAGE_FAILED")) };
        }
        if credential.is_null() { return Err(Failure("PROTECTED_STORAGE_FAILED")); }
        let result = unsafe {
            let item = &*credential;
            if item.Type != CRED_TYPE_GENERIC || item.Persist != CRED_PERSIST_LOCAL_MACHINE
                || item.CredentialBlobSize != 37 || item.CredentialBlob.is_null() {
                Err(Failure("PROTECTED_STORAGE_FAILED"))
            } else {
                let bytes = std::slice::from_raw_parts_mut(item.CredentialBlob, 37);
                let result = if &bytes[..5] != b"SYNI\x01" { Err(Failure("PROTECTED_STORAGE_FAILED")) }
                    else { Ok(Some(SigningKey::from_bytes(bytes[5..].try_into().expect("fixed secret length")))) };
                bytes.zeroize();
                result
            }
        };
        unsafe { CredFree(credential.cast()); }
        result
    }

    pub(super) fn open(target: &str) -> Result<SigningKey, Failure> {
        if !target.starts_with("Synapse/Instance/v1/") || target.len() > 256 || target.contains('\0') {
            return Err(Failure("PROTECTED_STORAGE_FAILED"));
        }
        let name = format!("Local\\Synapse.Instance.{:x}", Sha256::digest(target.as_bytes()));
        let name: Vec<u16> = name.encode_utf16().chain(Some(0)).collect();
        let handle = unsafe { CreateMutexW(std::ptr::null(), 0, name.as_ptr()) };
        if handle.is_null() { return Err(Failure("PROTECTED_STORAGE_FAILED")); }
        let wait = unsafe { WaitForSingleObject(handle, 5000) };
        if wait != WAIT_OBJECT_0 && wait != WAIT_ABANDONED {
            unsafe { CloseHandle(handle); }
            return Err(Failure("PROTECTED_STORAGE_FAILED"));
        }
        let _lock = Lock(handle);
        let mut target: Vec<u16> = target.encode_utf16().chain(Some(0)).collect();
        if let Some(key) = read(&target)? { return Ok(key); }
        let mut blob = Zeroizing::new([0u8; 37]);
        blob[..5].copy_from_slice(b"SYNI\x01");
        if unsafe { BCryptGenRandom(std::ptr::null_mut(), blob[5..].as_mut_ptr(), 32,
            BCRYPT_USE_SYSTEM_PREFERRED_RNG) } < 0 { return Err(Failure("PROTECTED_STORAGE_FAILED")); }
        let mut credential: CREDENTIALW = unsafe { std::mem::zeroed() };
        credential.Type = CRED_TYPE_GENERIC;
        credential.TargetName = target.as_mut_ptr();
        credential.CredentialBlobSize = blob.len() as u32;
        credential.CredentialBlob = blob.as_mut_ptr();
        credential.Persist = CRED_PERSIST_LOCAL_MACHINE;
        if unsafe { CredWriteW(&credential, 0) } == 0 { return Err(Failure("PROTECTED_STORAGE_FAILED")); }
        let key = read(&target)?.ok_or(Failure("PROTECTED_STORAGE_FAILED"))?;
        if key.as_bytes() != &blob[5..] { return Err(Failure("PROTECTED_STORAGE_FAILED")); }
        Ok(key)
    }
}
