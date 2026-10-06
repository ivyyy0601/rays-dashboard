"""Bounded prefetch/validate/publish pipeline; at-most-once scheduled email attempt."""
import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
ET = ZoneInfo('America/New_York')

def run_child(script, seconds, env=None):
    child = subprocess.Popen([sys.executable, str(ROOT / script)], cwd=ROOT,
                             env=env, start_new_session=True)
    try:
        return child.wait(timeout=max(1, seconds))
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        print(f'TIMEOUT: {script}; previous published snapshot preserved', flush=True)
        return 124

def main():
    from reliability import atomic_json, validate_row
    parser = argparse.ArgumentParser()
    parser.add_argument('--prefetch', action='store_true')
    args = parser.parse_args()
    directory = ROOT / 'data'
    directory.mkdir(exist_ok=True)
    with open(directory / 'daily_run.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return run_locked(directory, args)

def run_locked(directory, args):
    from reliability import atomic_json, validate_row
    now = datetime.now(ET)
    day = now.date().isoformat()
    stage = directory / 'staging' / day
    stage.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, RAYS_OUTPUT_DIR=str(stage), PYTHONUNBUFFERED='1')
    print(f'{now.isoformat()} phase={"prefetch" if args.prefetch else "final"}', flush=True)
    if args.prefetch:
        code = run_child('update_cache.py', 55*60, env)
        atomic_json(directory / 'prefetch_status.json', {'day': day, 'exit_code': code,
                    'finished_at': datetime.now(ET).isoformat()})
        return code
    marker = directory / f'email_delivery_{day}.json'
    if marker.exists():
        print('Email already attempted today; review delivery record before any resend', flush=True)
        return 0
    scheduled_deadline = now.replace(hour=20, minute=25, second=0, microsecond=0)
    budget = max(60, min(25*60, (scheduled_deadline-now).total_seconds()))
    deadline = time.monotonic() + budget
    candidate = None
    while time.monotonic() < deadline:
        code = run_child('update_cache.py', min(10*60, deadline-time.monotonic()), env)
        try:
            candidate = json.loads((stage / 'index_snapshot.json').read_text())
            from reliability import CALENDARS
            rows = candidate.get('rows', [])
            if (not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows)
                    or sorted(r.get('Index', '') for r in rows) != sorted(CALENDARS)
                    or candidate['built_at_et'][:10] != day):
                candidate = None
            else:
                for row in candidate['rows']:
                    validate_row(row, datetime.now(ET))
        except (OSError, ValueError, KeyError, TypeError):
            candidate = None
        if candidate and all(r.get('data_ok') for r in candidate['rows']):
            break
        if time.monotonic() + 60 >= deadline:
            break
        print(f'Validation incomplete (exit={code}); retrying pending data', flush=True)
        time.sleep(60)
    if candidate is None:
        try:
            candidate = json.loads((directory / 'index_snapshot.json').read_text())
        except (OSError, ValueError):
            candidate = {'rows': []}
        for row in candidate.get('rows', []):
            row['data_ok'] = False
            row.setdefault('notes', []).append('Daily refresh failed; previous snapshot retained')
    for row in candidate.get('rows', []):
        validate_row(row, datetime.now(ET))
    candidate['validated_at_et'] = datetime.now(ET).isoformat()
    atomic_json(directory / 'index_snapshot.json', candidate)
    import index_snapshot
    index_snapshot.save_breadth_cache(candidate)
    market = stage / 'market_series.json'
    if market.exists():
        atomic_json(directory / 'market_series.json', json.loads(market.read_text()))
    atomic_json(directory / 'daily_status.json', {
        'day': day, 'validated_at': candidate['validated_at_et'],
        'invalid_indices': [r['Index'] for r in candidate.get('rows', []) if not r.get('data_ok')],
        'snapshot_rows': len(candidate.get('rows', []))})
    # Claim before sending. SMTP delivery can be ambiguous on interruption;
    # never retry that ambiguity automatically and risk duplicate emails.
    atomic_json(marker, {'state': 'attempting', 'started_at': datetime.now(ET).isoformat()})
    code = run_child('check_alerts.py', 240)
    atomic_json(marker, {'state': 'worker_completed' if code == 0 else 'failed_or_unknown',
                        'exit_code': code, 'finished_at': datetime.now(ET).isoformat()})
    return code

if __name__ == '__main__':
    raise SystemExit(main())
