"""Measurement-only hooks around real CLI subprocesses and local provider."""
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest


@pytest.fixture(autouse=True)
def measure_commands(monkeypatch, tmp_path):
    trace_path = Path(os.environ['PR112_BENCH_TRACE'])
    original = subprocess.run
    resume_count = 0
    handler = http.server.BaseHTTPRequestHandler.handle_one_request

    def timed_handler(instance):
        started = time.perf_counter()
        try:
            return handler(instance)
        finally:
            if getattr(instance, 'command', None) == 'POST':
                with trace_path.open('a') as stream:
                    stream.write(json.dumps({'kind': 'provider_server', 'seconds': time.perf_counter() - started}) + '\n')

    def measured(command, *args, **kwargs):
        nonlocal resume_count
        if not isinstance(command, (list, tuple)) or list(command[1:4]) != ['-B', '-m', 'synapse']:
            return original(command, *args, **kwargs)
        metric = tmp_path / ('time-' + uuid.uuid4().hex + '.json')
        wrapper = Path(__file__).with_name('timed_exec.py')
        wrapped = [sys.executable, str(wrapper), str(metric), *command]
        result = original(wrapped, *args, **kwargs)
        row = json.loads(metric.read_text())
        subcommand = command[4:6]
        if subcommand == ['project', 'run']:
            run_dir = Path(command[command.index('--run-dir') + 1]).name
            label = 'run-' + ('reuse' if run_dir == 'automatic-run' else 'initial')
        elif command[4] == 'approve':
            run_dir = Path(command[command.index('--resume-run') + 1]).name
            label = 'approve-' + ('reuse' if run_dir == 'automatic-run' else 'initial')
        elif subcommand == ['project', 'resume']:
            resume_count += 1
            label = 'resume-first' if resume_count == 1 else 'resume-repeat'
        elif subcommand == ['project', 'learn']:
            label = 'source-publication'
        elif subcommand == ['project', 'recall']:
            label = 'source-recall'
        else:
            label = 'other'
        row.update(kind='cli', scenario=label)
        with trace_path.open('a') as stream:
            stream.write(json.dumps(row) + '\n')
        return result

    monkeypatch.setattr(http.server.BaseHTTPRequestHandler, 'handle_one_request', timed_handler)
    monkeypatch.setattr(subprocess, 'run', measured)
