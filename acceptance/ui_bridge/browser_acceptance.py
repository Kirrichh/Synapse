"""Browser/component evidence only. Native IPC, CSP, and OS containment need desktop runs."""
import argparse
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
from threading import Thread

from playwright.sync_api import sync_playwright
from browser_recovery import check_recovery

ROOT = Path(__file__).resolve().parents[2]


@contextmanager
def serve(directory):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def check(base_url, screenshot=None):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.environ.get("SYNAPSE_BROWSER_EXECUTABLE"), headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.set_default_timeout(5000)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base_url)
        page.get_by_text("DESKTOP_REQUIRED", exact=True).wait_for()
        assert page.get_by_role("button", name="Stop core", exact=True).count() == 0
        assert page.get_by_role("button", name="Force close desktop", exact=True).count() == 0
        assert "Task execution" not in page.get_by_role("status").inner_text()
        page.get_by_role("button", name="RU", exact=True).click()
        assert page.locator("html").get_attribute("lang") == "ru"
        assert page.get_by_role("button", name="RU", exact=True).evaluate("el => el === document.activeElement")
        assert "Подключение требует внимания" in page.get_by_role("status").inner_text()
        page.get_by_role("button", name="EN", exact=True).click()
        assert page.get_by_text("DESKTOP_REQUIRED", exact=True).is_visible()
        assert page.get_by_role("button", name="EN", exact=True).get_attribute("aria-pressed") == "true"
        assert not errors, errors
        if screenshot:
            page.screenshot(path=str(screenshot), full_page=True)
        page.close()

        component = browser.new_page(viewport={"width": 1600, "height": 1000})
        component.set_default_timeout(5000)
        component.add_init_script(path=ROOT / "acceptance/ui_bridge/renderer_peer.js")
        component.goto(base_url)
        component.get_by_role("button", name="Stop core", exact=True).wait_for()
        assert "Task execution and project access are not available yet" in component.get_by_role("status").inner_text()
        component.evaluate("window.acceptancePeer.deliver({sequence: 0, state: 'fail', code: 'CORE_EXITED'})")
        assert component.get_by_role("button", name="Stop core", exact=True).is_visible()
        component.evaluate("window.acceptancePeer.deliver({sequence: 10, session_id: 'aa993210-4107-4444-9777-aabbaa998877', state: 'fail', code: 'CORE_EXITED'})")
        assert component.get_by_role("button", name="Stop core", exact=True).is_visible()
        component.get_by_role("button", name="Stop core", exact=True).click()
        component.get_by_role("heading", name="Local core stopped", exact=True).wait_for()
        assert component.evaluate("window.acceptancePeer.commands.includes('desktop_stop')")
        component.evaluate("window.acceptancePeer.set({sequence: 4, state: 'fail', code: 'SHUTDOWN_TIMEOUT'})")
        force = component.get_by_role("button", name="Force close desktop", exact=True)
        force.wait_for()
        component.once("dialog", lambda dialog: dialog.dismiss())
        force.click()
        assert not component.evaluate("window.acceptancePeer.commands.includes('desktop_force_close')")
        component.once("dialog", lambda dialog: dialog.accept())
        force.click()
        assert component.evaluate("window.acceptancePeer.commands.includes('desktop_force_close')")
        component.evaluate("window.acceptancePeer.deliver({sequence: 5, domain_ready: true})")
        component.get_by_text("PROTOCOL_ERROR", exact=True).wait_for()
        component.evaluate("window.acceptancePeer.deliver({sequence: 6, state: 'ready'})")
        assert component.get_by_text("PROTOCOL_ERROR", exact=True).is_visible()
        assert component.evaluate("window.acceptancePeer.commands.includes('desktop_ack')")
        component.reload()
        component.get_by_role("button", name="Stop core", exact=True).wait_for()
        component.evaluate("window.acceptancePeer.set({sequence: 2, process_id: undefined, state: 'fail', code: 'CORE_MISSING'})")
        component.get_by_role("button", name="Check connection again", exact=True).wait_for()
        component.evaluate("window.acceptancePeer.rejectStart('BUSY')")
        component.get_by_role("button", name="Check connection again", exact=True).click()
        component.get_by_text("BUSY", exact=True).wait_for()
        component.get_by_role("button", name="Check connection again", exact=True).click()
        component.get_by_role("button", name="Stop core", exact=True).wait_for()
        check_recovery(browser, base_url)
        browser.close()
    print("Browser and renderer component behavior passed; no installed-desktop claim.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:4174")
    parser.add_argument("--screenshot", type=Path)
    parser.add_argument("--serve", type=Path, help="Serve a built renderer on an isolated loopback port")
    args = parser.parse_args()
    if args.serve:
        with serve(args.serve) as url:
            check(url, args.screenshot)
    else:
        check(args.url, args.screenshot)
