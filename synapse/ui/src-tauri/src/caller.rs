//! The native WebView supplies origin and label; renderer claims are irrelevant.
use crate::protocol::Failure;
pub fn authorize(label: &str, url: &url::Url) -> Result<(), Failure> {
    let local = (url.scheme() == "tauri" && url.host_str() == Some("localhost"))
        || ((url.scheme() == "http" || url.scheme() == "https")
            && url.host_str() == Some("tauri.localhost"));
    if label != "main"
        || !local
        || url.port().is_some()
        || !url.username().is_empty()
        || url.password().is_some()
    {
        return Err(Failure("CALLER_REFUSED"));
    }
    Ok(())
}
