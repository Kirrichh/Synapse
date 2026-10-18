fn main() {
    #[cfg(feature = "desktop")]
    {
        use sha2::{Digest, Sha256};
        let manifest = "resources/core/bundle-manifest.json";
        println!("cargo:rerun-if-changed={manifest}");
        let bytes = std::fs::read(manifest).expect(
            "Build the bundled core with scripts/ui/build_core_bundle.py before the desktop",
        );
        println!(
            "cargo:rustc-env=SYNAPSE_CORE_MANIFEST_SHA256={:x}",
            Sha256::digest(bytes)
        );
        tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "desktop_subscribe",
                "desktop_ack",
                "desktop_start",
                "desktop_stop",
                "desktop_force_close",
            ]),
        ))
        .expect("Tauri capability generation failed");
    }
}
