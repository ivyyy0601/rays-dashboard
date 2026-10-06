import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime
import pandas as pd
import reliability as rel
import update_cache  # script-safe Streamlit stub; does not execute main
import data
import index_snapshot as idx
import daily_pipeline as pipeline

NOW = pd.Timestamp('2026-10-06T00:00:00Z')

class ReliabilityTests(unittest.TestCase):
    def test_undated_fetch_does_not_replace_valid_dated_price(self):
        old = dict(price_date='2026-10-05',close=123,rsi=50)
        price,used = rel.retain_dated_price({'close':125},old,'2026-10-05')
        self.assertTrue(used)
        self.assertEqual(price['close'],123)
        self.assertFalse(rel.retain_dated_price({'close':125},old,'2026-10-06')[1])
        fresh = dict(price_date='2026-10-05',close=124)
        self.assertEqual(rel.retain_dated_price(fresh,old,'2026-10-05'),(fresh,False))
    def test_baseline_missing_data_is_not_zero_volume(self):
        dates = pd.to_datetime(['2026-10-02','2026-10-05'])
        prices = pd.DataFrame({'A':[10.,11.]},index=dates)
        volumes = pd.DataFrame({'A':[0.,3.]},index=dates)
        self.assertEqual(rel.missing_bars(prices,volumes,['A'],dates),0)
        volumes.iloc[0,0] = float('nan')
        self.assertEqual(rel.missing_bars(prices,volumes,['A'],dates),1)

    def test_taiwan_etf_contamination_is_flagged(self):
        empty = pd.Series(dtype=float)
        activity = dict(n_list=1,turnover=empty,volume=empty,traded=empty)
        with patch.dict(data.CONSTITUENT_FETCHERS, {'Taiwan':lambda:['0053.TW']}), \
                patch.object(idx,'_track_list',return_value={}), \
                patch.object(idx,'_constituent_activity',return_value=activity):
            notes = []
            out = idx._activity_block('Taiwan',NOW,notes)
            self.assertFalse(out['data_ok'])
            self.assertTrue(any('list includes ETFs' in n for n in notes))

    def test_calendars_holidays_and_intraday(self):
        for name, day in [('S&P 500','2026-10-05'), ('Hang Seng','2026-10-05'),
                          ('Nikkei 225','2026-10-05'), ('Taiwan','2026-10-05'),
                          ('CSI 300','2026-09-30'), ('KOSPI 200','2026-10-02')]:
            self.assertEqual(rel.expected_session(name, NOW), day, name)
        self.assertEqual(rel.expected_session('S&P 500', pd.Timestamp('2026-10-05T18:00Z')), '2026-10-02')
        self.assertEqual(rel.expected_session('S&P 500', pd.Timestamp('2026-10-04T23:00Z')), '2026-10-02')

    def test_stale_and_unknown_dates_fail_closed(self):
        row = dict(Index='S&P 500', price_date='2026-10-05', activity_date='2026-10-02',
                   data_ok=True, turnover_alert='HIGH')
        rel.validate_row(row, NOW)
        self.assertFalse(row['data_ok'])
        self.assertNotIn('turnover_alert', row)
        self.assertTrue(row['price_fresh'])
        with patch.object(rel, 'expected_session', side_effect=ValueError('unknown calendar')):
            row = rel.validate_row(dict(Index='X', data_ok=True, rsi_alert='HIGH'), NOW)
        self.assertFalse(row['data_ok'])
        self.assertNotIn('rsi_alert', row)

    def test_atomic_failure_preserves_previous(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'snapshot.json'
            rel.atomic_json(path, {'ok':1})
            with self.assertRaises(ValueError):
                rel.atomic_json(path, {'bad':float('nan')})
            self.assertEqual(json.loads(path.read_text()), {'ok':1})

    def test_download_retries_stale_and_reuses_disk(self):
        with tempfile.TemporaryDirectory() as d, patch.object(data, '__file__', str(Path(d)/'data.py')), \
                patch('time.sleep'), patch.object(data, '_YF_DAILY', {}):
            old = pd.DataFrame({'Close':[1.], 'Volume':[10.]}, index=pd.to_datetime(['2026-10-02']))
            new = pd.DataFrame({'Close':[1.,2.], 'Volume':[10.,0.]}, index=pd.to_datetime(['2026-10-02','2026-10-05']))
            with patch.object(data.yf, 'download', side_effect=[old,new]) as download:
                result = data.yf_daily(['TEST'], expected_date='2026-10-05')
                self.assertEqual(download.call_count, 2)
                self.assertEqual(download.call_args.kwargs['timeout'], 30)
                self.assertEqual(download.call_args.kwargs['threads'], 4)
                self.assertEqual(result['TEST'].iloc[-1]['Volume'], 0)
            data._YF_DAILY.clear()
            with patch.object(data.yf, 'download', side_effect=AssertionError('must use disk')):
                self.assertIn('TEST', data.yf_daily(['TEST'], expected_date='2026-10-05'))

    def test_small_missing_batch_retries_three_times(self):
        with patch.object(data, '_YF_DAILY', {}), patch('time.sleep'), \
                patch.object(data.yf, 'download', return_value=pd.DataFrame()) as download:
            self.assertEqual(data.yf_daily(['MISSING'], expected_date='2026-10-05'), {})
            self.assertEqual(download.call_count, 3)

    def test_latest_date_alone_does_not_hide_history_gaps(self):
        dates = pd.to_datetime(['2026-10-02','2026-10-05'])
        incomplete = pd.DataFrame({'Close':[1.,2.],'Volume':[float('nan'),10.]}, index=dates)
        complete = incomplete.fillna(5.)
        with tempfile.TemporaryDirectory() as d, patch.object(data,'__file__',str(Path(d)/'data.py')), \
                patch.object(data,'_YF_DAILY',{}), patch('time.sleep'), \
                patch.object(data.yf,'download',side_effect=[incomplete,complete]) as download:
            data.yf_daily(['GAP'],expected_date='2026-10-05',expected_sessions=dates)
            self.assertEqual(download.call_count,2)

    def test_breadth_keeps_price_day_with_missing_volume(self):
        dates = pd.bdate_range(end='2026-10-05', periods=220)
        closes = pd.DataFrame({'TEST':range(1,221)}, index=dates)
        with patch.object(idx.indicators, 'breadth_above_both_ma', wraps=idx.indicators.breadth_above_both_ma) as fn:
            idx._breadth_block(closes, dates[-21:].delete(2), dates[-1], ['TEST'])
            self.assertEqual(len(fn.call_args.args[0]), 220)

    def test_pipeline_publication_then_single_email_attempt(self):
        class Frozen(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026,10,5,20,0,tzinfo=pipeline.ET)
        with tempfile.TemporaryDirectory() as d, patch.object(pipeline,'ROOT',Path(d)), \
                patch.object(pipeline,'datetime',Frozen), patch('sys.argv',['run_daily.py']), \
                patch.object(idx,'save_breadth_cache'), patch.dict(rel.CALENDARS, {'S&P 500':'XNYS'}, clear=True):
            def child(script, seconds, env=None):
                if script == 'update_cache.py':
                    rel.atomic_json(Path(env['RAYS_OUTPUT_DIR'])/'index_snapshot.json', {
                        'built_at_et':'2026-10-05T20:00:00-04:00', 'rows':[
                        dict(Index='S&P 500', price_date='2026-10-05', activity_date='2026-10-05', data_ok=True)]})
                else:
                    self.assertTrue((Path(d)/'data/index_snapshot.json').exists())
                return 0
            with patch.object(pipeline,'run_child',side_effect=child) as run:
                self.assertEqual(pipeline.main(),0)
                self.assertEqual(pipeline.main(),0)
                self.assertEqual([c.args[0] for c in run.call_args_list], ['update_cache.py','check_alerts.py'])

    def test_child_timeout_terminates_process_group(self):
        import subprocess
        with patch.object(pipeline.subprocess, 'Popen') as proc, patch.object(pipeline.os, 'killpg') as kill:
            proc.return_value.pid = 12345
            proc.return_value.wait.side_effect = [subprocess.TimeoutExpired('test', 1), 0]
            self.assertEqual(pipeline.run_child('dummy.py',1),124)
            kill.assert_called_once_with(12345, pipeline.signal.SIGTERM)

    def test_email_excludes_invalid_index_signals(self):
        import sys
        import types
        with patch.dict(sys.modules, {'alerts':types.ModuleType('alerts')}):
            import check_alerts as ca
        stale = dict(Index='S&P 500', price_date='2000-01-01', activity_date='2000-01-01',
                     data_ok=True,rsi=90,turnover_dev=80,turnover_usd=100,
                     breadth={'pct_above_both':99})
        with patch.object(ca.data,'load_market_series',return_value=pd.DataFrame()), \
                patch.object(ca.data,'fetch_aaii_sentiment',return_value=pd.DataFrame()), \
                patch.object(ca.data,'fetch_putcall_ratio',return_value={'asof':'2000-01-01T20:00:00-05:00','stale':False}), \
                patch.object(ca.index_snapshot,'load',return_value={'rows':[stale]}):
            snap = ca.build_snapshot()
            self.assertEqual(snap['breadth_rows'],[])
            self.assertEqual(snap['tab2_rows'][0]['RSI(14)'],'—')
            self.assertEqual(snap['tab2_rows'][0]['Δ vs 20d'],'—')
            self.assertTrue(snap['data_warnings'])
            self.assertEqual(snap['putcall'],{})
            self.assertTrue(any('SPX put/call withheld' in w for w in snap['data_warnings']))

if __name__ == '__main__':
    unittest.main(verbosity=2)
