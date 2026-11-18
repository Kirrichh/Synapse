//! Verify a build-bound core inventory before executing its bundled interpreter.
use crate::protocol::Failure;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::Read,
    path::{Component, Path, PathBuf},
};

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    format: u32,
    target_os: String,
    architecture: String,
    runtime_version: String,
    python: String,
    files: BTreeMap<String, String>,
}

pub struct CoreBundle {
    pub root: PathBuf,
    pub python: PathBuf,
    pub runtime_version: String,
}

fn contained(root: &Path, relative: &str) -> Result<PathBuf, Failure> {
    if relative.is_empty() || relative.contains('\\') || relative.contains(':') {
        return Err(Failure("CORE_INTEGRITY"));
    }
    let path = Path::new(relative);
    if path
        .components()
        .any(|part| !matches!(part, Component::Normal(_)))
    {
        return Err(Failure("CORE_INTEGRITY"));
    }
    let mut full = root.to_path_buf();
    for part in path.components() {
        full.push(part);
        if fs::symlink_metadata(&full)
            .map_err(|_| Failure("CORE_MISSING"))?
            .file_type()
            .is_symlink()
        {
            return Err(Failure("CORE_INTEGRITY"));
        }
    }
    if !full
        .canonicalize()
        .map_err(|_| Failure("CORE_MISSING"))?
        .starts_with(root)
    {
        return Err(Failure("CORE_INTEGRITY"));
    }
    Ok(full)
}

pub fn verify(root: &Path, expected_manifest_digest: &str) -> Result<CoreBundle, Failure> {
    let root = root.canonicalize().map_err(|_| Failure("CORE_MISSING"))?;
    let manifest_path = contained(&root, "bundle-manifest.json")?;
    if fs::metadata(&manifest_path)
        .map_err(|_| Failure("CORE_MISSING"))?
        .len()
        > 2 * 1024 * 1024
    {
        return Err(Failure("CORE_INTEGRITY"));
    }
    let mut bytes = Vec::new();
    fs::File::open(&manifest_path)
        .map_err(|_| Failure("CORE_MISSING"))?
        .take(2 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| Failure("CORE_MISSING"))?;
    if bytes.len() > 2 * 1024 * 1024 {
        return Err(Failure("CORE_INTEGRITY"));
    }
    if format!("{:x}", Sha256::digest(&bytes)) != expected_manifest_digest {
        return Err(Failure("CORE_INTEGRITY"));
    }
    let manifest: Manifest =
        serde_json::from_slice(&bytes).map_err(|_| Failure("CORE_INTEGRITY"))?;
    if manifest.format != 1
        || manifest.target_os != std::env::consts::OS
        || manifest.architecture != std::env::consts::ARCH
        || manifest.runtime_version.is_empty()
        || manifest.runtime_version.len() > 128
        || !manifest.files.contains_key(&manifest.python)
        || manifest.files.is_empty()
        || manifest.files.len() > 8192
    {
        return Err(Failure("CORE_INTEGRITY"));
    }
    let mut total = 0u64;
    for (relative, expected) in &manifest.files {
        let path = contained(&root, relative)?;
        let mut file = fs::File::open(path).map_err(|_| Failure("CORE_MISSING"))?;
        let info = file.metadata().map_err(|_| Failure("CORE_INTEGRITY"))?;
        total = total
            .checked_add(info.len())
            .ok_or(Failure("CORE_INTEGRITY"))?;
        if !info.is_file() || info.len() > 128 * 1024 * 1024 || total > 512 * 1024 * 1024 {
            return Err(Failure("CORE_INTEGRITY"));
        }
        let mut hash = Sha256::new();
        let mut buffer = [0u8; 65536];
        let mut observed = 0u64;
        loop {
            let count = file
                .read(&mut buffer)
                .map_err(|_| Failure("CORE_INTEGRITY"))?;
            if count == 0 {
                break;
            }
            observed += count as u64;
            if observed > info.len() {
                return Err(Failure("CORE_INTEGRITY"));
            }
            hash.update(&buffer[..count]);
        }
        if observed != info.len() || format!("{:x}", hash.finalize()) != *expected {
            return Err(Failure("CORE_INTEGRITY"));
        }
    }
    // Extra modules or .pth files could alter startup without changing inventoried files.
    let mut pending = vec![root.clone()];
    let mut entries = 0;
    while let Some(directory) = pending.pop() {
        for entry in fs::read_dir(directory).map_err(|_| Failure("CORE_INTEGRITY"))? {
            let entry = entry.map_err(|_| Failure("CORE_INTEGRITY"))?;
            entries += 1;
            if entries > 16384 {
                return Err(Failure("CORE_INTEGRITY"));
            }
            let kind = entry.file_type().map_err(|_| Failure("CORE_INTEGRITY"))?;
            if kind.is_dir() {
                pending.push(entry.path());
            } else if kind.is_file() {
                let path = entry.path();
                let relative = path
                    .strip_prefix(&root)
                    .map_err(|_| Failure("CORE_INTEGRITY"))?
                    .to_str()
                    .ok_or(Failure("CORE_INTEGRITY"))?
                    .replace('\\', "/");
                if relative != "bundle-manifest.json" && !manifest.files.contains_key(&relative) {
                    return Err(Failure("CORE_INTEGRITY"));
                }
            } else {
                return Err(Failure("CORE_INTEGRITY"));
            }
        }
    }
    Ok(CoreBundle {
        python: contained(&root, &manifest.python)?,
        root,
        runtime_version: manifest.runtime_version,
    })
}
