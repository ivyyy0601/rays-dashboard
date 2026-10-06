"""Independent, read-only scheduler health check. Nonzero means attention needed.

Runs outside the download process. Does not rebuild data or send market signals.
"""
import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')

def inspect(directory, now=None):
    now = now or datetime.now(ET)
    now = now.astimezone(ET)
    due = now.replace(hour=20, minute=40, second=0, microsecond=0)
    if now < due:
        due -= timedelta(days=1)
    day = due.date().isoformat()
    errors = []
    def read(name):
        try:
            value = json.loads((Path(directory)/name).read_text())
            if not isinstance(value, dict):
                raise ValueError('not an object')
            return value
        except (OSError, ValueError):
            errors.append(f'{name}: unavailable or invalid')
            return {}
    status = read('daily_status.json')
    if status.get('day', '') < day:
        errors.append(f'No completed daily validation for {day}')
    snapshot = read('index_snapshot.json')
    from reliability import CALENDARS, validate_row
    rows = snapshot.get('rows', [])
    if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
        rows = []
    if sorted(r.get('Index', '') for r in rows) != sorted(CALENDARS):
        errors.append('Index snapshot must contain each of the 12 indices exactly once')
    invalid = []
    for row in rows:
        # Validate against the due run, not the next day's intraday clock.
        validate_row(row, due)
        if not row.get('data_ok'):
            invalid.append(row.get('Index', 'unknown'))
    if invalid:
        errors.append('Unverified/incomplete indices: '+', '.join(invalid))
    mail = read(f'email_delivery_{day}.json')
    if mail.get('state') not in ('worker_completed', 'sent', 'skipped_no_alerts'):
        errors.append('Email attempt missing, failed, or delivery uncertain')
    return {'ok': not errors, 'expected_run': day, 'checked_at': now.isoformat(),
            'errors': errors, 'invalid_indices': invalid}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default=str(Path(__file__).parent/'data'))
    args = parser.parse_args()
    result = inspect(args.data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['ok'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
