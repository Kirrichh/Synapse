"""Run only selected files from an existing job's pytest arguments."""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path
import subprocess
import sys


def selected_arguments(arguments, selected):
    options, cases = [], []
    for argument in arguments:
        if argument.startswith(('tests/', 'acceptance/')):
            candidates = glob.glob(argument) if '*' in argument else [argument]
            for candidate in candidates:
                if Path(candidate).is_dir():
                    cases.extend(path for path in sorted(selected) if path.startswith(candidate.rstrip('/') + '/'))
                elif candidate.split('::')[0] in selected:
                    cases.append(candidate)
        else:
            options.append(argument)
    return [*options, *dict.fromkeys(cases)] if cases else []


def main():
    arguments = selected_arguments(sys.argv[1:], set(json.loads(os.environ['SELECTED_TESTS'])))
    if not arguments:
        print('No affected acceptance files in this invocation.')
        return 0
    return subprocess.call([sys.executable, '-m', 'pytest', *arguments])


if __name__ == '__main__':
    raise SystemExit(main())
