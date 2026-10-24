//! Tauri host binds the controller to packaged resources and OS containment.
use crate::{bundle, controller::Host, lifecycle, protocol::Failure};
use std::path::PathBuf;
use tauri::AppHandle;

pub struct DesktopHost {
    pub app: AppHandle,
    pub resource: PathBuf,
}
impl Host for DesktopHost {
    fn launch(&mut self) -> Result<(lifecycle::ManagedCore, String), Failure> {
        let bundle = bundle::verify(&self.resource, env!("SYNAPSE_CORE_MANIFEST_SHA256"))?;
        let process = lifecycle::spawn(&bundle)?;
        Ok((process, bundle.runtime_version))
    }
    fn exit(&self, code: i32) {
        self.app.exit(code);
    }
}
