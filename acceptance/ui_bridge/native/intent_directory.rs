//! Actual Windows directory lifetime, independent of encryption semantics.
#![cfg(windows)]
use std::fs;
use synapse_desktop::{commands::Scope, intent_store::IntentStore,
    protected_data::UserProtectedData};

fn scope() -> Scope {
    Scope { principal: "synthetic-operator".into(), workspace: "synthetic-workspace".into() }
}

#[test]
fn admitted_root_and_ancestors_cannot_be_replaced_until_store_closes() {
    let temp = tempfile::tempdir().unwrap();
    let parent = temp.path().join("application");
    let root = parent.join("pending");
    let mut store = IntentStore::open(&root, scope(), UserProtectedData, &[]).unwrap();
    let id = store.issue().unwrap();
    assert!(fs::rename(&root, parent.join("moved-pending")).is_err(),
        "an open store must retain its admitted directory object");
    assert!(fs::rename(&parent, temp.path().join("moved-application")).is_err(),
        "retaining only the leaf does not protect its ancestor path");
    assert_eq!(store.pending().unwrap()[0].intent_id, id);
    drop(store);
    fs::rename(&parent, temp.path().join("moved-application")).unwrap();
}

#[test]
fn failed_excluded_open_does_not_create_directories_or_hold_ancestor_handles() {
    let temp = tempfile::tempdir().unwrap();
    let agent = temp.path().join("agent");
    fs::create_dir(&agent).unwrap();
    assert!(IntentStore::open(&agent.join("pending"), scope(), UserProtectedData,
        &[agent.clone()]).is_err());
    assert!(!agent.join("pending").exists());
    fs::rename(&agent, temp.path().join("moved-agent")).unwrap();
}
