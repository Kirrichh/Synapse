"""Require selected jobs to pass, allowing only deliberately unselected skips."""
import json
import os


def check(results, required):
    return all(results.get(name, {}).get('result') == 'success' for name in required)


def read_object(name):
    try:
        value = json.loads(os.environ.get(name, ''))
        if not isinstance(value, dict):
            raise ValueError('expected object')
        return value
    except (ValueError, TypeError):
        raise SystemExit(f'{name} is missing or invalid; verification evidence is unavailable.') from None


def required_jobs(plan):
    required = {'select'} | {name for name, group in plan['groups'].items() if group['enabled']}
    required.update(name for name, selected in {
        'durable': plan['durable'], 'version': plan['version'], 'ui': any(plan['ui'].get(key, False) for key in ('python', 'native', 'renderer', 'reporting', 'python_mutations')),
        'additional-tests': bool(plan['additional']), 'selection-acceptance': plan['tooling']}.items() if selected)
    return required


if __name__ == '__main__':
    results = read_object('JOB_RESULTS')
    if not check(results, {'select'}):
        raise SystemExit('Test selection did not succeed; required checks cannot be established.')
    plan = read_object('TEST_PLAN')
    try:
        required = required_jobs(plan)
    except (KeyError, TypeError, AttributeError):
        raise SystemExit('TEST_PLAN is incomplete or invalid; required checks cannot be established.') from None
    if not check(results, required):
        raise SystemExit('A selected verification job did not succeed.')
