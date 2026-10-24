"""Continuous renderer recovery against an external IPC peer, without page reloads."""
from pathlib import Path

PEER = (Path(__file__).parent / 'renderer_peer.js').read_text()


def page_with_peer(browser, url, setup=''):
    page = browser.new_page()
    page.set_default_timeout(5000)
    page.add_init_script(script=PEER + '\n' + setup)
    page.goto(url)
    return page


def reconnect(page):
    page.get_by_role('button', name='Reconnect display', exact=True).click()


def check_recovery(browser, url):
    # A broken first response followed by a same-sequence live snapshot needs
    # a new subscription, never a new core and never a reload.
    page = page_with_peer(browser, url, """
        window.acceptancePeer.set({state:'ready',process_id:123});
        window.acceptancePeer.corruptSubscription();
    """)
    page.get_by_text('PROTOCOL_ERROR', exact=True).wait_for()
    reconnect(page)
    page.get_by_role('button', name='Stop core', exact=True).wait_for()
    assert page.evaluate("window.acceptancePeer.commands.filter(c=>c==='desktop_subscribe').length") == 2
    assert not page.evaluate("window.acceptancePeer.commands.includes('desktop_start')")
    page.evaluate("window.acceptancePeer.old(0,{sequence:99,state:'fail',code:'CORE_EXITED'})")
    assert page.get_by_role('button', name='Stop core', exact=True).is_visible()
    page.close()

    page = page_with_peer(browser, url)
    page.get_by_role('button', name='Stop core', exact=True).wait_for()
    assert page.evaluate('window.acceptancePeer.spawnCount()') == 1
    for corrupt in ("window.acceptancePeer.raw('{')",
                    "window.acceptancePeer.rejectAck(); window.acceptancePeer.deliver({})"):
        page.evaluate(corrupt)
        page.get_by_text('PROTOCOL_ERROR', exact=True).wait_for()
        assert page.get_by_role('button', name='Force close desktop', exact=True).count() == 0
        assert page.get_by_role('button', name='Stop core', exact=True).count() == 0
        page.evaluate("window.acceptancePeer.deliver({sequence:88,state:'ready'})")
        assert page.get_by_text('PROTOCOL_ERROR', exact=True).is_visible()
        reconnect(page)
        page.get_by_role('button', name='Stop core', exact=True).wait_for()
        assert page.evaluate('window.acceptancePeer.spawnCount()') == 1
        assert page.evaluate("window.acceptancePeer.commands.filter(c=>c==='desktop_start').length") == 1
    # Refusal keeps the native session ready and cannot authorize force-close.
    page.evaluate("window.acceptancePeer.set({sequence:2,state:'ready',code:'NOT_READY'})")
    page.get_by_text('NOT_READY', exact=True).wait_for()
    assert page.get_by_role('button', name='Force close desktop', exact=True).count() == 0
    page.get_by_role('button', name='Stop core', exact=True).click()
    page.get_by_role('heading', name='Local core stopped', exact=True).wait_for()
    page.close()

    page = page_with_peer(browser, url, "window.acceptancePeer.set({state:'stopped',process_id:123});")
    page.get_by_role('heading', name='Local core stopped', exact=True).wait_for()
    assert not page.evaluate("window.acceptancePeer.commands.includes('desktop_start')")
    page.close()

    # Reconnecting an unstarted shell is read-only; core startup is separate.
    page = page_with_peer(browser, url, 'window.acceptancePeer.corruptSubscription();')
    page.get_by_text('PROTOCOL_ERROR', exact=True).wait_for()
    reconnect(page)
    page.get_by_role('button', name='Check connection again', exact=True).wait_for()
    assert not page.evaluate("window.acceptancePeer.commands.includes('desktop_start')")
    page.get_by_role('button', name='Check connection again', exact=True).click()
    page.get_by_role('button', name='Stop core', exact=True).wait_for()
    page.close()
