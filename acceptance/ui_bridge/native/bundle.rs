use serde_json::json;
use sha2::{Digest, Sha256};
use std::{fs, path::Path};
use synapse_desktop::bundle::verify;

fn manifest(root: &Path, changes: Option<serde_json::Value>) -> String {
    fs::create_dir_all(root.join("bin")).unwrap();
    fs::write(root.join("bin/core"), b"executable bytes").unwrap();
    let mut value = json!({"format":1,"target_os":std::env::consts::OS,"architecture":std::env::consts::ARCH,
        "runtime_version":"v1","python":"bin/core","files":{"bin/core":format!("{:x}",Sha256::digest(b"executable bytes"))}});
    if let Some(fields) = changes {
        for (key, value_) in fields.as_object().unwrap() {
            value[key] = value_.clone();
        }
    }
    let bytes = serde_json::to_vec(&value).unwrap();
    fs::write(root.join("bundle-manifest.json"), &bytes).unwrap();
    format!("{:x}", Sha256::digest(&bytes))
}
#[test]
fn trusted_inventory_binds_bytes_and_rejects_unlisted_startup_code() {
    let directory = tempfile::tempdir().unwrap();
    let root = directory.path();
    let digest = manifest(root, None);
    let bundle = verify(root, &digest).unwrap();
    assert_eq!(bundle.python, root.join("bin/core").canonicalize().unwrap());
    assert_eq!(bundle.runtime_version, "v1");
    assert!(verify(root, &"0".repeat(64)).is_err());
    fs::write(root.join("bin/core"), b"modified executable").unwrap();
    assert!(verify(root, &digest).is_err());
    fs::write(root.join("bin/core"), b"executable bytes").unwrap();
    fs::write(root.join("inject.pth"), b"import attacker").unwrap();
    assert!(verify(root, &digest).is_err());
}
#[test]
fn trusted_manifest_still_requires_platform_and_contained_executable() {
    for fields in [
        json!({"format":2}),
        json!({"target_os":"foreign"}),
        json!({"architecture":"foreign"}),
        json!({"runtime_version":""}),
        json!({"runtime_version":"x".repeat(129)}),
        json!({"extra":true}),
        json!({"python":"../escape","files":{"../escape":"abc"}}),
        json!({"python":"/absolute","files":{"/absolute":"abc"}}),
        json!({"python":"C:\\escape","files":{"C:\\escape":"abc"}}),
        json!({"python":"bin/missing"}),
        json!({"files":{}}),
    ] {
        let directory = tempfile::tempdir().unwrap();
        let digest = manifest(directory.path(), Some(fields));
        assert!(verify(directory.path(), &digest).is_err());
    }
}
#[cfg(unix)]
#[test]
fn inventory_does_not_follow_symlinks_even_to_identical_bytes() {
    let directory = tempfile::tempdir().unwrap();
    let digest = manifest(directory.path(), None);
    let outside = tempfile::NamedTempFile::new().unwrap();
    fs::write(outside.path(), b"executable bytes").unwrap();
    fs::remove_file(directory.path().join("bin/core")).unwrap();
    std::os::unix::fs::symlink(outside.path(), directory.path().join("bin/core")).unwrap();
    assert!(verify(directory.path(), &digest).is_err());
}
