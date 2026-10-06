import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import pipeline_health
from reliability import CALENDARS

class HealthTests(unittest.TestCase):
    def test_missing_run_fails(self):
        with tempfile.TemporaryDirectory() as d:
            result = pipeline_health.inspect(d, datetime(2026,10,6,21,tzinfo=ZoneInfo('America/New_York')))
        self.assertFalse(result['ok'])
        self.assertEqual(result['expected_run'], '2026-10-06')

    def test_before_deadline_checks_previous_run(self):
        with tempfile.TemporaryDirectory() as d:
            result = pipeline_health.inspect(d, datetime(2026,10,6,19,tzinfo=ZoneInfo('America/New_York')))
        self.assertEqual(result['expected_run'], '2026-10-05')

    def test_complete_and_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as d, patch('reliability.validate_row', side_effect=lambda r,n:r):
            p = Path(d)
            (p/'daily_status.json').write_text(json.dumps({'day':'2026-10-06'}))
            (p/'email_delivery_2026-10-06.json').write_text(json.dumps({'state':'worker_completed'}))
            rows = [{'Index':name,'data_ok':True} for name in CALENDARS]
            (p/'index_snapshot.json').write_text(json.dumps({'rows':rows}))
            now = datetime(2026,10,6,21,tzinfo=ZoneInfo('America/New_York'))
            self.assertTrue(pipeline_health.inspect(d,now)['ok'])
            rows.append(rows[0])
            (p/'index_snapshot.json').write_text(json.dumps({'rows':rows}))
            self.assertFalse(pipeline_health.inspect(d,now)['ok'])

if __name__ == '__main__':
    unittest.main()
