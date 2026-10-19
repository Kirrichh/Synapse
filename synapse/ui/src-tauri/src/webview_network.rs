//! Native request admission, separate from navigation and renderer CSP.
//! Only the two Tauri custom-protocol endpoints may reach their local handlers.

pub fn local_resource(uri: &str, https: bool) -> bool {
    let Ok(url) = url::Url::parse(uri) else {
        return false;
    };
    url.scheme() == if https { "https" } else { "http" }
        && matches!(url.host_str(), Some("tauri.localhost" | "ipc.localhost"))
        && url.port().is_none()
        && url.username().is_empty()
        && url.password().is_none()
}

#[cfg(all(windows, feature = "desktop"))]
pub fn install(
    platform: &tauri::webview::PlatformWebview,
    https: bool,
) -> windows::core::Result<()> {
    use webview2_com::{
        take_pwstr, Microsoft::Web::WebView2::Win32::*,
        WebResourceRequestedEventHandler,
    };
    use windows::{core::{w, Interface, PWSTR}, Win32::System::Com::IStream};

    // The caller creates only about:blank until this complete setup succeeds.
    // Require the filter that includes workers; an older runtime must refuse
    // startup instead of silently falling back to document-only interception.
    unsafe {
        let controller = platform.controller();
        let webview = controller.CoreWebView2()?;
        let requests = webview.cast::<ICoreWebView2_22>()?;
        let denied = platform.environment().CreateWebResourceResponse(
            None::<&IStream>, 403, w!("Forbidden"),
            w!("Content-Length: 0\r\nCache-Control: no-store\r\n"),
        )?;
        let mut token = 0;
        webview.add_WebResourceRequested(
            &WebResourceRequestedEventHandler::create(Box::new(move |_, args| {
                let Some(args) = args else {
                    let _ = controller.Close();
                    std::process::exit(1);
                };
                let local = (|| -> windows::core::Result<bool> {
                    let request = args.Request()?;
                    let mut uri = PWSTR::null();
                    request.Uri(&mut uri)?;
                    Ok(local_resource(&take_pwstr(uri), https))
                })().unwrap_or(false);
                if !local && args.SetResponse(&denied).is_err() {
                    // Returning an error alone does not cancel a network request.
                    // Close the WebView and terminate its owner if denial fails.
                    let _ = controller.Close();
                    std::process::exit(1);
                }
                Ok(())
            })),
            &mut token,
        )?;
        requests.AddWebResourceRequestedFilterWithRequestSourceKinds(
            w!("*"), COREWEBVIEW2_WEB_RESOURCE_CONTEXT_ALL,
            COREWEBVIEW2_WEB_RESOURCE_REQUEST_SOURCE_KINDS_ALL,
        )?;
    }
    Ok(())
}
