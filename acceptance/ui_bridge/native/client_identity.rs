#![cfg(windows)]
use synapse_desktop::client_identity::ClientIdentity;
use windows_sys::Win32::Security::Credentials::{CredDeleteW, CredWriteW, CREDENTIALW,
    CRED_TYPE_GENERIC, CRED_PERSIST_LOCAL_MACHINE};

struct Credential(String);
impl Credential {
    fn new() -> Self { Self(format!("Synapse/Instance/v1/acceptance-{}", tempfile::NamedTempFile::new().unwrap().path().file_name().unwrap().to_string_lossy())) }
    fn wide(&self) -> Vec<u16> { self.0.encode_utf16().chain(Some(0)).collect() }
}
impl Drop for Credential {
    fn drop(&mut self) { unsafe { CredDeleteW(self.wide().as_ptr(), CRED_TYPE_GENERIC, 0); } }
}

#[test]
fn actual_windows_credential_survives_restart_and_concurrent_open() {
    let target = Credential::new();
    let workers: Vec<_> = (0..4).map(|_| {
        let name = target.0.clone();
        std::thread::spawn(move || ClientIdentity::open(&name).unwrap().public_key())
    }).collect();
    let keys: Vec<_> = workers.into_iter().map(|worker| worker.join().unwrap()).collect();
    let first = keys[0].clone();
    assert_eq!(first.len(), 64);
    for key in keys { assert_eq!(key, first); }
    assert_eq!(ClientIdentity::open(&target.0).unwrap().public_key(), first);
    let other = Credential::new();
    assert_ne!(ClientIdentity::open(&other.0).unwrap().public_key(), first);
}

#[test]
fn malformed_existing_identity_is_not_replaced_or_treated_as_absence() {
    let target = Credential::new();
    let mut name = target.wide();
    let mut broken = b"not-an-instance-key".to_vec();
    let mut credential: CREDENTIALW = unsafe { std::mem::zeroed() };
    credential.Type = CRED_TYPE_GENERIC;
    credential.TargetName = name.as_mut_ptr();
    credential.Persist = CRED_PERSIST_LOCAL_MACHINE;
    credential.CredentialBlob = broken.as_mut_ptr();
    credential.CredentialBlobSize = broken.len() as u32;
    assert_ne!(unsafe { CredWriteW(&credential, 0) }, 0);
    assert!(ClientIdentity::open(&target.0).is_err());
    assert!(ClientIdentity::open(&target.0).is_err());
    let mut wrong_tag = [0u8;37];
    credential.CredentialBlob = wrong_tag.as_mut_ptr();
    credential.CredentialBlobSize = 37;
    assert_ne!(unsafe { CredWriteW(&credential, 0) }, 0);
    assert!(ClientIdentity::open(&target.0).is_err());
    wrong_tag[..5].copy_from_slice(b"SYNI\x01");
    credential.Persist = 1; // Session-only storage cannot establish durable identity.
    assert_ne!(unsafe { CredWriteW(&credential, 0) }, 0);
    assert!(ClientIdentity::open(&target.0).is_err());
    assert!(ClientIdentity::open("unrelated-credential").is_err());
}
