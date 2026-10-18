"""Require selected jobs to pass, allowing only deliberately unselected skips."""
import json
import os


def check(results, required):
    return all(results.get(name, {}).get('result') == 'success' for name in required)


if __name__ == '__main__':
    plan = json.loads(os.environ['TEST_PLAN'])
    required = {'select'} | {name for name, group in plan['groups'].items() if group['enabled']}
    required.update(name for name, selected in {
        'durable': plan['durable'], 'version': plan['version'], 'ui': any(plan['ui'][key] for key in ('python', 'native', 'renderer', 'reporting')),
        'additional-tests': bool(plan['additional']), 'selection-acceptance': plan['tooling']}.items() if selected)
    if not check(json.loads(os.environ['JOB_RESULTS']), required):
        raise SystemExit('A selected verification job did not succeed.')
