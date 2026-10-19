//! User-bound protected application data; no plaintext fallback on failure.
use crate::protocol::Failure;

pub trait Protector: Send {
    fn protect(&self, clear: &[u8]) -> Result<Vec<u8>, Failure>;
    fn unprotect(&self, cipher: &[u8]) -> Result<Vec<u8>, Failure>;
}

pub struct UserProtectedData;

#[cfg(windows)]
impl UserProtectedData {
    fn transform(&self, input: &[u8], protect: bool) -> Result<Vec<u8>, Failure> {
        use windows_sys::Win32::{Foundation::LocalFree, Security::Cryptography::{
            CryptProtectData, CryptUnprotectData, CRYPT_INTEGER_BLOB, CRYPTPROTECT_UI_FORBIDDEN,
        }};
        if input.is_empty() || input.len() > 131072 { return Err(Failure("PROTECTED_STORAGE_FAILED")); }
        let source = CRYPT_INTEGER_BLOB { cbData: input.len() as u32, pbData: input.as_ptr().cast_mut() };
        let mut output = CRYPT_INTEGER_BLOB { cbData: 0, pbData: std::ptr::null_mut() };
        let ok = unsafe {
            if protect {
                CryptProtectData(&source, std::ptr::null(), std::ptr::null(), std::ptr::null(),
                    std::ptr::null(), CRYPTPROTECT_UI_FORBIDDEN, &mut output)
            } else {
                CryptUnprotectData(&source, std::ptr::null_mut(), std::ptr::null(), std::ptr::null(),
                    std::ptr::null(), CRYPTPROTECT_UI_FORBIDDEN, &mut output)
            }
        };
        if ok == 0 { return Err(Failure("PROTECTED_STORAGE_FAILED")); }
        let result = if output.cbData as usize > 131072 || output.pbData.is_null() {
            Err(Failure("PROTECTED_STORAGE_FAILED"))
        } else {
            Ok(unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize) }.to_vec())
        };
        unsafe { LocalFree(output.pbData.cast()); }
        result
    }
}

impl Protector for UserProtectedData {
    fn protect(&self, clear: &[u8]) -> Result<Vec<u8>, Failure> {
        #[cfg(windows)]
        { self.transform(clear, true) }
        #[cfg(not(windows))]
        { let _ = clear; Err(Failure("PROTECTED_STORAGE_UNAVAILABLE")) }
    }
    fn unprotect(&self, cipher: &[u8]) -> Result<Vec<u8>, Failure> {
        #[cfg(windows)]
        { self.transform(cipher, false) }
        #[cfg(not(windows))]
        { let _ = cipher; Err(Failure("PROTECTED_STORAGE_UNAVAILABLE")) }
    }
}
