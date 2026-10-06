"""Fail-closed freshness checks and atomic persistence for daily snapshots."""
import json
import os
import tempfile
from pathlib import Path
from functools import lru_cache
import pandas as pd
import numpy as np

def missing_bars(closes, volumes, tickers, sessions):
    """Zero volume is reported data, NaN is not. Never silently complete a baseline."""
    p = closes.reindex(index=sessions, columns=tickers)
    v = volumes.reindex(index=sessions, columns=tickers)
    valid = p.gt(0) & v.ge(0) & np.isfinite(p) & np.isfinite(v)
    return int((~valid).to_numpy().sum())

def retain_dated_price(price, previous, target):
    if price.get('price_date') == target:
        return price, False
    if previous.get('price_date') != target or not previous.get('close'):
        return price, False
    fields = ['price_date','close','rsi','close_source','rsi_source','rsi_history']
    return {k:previous[k] for k in fields if k in previous}, True

CALENDARS = {
    'S&P 500': 'XNYS', 'Nasdaq 100': 'XNYS', 'SOX': 'XNYS',
    'Russell 2000': 'XNYS', 'Hang Seng': 'XHKG', 'CSI 300': 'XSHG',
    'CSI 1000': 'XSHG', 'ChiNext': 'XSHG', 'Nikkei 225': 'XTKS',
    'Topix': 'XTKS', 'Taiwan': 'XTAI', 'KOSPI 200': 'XKRX',
}

@lru_cache(maxsize=32)
def calendar(name, year):
    import exchange_calendars as xc
    return xc.get_calendar(CALENDARS[name], start=f'{year-2}-01-01', end=f'{year}-12-31')

def expected_session(name, now):
    current = pd.Timestamp(now)
    if current.tzinfo is None:
        raise ValueError('Freshness validation requires timezone-aware now')
    cal = calendar(name, current.year)
    schedule = cal.schedule.loc[(current - pd.Timedelta(days=30)).strftime('%Y-%m-%d'):current.strftime('%Y-%m-%d')]
    finished = schedule[schedule['close'] + pd.Timedelta(minutes=30) <= current]
    if finished.empty:
        raise ValueError(f'No completed exchange session for {name}')
    return finished.index[-1].strftime('%Y-%m-%d')

def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(payload, f, indent=2, default=str, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

def validate_row(row, now):
    notes = row.setdefault('notes', [])
    try:
        target = expected_session(row['Index'], now)
        row['expected_session'] = target
        row['price_fresh'] = row.get('price_date') == target
        row['activity_fresh'] = row.get('activity_date') == target
        if not row['price_fresh'] or not row['activity_fresh']:
            message = (f"freshness check failed: expected {target}; close={row.get('price_date')}; "
                       f"activity={row.get('activity_date')}")
            if message not in notes:
                notes.append(message)
            row['data_ok'] = False
    except Exception as exc:
        row['price_fresh'] = row['activity_fresh'] = False
        row['data_ok'] = False
        message = f'calendar validation unavailable: {type(exc).__name__}: {exc}'
        if message not in notes:
            notes.append(message)
    if not row.get('data_ok'):
        row.pop('turnover_alert', None)
    if not row.get('price_fresh'):
        row.pop('rsi_alert', None)
    row['data_notes'] = list(notes)
    return row
