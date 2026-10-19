pub mod bridge;
pub mod bundle;
pub mod caller;
pub mod controller;
pub mod diagnostics;
pub mod lifecycle;
pub mod protocol;
pub mod publication;
pub mod transport;
pub mod webview_network;

#[cfg(feature = "desktop")]
mod desktop;
#[cfg(feature = "desktop")]
pub use desktop::run;
