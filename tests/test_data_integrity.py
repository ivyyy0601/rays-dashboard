import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from datetime import datetime,timezone
import pandas as pd
import data
import index_snapshot


class DataIntegrityTests(unittest.TestCase):
    def payload(self):
        return 'Date,Code,Component Weight (TOPIX)\n'+''.join(f'20260831,{1000+i},0.1%\n' for i in range(1000))+'Notes,,\n'

    def test_topix_official_dated_rows_not_footer(self):
        tickers,day=data.parse_topix_weights(self.payload(),now='2026-10-05')
        self.assertEqual(1000,len(tickers));self.assertEqual('2026-08-31',day)
        self.assertIn('1000.T',tickers)

    def test_topix_bad_partial_and_stale_lists_rejected(self):
        with self.assertRaises(ValueError):data.parse_topix_weights(self.payload().replace('0.1%','0.01%'),now='2026-10-05')
        with self.assertRaises(ValueError):data.parse_topix_weights(self.payload(),now='2027-01-01')
        with self.assertRaises(ValueError):data.parse_topix_weights(self.payload().replace('1001,','1000,'),now='2026-10-05')

    def test_previous_price_never_borrows_activity_date(self):
        with patch.object(index_snapshot,'_activity_block',return_value={'activity_date':'2026-10-02','data_ok':True}):
            row=index_snapshot.build_row('Topix',datetime.now(timezone.utc),{'close':100,'rsi':50,'price_date_from_activity':True},[])
        self.assertIsNone(row['price_date'])
        self.assertTrue(any('without an observation date' in n for n in row['notes']))

    def test_same_putcall_snapshot_recorded_only_once(self):
        import update_cache
        pc={'asof':'2026-10-03T01:33:00+00:00','source':'Barchart $SPX','vol_ratio':2,'oi_ratio':1,
            'total_put_vol':2,'total_call_vol':1,'total_put_oi':1,'total_call_oi':1,'stale':False}
        with tempfile.TemporaryDirectory() as directory, patch.object(update_cache,'DATA_DIR',Path(directory)),patch.object(data,'fetch_putcall_ratio',return_value=pc):
            update_cache.update_putcall_history();update_cache.update_putcall_history()
            frame=pd.read_csv(Path(directory)/'putcall_snapshots.csv')
            self.assertEqual(1,len(frame));self.assertIn('retrieval',frame.date_basis.iloc[0])


class BreadthTests(unittest.TestCase):
    def test_missing_latest_close_is_not_a_current_constituent_observation(self):
        import indicators
        frame=pd.DataFrame({'current':[1,2,3,4], 'stale':[1,2,3,float('nan')]})
        result=indicators.breadth_above_both_ma(frame,short=2,long=3)
        self.assertEqual(1,result['n_stocks'])
