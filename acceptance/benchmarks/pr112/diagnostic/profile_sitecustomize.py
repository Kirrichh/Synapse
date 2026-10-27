"""Sample Python stacks in the diagnostic-only reuse approval process."""
import atexit
from collections import Counter
import json
import os
from pathlib import Path
import signal
import sys

output = os.environ.get('PR112_PROFILE_OUTPUT')
if output and 'approve' in sys.argv and any('automatic-run' in arg for arg in sys.argv) and sys.argv[0] == '-m':
    inclusive = Counter()
    leaf = Counter()
    samples = [0]
    def label(frame):
        return f'{frame.f_code.co_filename}:{frame.f_code.co_firstlineno}:{frame.f_code.co_name}'
    def sample(signum, frame):
        samples[0] += 1
        leaf[label(frame)] += 1
        seen = set()
        while frame is not None:
            seen.add(label(frame))
            frame = frame.f_back
        inclusive.update(seen)
    def finish():
        signal.setitimer(signal.ITIMER_PROF, 0)
        with open(output, 'w') as stream:
            json.dump({'interval_seconds': 0.01, 'samples': samples[0], 'inclusive': inclusive.most_common(60), 'leaf': leaf.most_common(60)}, stream, indent=2)
    signal.signal(signal.SIGPROF, sample)
    signal.setitimer(signal.ITIMER_PROF, 0.01, 0.01)
    atexit.register(finish)
