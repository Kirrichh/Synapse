use synapse_desktop::caller::authorize;
#[test]
fn only_packaged_main_window_has_control_authority() {
    for url in [
        "tauri://localhost/index.html",
        "http://tauri.localhost/",
        "https://tauri.localhost/",
    ] {
        let url = url::Url::parse(url).unwrap();
        assert!(authorize("main", &url).is_ok());
        assert!(authorize("agent-output", &url).is_err());
    }
    for url in [
        "http://localhost/",
        "http://tauri.localhost.evil/",
        "https://example.com/",
        "tauri://evil/",
        "file:///index.html",
        "data:text/html,evil",
        "https://user@tauri.localhost/",
        "https://tauri.localhost:8443/",
        "https://user:password@tauri.localhost/",
    ] {
        assert!(
            authorize("main", &url::Url::parse(url).unwrap()).is_err(),
            "{url}"
        );
    }
}
#[cfg(target_os = "linux")]
#[test]
fn linux_requires_actual_main_pid_and_group_cleanup_properties() {
    use synapse_desktop::lifecycle::inspect_service;
    let group = "/user.slice/user-1000.slice/user@1000.service/app.slice/synapse-desktop.service";
    let valid=format!("MainPID=123\nActiveState=active\nKillMode=control-group\nExitType=main\nRestart=no\nSendSIGKILL=yes\nTimeoutStopUSec=5s\nControlGroup={group}\n");
    assert!(inspect_service(&valid, group, 123).is_ok());
    for (old, new) in [
        ("MainPID=123", "MainPID=124"),
        ("active", "inactive"),
        ("control-group", "process"),
        ("ExitType=main", "ExitType=cgroup"),
        ("Restart=no", "Restart=always"),
        ("SendSIGKILL=yes", "SendSIGKILL=no"),
        ("TimeoutStopUSec=5s", "TimeoutStopUSec=infinity"),
        ("ControlGroup=", "ControlGroup=/other"),
    ] {
        assert!(
            inspect_service(&valid.replace(old, new), group, 123).is_err(),
            "{old}"
        );
    }
    assert!(inspect_service(&(valid.clone() + "MainPID=123\n"), group, 123).is_err());
    assert!(inspect_service(&valid, "/../synapse-desktop.service", 123).is_err());
}
#[cfg(target_os = "linux")]
#[test]
fn direct_launch_without_the_service_is_refused_before_core_start() {
    let directory = tempfile::tempdir().unwrap();
    let bundle = synapse_desktop::bundle::CoreBundle {
        root: directory.path().into(),
        python: directory.path().join("unstarted"),
        runtime_version: "v1".into(),
    };
    let failure = match synapse_desktop::lifecycle::spawn(&bundle) {
        Err(failure) => failure,
        Ok(_) => panic!("process launched outside its required systemd main service"),
    };
    assert_eq!(failure.0, "ENVIRONMENT_UNSUPPORTED");
}
