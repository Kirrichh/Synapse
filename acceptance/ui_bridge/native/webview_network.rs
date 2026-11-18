use synapse_desktop::webview_network::local_resource;

#[test]
fn packaged_resources_and_ipc_use_only_the_configured_local_protocol() {
    for https in [false, true] {
        let scheme = if https { "https" } else { "http" };
        for host in ["tauri.localhost", "ipc.localhost"] {
            assert!(local_resource(&format!("{scheme}://{host}/assets/app.js"), https));
        }
        for uri in [
            "http://127.0.0.1/navigation", "https://example.invalid/",
            "http://tauri.localhost.evil.invalid/", "http://ipc.localhost:9000/",
            "http://user@tauri.localhost/", "http://tauri.localhost@evil.invalid/",
            "http://tauri.localhost./", "http://sub.tauri.localhost/",
            "file:///synthetic.html", "data:text/html,synthetic", "about:blank",
            "javascript:void(0)", "tauri://localhost/", "not a URL",
        ] {
            assert!(!local_resource(uri, https), "unexpected local admission: {uri}");
        }
        let other = if https { "http" } else { "https" };
        assert!(!local_resource(&format!("{other}://tauri.localhost/"), https));
    }
}
