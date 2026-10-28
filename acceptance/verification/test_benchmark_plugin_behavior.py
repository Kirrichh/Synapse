"""Exercise ordinary pytest discovery at both repository entry points."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = '''import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import subprocess, sys, threading

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(204)
        self.end_headers()
    def log_message(self, *args):
        pass

def test_real_effects():
    completed = subprocess.run([sys.executable, '-B', '-m', 'synapse', '--help'], capture_output=True, text=True)
    assert completed.returncode == 0
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request('POST', '/provider', body=b'')
        assert connection.getresponse().status == 204
        connection.close()
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        server.server_close()
'''


@pytest.mark.parametrize('entry', ['root', 'acceptance'])
@pytest.mark.parametrize('explicit', [False, True])
def test_ordinary_and_explicit_pytest_invocations(entry, explicit, tmp_path):
    trace = tmp_path / 'trace.jsonl'
    env = {**os.environ, 'PR112_BENCH_TRACE': str(trace),
           'PYTHONPATH': os.pathsep.join((str(ROOT / 'acceptance/benchmarks/pr112'), str(ROOT)))}
    env.pop('PYTEST_DISABLE_PLUGIN_AUTOLOAD', None)
    with tempfile.TemporaryDirectory(prefix='pytest-probe-', dir=ROOT / 'acceptance/verification') as directory:
        probe = Path(directory) / 'test_probe.py'
        probe.write_text(PROBE)
        command = [sys.executable, '-B', '-m', 'pytest', '-q', '-p', 'no:cacheprovider']
        if explicit:
            command += ['-p', 'benchmark_plugin']
        command.append(str(probe))
        process = subprocess.run(command, cwd=ROOT if entry == 'root' else ROOT / 'acceptance', env=env,
                                 capture_output=True, text=True, timeout=60)
        assert process.returncode == 0, process.stdout + process.stderr
    events = [json.loads(line)['kind'] for line in trace.read_text().splitlines()] if trace.exists() else []
    if explicit:
        assert 'cli' in events and 'provider_server' in events
    else:
        assert events == []
