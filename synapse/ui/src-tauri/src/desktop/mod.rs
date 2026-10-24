mod worker;
use crate::controller::{Controller, Operation};
use std::sync::Arc;
use tauri::{ipc::Channel, Manager, State, WebviewWindow};
use worker::DesktopHost;

fn trusted(window: &WebviewWindow) -> Result<(), &'static str> {
    let url = window.url().map_err(|_| "CALLER_REFUSED")?;
    crate::caller::authorize(window.label(), &url).map_err(|failure| failure.0)
}

#[tauri::command]
fn desktop_identity(window: WebviewWindow, worker: State<'_, Controller>) -> Result<String, &'static str> {
    trusted(&window)?;
    worker.public_key().map(str::to_owned)
}

#[tauri::command]
fn desktop_application_subscribe(window: WebviewWindow, worker: State<'_, Controller>, channel: Channel<String>)
    -> Result<String, &'static str> {
    trusted(&window)?;
    Ok(worker.application_delivery.subscribe(Arc::new(move |text| channel.send(text).map_err(|_| ()))))
}

#[tauri::command]
fn desktop_application_unsubscribe(window: WebviewWindow, worker: State<'_, Controller>, subscription_id: String)
    -> Result<(), &'static str> {
    trusted(&window)?;
    worker.application_delivery.unsubscribe(&subscription_id);
    Ok(())
}

#[tauri::command]
fn desktop_application_ack(window: WebviewWindow, worker: State<'_, Controller>, subscription_id: String, request_id: String)
    -> Result<(), &'static str> {
    trusted(&window)?;
    worker.application_delivery.acknowledge(&subscription_id, &request_id).map_err(|failure| failure.0)
}

#[tauri::command]
fn desktop_application(window: WebviewWindow, worker: State<'_, Controller>, subscription_id: String, request: String)
    -> Result<String, &'static str> {
    trusted(&window)?;
    let value = crate::jcs::decode(request.as_bytes()).map_err(|_| "COMMAND_INVALID")?;
    let operation = serde_json::from_value(value).map_err(|_| "COMMAND_INVALID")?;
    let delivery = worker.application_delivery.clone();
    let ticket = delivery.reserve(&subscription_id).map_err(|failure| failure.0)?;
    let result = match worker.application(ticket.clone(), operation) {
        Ok(result) => result,
        Err(code) => { delivery.refused_before_enqueue(&ticket); return Err(code); }
    };
    let request_id = ticket.request_id().to_owned();
    tauri::async_runtime::spawn_blocking(move || {
        let result = result.recv_timeout(std::time::Duration::from_secs(crate::protocol::PROFILE.application_timeout_seconds)).unwrap_or(Err("DELIVERY_TIMEOUT"));
        let _ = delivery.deliver(ticket, result);
    });
    Ok(request_id)
}

#[tauri::command]
fn desktop_subscribe(
    window: WebviewWindow,
    worker: State<'_, Controller>,
    channel: Channel<String>,
) -> Result<String, &'static str> {
    trusted(&window)?;
    Ok(worker.publication.subscribe(Arc::new(move |snapshot| {
        channel.send(snapshot).map_err(|_| ())
    })))
}
#[tauri::command]
fn desktop_ack(
    window: WebviewWindow,
    worker: State<'_, Controller>,
    subscription_id: String,
    sequence: u32,
) -> Result<(), &'static str> {
    trusted(&window)?;
    worker
        .publication
        .acknowledge(&subscription_id, sequence)
        .map_err(|failure| failure.0)
}
#[tauri::command]
fn desktop_start(window: WebviewWindow, worker: State<'_, Controller>) -> Result<(), &'static str> {
    trusted(&window)?;
    worker.request(Operation::Start)
}
#[tauri::command]
fn desktop_stop(window: WebviewWindow, worker: State<'_, Controller>) -> Result<(), &'static str> {
    trusted(&window)?;
    worker.request(Operation::Stop)
}
#[tauri::command]
fn desktop_force_close(
    window: WebviewWindow,
    worker: State<'_, Controller>,
) -> Result<(), &'static str> {
    trusted(&window)?;
    if worker.publication.snapshot().state != "fail" {
        return Err("CALLER_REFUSED");
    }
    // OS containment owns descendant cleanup after the shell exits. This does
    // not assert that remote operations were undone or did not happen.
    window.app_handle().exit(1);
    Ok(())
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            desktop_subscribe,
            desktop_ack,
            desktop_start,
            desktop_stop,
            desktop_force_close,
            desktop_identity,
            desktop_application,
            desktop_application_subscribe,
            desktop_application_ack,
            desktop_application_unsubscribe
        ])
        .setup(|app| {
            let resource = app.path().resource_dir()?.join("core");
            #[cfg(windows)]
            let application = {
                let identity = crate::client_identity::ClientIdentity::open(
                    &format!("Synapse/Instance/v1/{}", app.config().identifier))?;
                Some(crate::application::Application::new(identity, app.path().app_local_data_dir()?.join("intents")))
            };
            #[cfg(not(windows))]
            let application = None;
            app.manage(Controller::with_application(DesktopHost {
                app: app.handle().clone(),
                resource,
            }, application));
            let config = app.config().app.windows[0].clone();
            #[cfg(windows)]
            let (config, target, visible) = {
                let mut blank = config.clone();
                let tauri::WebviewUrl::App(path) = &config.url else {
                    return Err("desktop requires packaged content".into());
                };
                let origin = if config.use_https_scheme {
                    "https://tauri.localhost/"
                } else {
                    "http://tauri.localhost/"
                };
                let target = url::Url::parse(origin)?.join(&path.to_string_lossy())?;
                crate::caller::authorize("main", &target)
                    .map_err(|_| "desktop requires packaged content")?;
                blank.url = tauri::WebviewUrl::External(url::Url::parse("about:blank")?);
                blank.visible = false;
                (blank, target, config.visible)
            };
            let window = tauri::WebviewWindowBuilder::from_config(app, &config)?
                .on_navigation(|url| crate::caller::authorize("main", url).is_ok())
                .on_new_window(|_, _| tauri::webview::NewWindowResponse::Deny)
                .build()?;
            #[cfg(windows)]
            {
                window.with_webview(move |platform| {
                    let protected = crate::webview_network::install(&platform, config.use_https_scheme);
                    // Tauri holds the WebView dispatch lock in this callback.
                    // Re-entering its navigate/show methods here would deadlock.
                    let loaded = protected.and_then(|()| unsafe {
                        platform.controller().CoreWebView2()?.Navigate(
                            &windows::core::HSTRING::from(target.as_str()),
                        )
                    });
                    if loaded.is_err() {
                        // Never expose application content after incomplete admission setup.
                        eprintln!("desktop: NETWORK_GUARD_FAILED");
                        std::process::exit(1);
                    }
                })?;
                if visible {
                    window.show()?;
                }
            }
            #[cfg(not(windows))]
            let _ = window;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let worker = window.state::<Controller>();
                if let Err(code) = worker.request(Operation::Close) {
                    let _ = worker.publication.update(|snapshot| {
                        snapshot.state = "fail";
                        snapshot.code = Some(code);
                    });
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("desktop runtime failed");
}
