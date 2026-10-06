"""
Tab 2 snapshot — close, RSI(14), volume and turnover for every index, computed
once by the daily cron and saved to data/index_snapshot.json.

The dashboard table, the RSI history chart, the alert email and the AI chat all
read this one file, so every number on a row has a known trading date and
nothing is mixed with live page-load data.

Close / RSI
  - Yahoo (A-shares: Sina via AkShare) daily closes, RSI(14) computed locally.
  - Topix / KOSPI 200: TradingView's dated daily index snapshot (close + RSI).
  - Only completed sessions are used (data.completed_daily_rows).

Volume / turnover — index volume is Σ of every constituent's daily volume, and
turnover is Σ close × volume (local currency, shown in USD). Provider "index
volume" fields were checked against official figures and do not measure the
index itself. Only turnover gets a 20-day average, Δ and alert:
  - Δ = latest session ÷ mean of the 20 sessions before it − 1, in local
    currency (FX moves are not trading activity).
  - A session counts only if ≥ 90% of listed constituents reported volume, so a
    holiday or partial-data day can't distort the latest value or the average.
  - A short constituent list (fallback source), a turnover identical to the
    previous session, or one < 40% of the 20-day median flags the row ⚠️ and
    suppresses its alert.

Constituent lists are re-fetched every run from the index publisher / exchange /
tracking-ETF holdings, so index reviews flow through automatically. Each run is
diffed against the last saved list (data/constituents/): changes are noted on the
row and appended to data/constituent_changes.csv; a fallback source, or a list
unchanged for longer than the index's normal review cycle, is flagged.

Run standalone:  python index_snapshot.py
"""
import json
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

if __name__ == "__main__":
    # Plain-script mode: bypass streamlit's caching decorators
    _st = types.ModuleType("streamlit")
    def _cache_data(*a, **k):
        if a and callable(a[0]):
            return a[0]
        return lambda f: f
    _cache_data.clear = lambda: None
    _st.cache_data = _cache_data
    sys.modules["streamlit"] = _st

import numpy as np
import pandas as pd
import yfinance as yf

import config
import data
import indicators
from reliability import expected_session, validate_row, atomic_json, calendar, missing_bars, retain_dated_price

ET = ZoneInfo("America/New_York")
OUTPUT_DIR = Path(os.environ.get('RAYS_OUTPUT_DIR', str(Path(__file__).parent / 'data')))
SNAPSHOT_FILE = OUTPUT_DIR / "index_snapshot.json"
MARKET_FILE = OUTPUT_DIR / "market_series.json"
LIST_DIR = Path(__file__).parent / "data" / "constituents"
CHANGE_LOG = Path(__file__).parent / "data" / "constituent_changes.csv"
INCOMPLETE_RATIO = 0.4   # latest turnover < 40% of the 20-day median → incomplete day
WINDOW = config.TURNOVER_AVG_WINDOW


def _fmt_date(d):
    return pd.Timestamp(d).strftime("%Y-%m-%d") if d is not None else None


def _to_dates(df):
    """Index → naive calendar dates in the exchange's own timezone."""
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    df = df.copy()
    df.index = idx.normalize()
    return df[~df.index.duplicated(keep="last")]


# ---------- Close / RSI ----------

def _price_history(name: str, now: datetime):
    """(completed daily closes, source label). Yahoo's `end` is exclusive, so
    end = now + 2 days — end = now drops the latest Asian session."""
    ticker = config.INDICES[name]["index"]
    if ticker in data.AKSHARE_ONLY_INDICES:
        df = data._fetch_index_via_akshare(ticker, config.HISTORY_DAYS)
        source = data.akshare_index_source(ticker)
    else:
        df = yf.download(ticker, start=now - timedelta(days=config.HISTORY_DAYS),
                         end=now + timedelta(days=2), progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        source = f"Yahoo {ticker}"
    if df.empty or "Close" not in df:
        return pd.Series(dtype=float), source
    close = _to_dates(df.dropna(subset=["Close"]))["Close"]
    return data.completed_daily_rows(close, name, now=now), source


def _price_block(name: str, now: datetime, notes: list) -> dict:
    if name in config.TV_SNAPSHOT_INDICES:
        snap = data.fetch_topix_snapshot() if name == "Topix" else data.fetch_kospi_snapshot()
        prev = snap.attrs.get("previous_session")
        if snap.empty and prev:
            # Market trading now → previous completed session; build_row dates it
            # with the constituents' latest complete session.
            return {"close": prev["close"], "rsi": prev["rsi"],
                    "close_source": snap.attrs.get("history_source", "TradingView")
                                    + f" — previous session (market trading on {prev['trading_day']})",
                    "rsi_source": "TradingView (same snapshot, previous session)",
                    "price_date_from_activity": True}
        if snap.empty:
            notes.append("TradingView index snapshot unavailable")
            return {}
        return {"price_date": _fmt_date(snap.index[-1]), "close": float(snap["Close"].iloc[-1]),
                "rsi": float(snap["RSI14"].iloc[-1]),
                "close_source": snap.attrs.get("history_source", "TradingView"),
                "rsi_source": "TradingView (same snapshot)"}
    close, source = _price_history(name, now)
    if close.empty:
        notes.append("no price data")
        return {}
    rsi = indicators.compute_rsi(close, config.RSI_WINDOW)
    rsi.iloc[:config.RSI_WINDOW] = np.nan
    hist = rsi.dropna().tail(config.RSI_CHART_DAYS)
    return {"price_date": _fmt_date(close.index[-1]), "close": float(close.iloc[-1]),
            "rsi": float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else None,
            "close_source": source, "rsi_source": "local RSI(14) on the same closes",
            "rsi_history": {"dates": [_fmt_date(d) for d in hist.index],
                            "values": [round(float(v), 2) for v in hist.values]}}


# ---------- Constituent list monitoring ----------

def _crosscheck(name: str, our_volume: float, day, now: datetime, notes: list) -> dict:
    """Compare Yahoo's index-level volume with our Σ constituents on the same
    session (config.VOLUME_CROSSCHECK). Out of band → the list is likely stale."""
    ticker, mult, (lo, hi) = config.VOLUME_CROSSCHECK[name]
    try:
        df = yf.download(ticker, start=now - timedelta(days=15), end=now + timedelta(days=2),
                         progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        ref = _to_dates(df)["Volume"].get(pd.Timestamp(day))
    except Exception:
        ref = None
    if ref is None or not ref > 0 or not our_volume:
        notes.append(f"volume cross-check unavailable ({ticker} has no volume for {_fmt_date(day)})")
        return {"crosscheck": {"ref": ticker, "ratio": None, "band": [lo, hi]}}
    ratio = float(ref) * mult / our_volume
    if not lo <= ratio <= hi:
        notes.append(f"{ticker} volume ÷ our constituent sum = {ratio:.3f}, outside the normal "
                     f"{lo:.2f}–{hi:.2f} — constituent list may be out of date")
    return {"crosscheck": {"ref": ticker, "ratio": ratio, "band": [lo, hi],
                           "ok": lo <= ratio <= hi}}



def _track_list(name: str, tickers: list, now: datetime, notes: list) -> dict:
    """Diff today's list with the last saved one; log changes; flag staleness."""
    today = now.astimezone(ET).date().isoformat()
    source = data.CONSTITUENT_SOURCE.get(name, "")
    path = LIST_DIR / f"{name.replace(' ', '_').replace('&', 'and')}.json"
    try:
        prev = json.loads(path.read_text())
    except (OSError, ValueError):
        prev = None
    out = {"list_source": source or None}
    if "fallback" in source:
        notes.append(f"constituent list came from a fallback source ({source}) — may be stale")

    if name == "Topix":
        notes.append("TOPIX membership uses JPX's dated monthly weights; later constituent changes may not yet be included")
    current = sorted(set(tickers))
    last_changed = today
    if prev:
        added = sorted(set(current) - set(prev["tickers"]))
        removed = sorted(set(prev["tickers"]) - set(current))
        last_changed = prev.get("last_changed", prev.get("date", today))
        if added or removed:
            last_changed = today
            out["list_change"] = (f"since {prev['date']}: +{len(added)} −{len(removed)} "
                                  f"(+{', '.join(added[:5])}{'…' if len(added) > 5 else ''}"
                                  f"{' / −' + ', '.join(removed[:5]) if removed else ''}"
                                  f"{'…' if len(removed) > 5 else ''})")
            new_log = not CHANGE_LOG.exists()
            with CHANGE_LOG.open("a") as f:
                if new_log:
                    f.write("date,index,source,added,removed\n")
                f.write(f"{today},{name},{source},{' '.join(added)},{' '.join(removed)}\n")
        else:
            idle = (pd.Timestamp(today) - pd.Timestamp(last_changed)).days
            if idle > config.MAX_DAYS_LIST_UNCHANGED[name]:
                notes.append(f"constituent list unchanged for {idle} days (since {last_changed}) — "
                             "longer than this index's review cycle; check the source")
    out["list_last_changed"] = last_changed
    if current:
        LIST_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"date": today, "last_changed": last_changed,
                                    "source": source, "tickers": current}))
    return out


# ---------- Volume / turnover ----------

def _constituent_activity(name: str, tickers: list, now: datetime) -> dict:
    """Σ constituents' daily volume and turnover (close × volume, local currency).
    Uses the shared Yahoo download (data.yf_daily) that breadth reuses later in
    the same cron run — 500 days so breadth's 200-day MAs need no second request."""
    try:
        target = expected_session(name, now)
        sessions = calendar(name, now.year).sessions_in_range(pd.Timestamp(target)-pd.Timedelta(days=60),target)[-21:]
    except Exception as exc:
        target = None
        sessions = None
        print(f'Calendar unavailable for {name}: {exc}', flush=True)
    bars = data.yf_daily(tickers, days=500, expected_date=target, expected_sessions=sessions)
    if not bars:
        empty = pd.Series(dtype=float)
        return {"volume": empty, "turnover": empty, "traded": empty,
                "closes": pd.DataFrame(), "volumes": pd.DataFrame(), "n_list": len(tickers)}
    cutoff = pd.Timestamp(now.date()) - pd.Timedelta(days=70)
    closes = pd.DataFrame({t: b["Close"] for t, b in bars.items()})
    vdf = pd.DataFrame({t: b["Volume"] for t, b in bars.items()})
    tdf = closes * vdf
    vdf, tdf = vdf[vdf.index >= cutoff], tdf[tdf.index >= cutoff]
    return {"volume": vdf.sum(axis=1, min_count=1), "turnover": tdf.sum(axis=1, min_count=1),
            "traded": (vdf > 0).sum(axis=1), "closes": closes, "volumes": vdf,
            "n_list": len(tickers)}


def _coverage_note(tickers: list, closes: pd.DataFrame) -> str:
    """Plain-language reason for every constituent left out of the breadth
    (= the gap in Tab 3's Coverage), straight from the data."""
    no_data = [t for t in tickers if t not in closes.columns]
    young = {}
    for t in closes.columns:
        first = closes[t].first_valid_index()
        if first is None:
            no_data.append(t)
        elif closes[t].notna().sum() < config.MA_LONG:
            young[t] = first
    strip = lambda t: t.split(".")[0]
    parts = []
    if young:
        names = ", ".join(f"{strip(t)} (since {d:%Y-%m-%d})"
                          for t, d in sorted(young.items(), key=lambda kv: kv[1])[:5])
        parts.append(f"{len(young)} have fewer than {config.MA_LONG} observed price bars, so no "
                     f"{config.MA_LONG}-day MA yet: {names}{' …' if len(young) > 5 else ''}")
    if no_data:
        parts.append(f"{len(no_data)} with no Yahoo price data (cause unverified): "
                     f"{', '.join(strip(t) for t in no_data[:5])}{' …' if len(no_data) > 5 else ''}")
    return "; ".join(parts) or "All constituents counted"


def _turnover_note(tickers: list, volumes: pd.DataFrame, day) -> str:
    """Plain-language reason for every constituent missing from the volume /
    turnover sum on `day` (= the gap in Tab 2's Coverage), straight from the data."""
    strip = lambda t: t.split(".")[0]
    no_data = [t for t in tickers if t not in volumes.columns]
    on_day = volumes.loc[day] if day in volumes.index else pd.Series(dtype=float)
    idle = [t for t in volumes.columns if not on_day.get(t, 0) > 0]
    parts = []
    if idle:
        parts.append(f"{len(idle)} with zero or missing reported volume on {day:%Y-%m-%d} (cause unverified): "
                     f"{', '.join(strip(t) for t in idle[:5])}{' …' if len(idle) > 5 else ''}")
    if no_data:
        parts.append(f"{len(no_data)} with no Yahoo data (cause unverified): "
                     f"{', '.join(strip(t) for t in no_data[:5])}{' …' if len(no_data) > 5 else ''}")
    return "; ".join(parts) or "All constituents counted"


def _breadth_block(closes: pd.DataFrame, full_sessions: pd.Index, latest, tickers: list) -> dict:
    """Moving-average breadth and advance/decline from the same closes, up to the
    same latest session as turnover. Recent sessions that failed the coverage
    check are dropped so a partial day can't skew % up / down."""
    # Volume coverage must never remove valid price sessions from the MA window.
    closes = closes[closes.index <= latest]
    br = indicators.breadth_above_both_ma(closes, config.MA_SHORT, config.MA_LONG)
    ad = indicators.daily_advance_decline(closes)
    f = lambda x: float(x) if x is not None and x == x else None  # NaN → None for JSON
    return {"breadth": {
        "as_of": _fmt_date(latest),
        "pct_above_short": f(br["pct_above_short"]), "pct_above_long": f(br["pct_above_long"]),
        "pct_above_both": f(br["pct_above_both"]), "pct_below_both": f(br["pct_below_both"]),
        "pct_up": f(ad["pct_up"]), "pct_down": f(ad["pct_down"]),
        "n_stocks": int(br["n_stocks"]),   # names with enough history for the 200-day MA
        "coverage_note": _coverage_note(tickers, closes),
        "ad_history": indicators.daily_advance_decline_history(closes, days=30),
    }}


_FX = {}


def _usd_per_unit(currency: str, now: datetime) -> pd.Series:
    """Daily USD value of one unit of `currency`."""
    if currency not in _FX:
        df = yf.download(config.FX_TICKERS[currency], start=now - timedelta(days=90),
                         end=now + timedelta(days=2), progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        _FX[currency] = 1.0 / _to_dates(df)["Close"].dropna() if not df.empty else pd.Series(dtype=float)
    return _FX[currency]


def _activity_block(name: str, now: datetime, notes: list) -> dict:
    tickers = data.CONSTITUENT_FETCHERS[name]()
    tracked = _track_list(name, tickers, now, notes)
    act = _constituent_activity(name, tickers, now)
    n_list = act["n_list"]
    out = {"constituents": n_list, "data_ok": True, **tracked}
    if name == 'Taiwan' and {'0053.TW', '0056.TW'}.intersection(tickers):
        notes.append('TAIEX membership unverified: list includes ETFs (TWSE classifies 0053/0056 as ETFs); signals withheld')
        out['data_ok'] = False
    # A fallback or stale list means the sum may not be the index → no alert
    if "fallback" in (tracked.get("list_source") or "") or any("unchanged for" in n for n in notes):
        out["data_ok"] = False
    if n_list < config.CONSTITUENT_MIN[name]:
        notes.append(f"constituent list has only {n_list} names (expected ≥ "
                     f"{config.CONSTITUENT_MIN[name]}) — fallback/broken list")
        out["data_ok"] = False

    # Sessions that finished and where enough constituents reported volume
    turn = data.completed_daily_rows(act["turnover"], name, now=now)
    traded = act["traded"].reindex(turn.index)
    full = traded >= config.CONSTITUENT_COVERAGE_MIN * max(n_list, 1)
    skipped = int((~full).sum())
    turn, vol, traded = turn[full], act["volume"].reindex(turn.index)[full], traded[full]
    turn = turn[turn > 0]
    if skipped:
        out["sessions_skipped"] = skipped
    if len(turn) < WINDOW + 1:
        notes.append(f"only {len(turn)} complete sessions — need {WINDOW + 1} for a 20-day average")
        out["data_ok"] = False
        return out

    out.update(_breadth_block(act["closes"], turn.index, turn.index[-1], tickers))
    if name in config.VOLUME_CROSSCHECK:
        out.update(_crosscheck(name, float(vol.iloc[-1]), turn.index[-1], now, notes))
        if out["crosscheck"].get("ok") is False:
            out["data_ok"] = False
    latest, prior = float(turn.iloc[-1]), turn.iloc[-(WINDOW + 1):-1]
    avg = float(prior.mean())
    if latest == float(turn.iloc[-2]):
        notes.append("turnover identical to previous session — feed duplicate")
        out["data_ok"] = False
    if latest < INCOMPLETE_RATIO * float(prior.median()):
        notes.append("turnover < 40% of the 20-day median — likely an incomplete day")
        out["data_ok"] = False

    latest_day = turn.index[-1]
    incomplete = missing_bars(act['closes'], act['volumes'], tickers, turn.index[-(WINDOW+1):])
    out['missing_comparison_bars'] = incomplete
    if incomplete:
        notes.append(f'{incomplete} missing/invalid constituent bars in latest + prior 20 sessions; comparison provisional')
        out['data_ok'] = False
    latest_values = act['volumes'].reindex(columns=tickers).loc[latest_day]
    latest_closes = act['closes'].reindex(columns=tickers).loc[latest_day]
    missing = latest_values.isna() | latest_closes.isna() | (latest_values < 0)
    if missing.any():
        notes.append(f'incomplete constituent data: {int(missing.sum())}/{n_list} missing or invalid; '
                     + ', '.join(missing.index[missing][:20]))
        out['data_ok'] = False
    try:
        cal = calendar(name, now.year)
        expected_window = cal.sessions_in_range(prior.index[0], latest_day)
        actual_dates = list(turn.index[-(WINDOW+1):].strftime('%Y-%m-%d'))
        if list(expected_window.strftime('%Y-%m-%d')) != actual_dates:
            notes.append('20-day baseline skips exchange sessions; alert suppressed')
            out['data_ok'] = False
    except Exception as exc:
        notes.append(f'20-day calendar check unavailable: {exc}')
        out['data_ok'] = False
    currency = config.INDEX_CURRENCY[name]
    out.update({
        "activity_date": _fmt_date(turn.index[-1]),
        "window_start": _fmt_date(prior.index[0]),
        "window_end": _fmt_date(prior.index[-1]),
        "constituents_traded": int(traded.loc[turn.index[-1]]),
        "turnover_note": _turnover_note(tickers, act["volumes"], turn.index[-1]),
        "volume": float(vol.loc[turn.index[-1]]),
        "currency": currency,
        "turnover_local": latest,
        "turnover_avg20_local": avg,
        "turnover_dev": (latest / avg - 1) * 100 if avg > 0 else None,
    })
    if currency == "USD":
        out["turnover_usd"], out["turnover_avg20_usd"] = latest, avg
    else:
        fx = _usd_per_unit(currency, now)
        if fx.empty:
            notes.append(f"no {currency}/USD rate — USD turnover unavailable")
        else:
            usd = turn * fx.reindex(fx.index.union(turn.index)).ffill().reindex(turn.index)
            out["turnover_usd"] = float(usd.iloc[-1])
            out["turnover_avg20_usd"] = float(usd.iloc[-(WINDOW + 1):-1].mean())
    return out


# ---------- Row ----------

def build_row(name: str, now: datetime, price: dict, notes: list) -> dict:
    row = {"Index": name, "notes": notes, **price}
    n_price_notes = len(notes)
    row.update(_activity_block(name, now, notes))
    if row.pop("price_date_from_activity", False):
        # TradingView's previous-session value has no source timestamp. An
        # activity date from another feed cannot establish its observation date.
        row["price_date"] = None
        notes.append("previous-session close/RSI supplied without an observation date")
    # Constituent-list / volume checks only — what Tab 3's breadth shares with Tab 2
    row["data_notes"] = notes[n_price_notes:]

    rsi = row.get("rsi")
    if rsi is not None and rsi > config.RSI_UPPER:
        row["rsi_alert"] = f"OVERBOUGHT ({rsi:.1f})"
    elif rsi is not None and rsi < config.RSI_LOWER:
        row["rsi_alert"] = f"OVERSOLD ({rsi:.1f})"
    dev = row.get("turnover_dev")
    if row.get("data_ok") and dev is not None:
        if dev > config.TURNOVER_DEVIATION_PCT:
            row["turnover_alert"] = f"HIGH (+{dev:.1f}%)"
        elif dev < -config.TURNOVER_DEVIATION_PCT:
            row["turnover_alert"] = f"LOW ({dev:.1f}%)"

    pdate, adate = row.get("price_date"), row.get("activity_date")
    if pdate and adate and pdate != adate:
        notes.append(f"close is from {pdate} but volume/turnover from {adate}")
    return validate_row(row, now)


def build(now: datetime = None) -> dict:
    now = now or datetime.now(timezone.utc)
    # Closes + RSI for every index first (seconds), then the constituent
    # downloads (~10 min on the server). Every value is the latest *completed*
    # session; a market trading at run time contributes its previous session.
    prices, notes = {}, {}
    previous = {}
    # Prefer the 19:00 staging snapshot; use published prices only if their
    # observed date is still exactly the expected completed session.
    for path in [Path(__file__).parent/'data/index_snapshot.json', SNAPSHOT_FILE]:
        try:
            previous.update({r['Index']:r for r in json.loads(path.read_text()).get('rows',[])})
        except (OSError,ValueError,KeyError):
            pass
    for name in config.INDICES:
        notes[name] = []
        try:
            prices[name] = _price_block(name, now, notes[name])
        except Exception as e:
            prices[name] = {}
            notes[name].append(f"price fetch failed: {e}")
        try:
            target = expected_session(name, now)
            prices[name], retained = retain_dated_price(prices[name], previous.get(name,{}), target)
            if retained:
                notes[name].append(f'Retained previously saved dated price for {target}; new fetch missing/stale/undated')
        except Exception:
            pass  # validate_row will fail closed if the calendar is unavailable.
    rows = []
    for name in config.INDICES:
        print(f"  {name}...", flush=True)
        try:
            rows.append(build_row(name, now, prices[name], notes[name]))
        except Exception as e:
            rows.append({"Index": name, **prices[name], "data_ok": False,
                         "notes": notes[name] + [f"volume/turnover failed: {e}"]})
        r = rows[-1]
        tu = r.get("turnover_usd")
        print(f"    close {r.get('price_date')} {r.get('close')} rsi={r.get('rsi')} | "
              f"{r.get('activity_date')} Σ{r.get('constituents_traded')}/{r.get('constituents')} "
              f"turnover {'$%.1fB' % (tu / 1e9) if tu else '—'} Δ={r.get('turnover_dev')} "
              f"ok={r.get('data_ok')} | {'; '.join(r['notes']) or 'OK'}", flush=True)
    return {"built_at_et": now.astimezone(ET).isoformat(timespec="seconds"), "rows": rows}


def save_market_series(now: datetime) -> None:
    """VIX and GLD daily closes (latest completed US session) for Tab 1 and the
    alert email — the page reads this file instead of fetching Yahoo live."""
    series = {}
    for ticker in (config.VIX_TICKER, config.GLD_TICKER):
        df = yf.download(ticker, start=now - timedelta(days=5500), end=now + timedelta(days=2),
                         progress=False, auto_adjust=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        if df.empty:
            print(f"  ⚠️ {ticker}: no data — keeping the previous file")
            return
        close = data.completed_daily_rows(_to_dates(df.dropna(subset=["Close"]))["Close"],
                                          "S&P 500", now=now)
        series[ticker] = {"dates": [_fmt_date(d) for d in close.index],
                          "close": [round(float(v), 4) for v in close.values]}
    atomic_json(MARKET_FILE, {"built_at_et": now.astimezone(ET).isoformat(timespec="seconds"),
                             "series": series})
    print(f"✓ Saved VIX / GLD ({series[config.VIX_TICKER]['dates'][-1]}) → {MARKET_FILE}")


def build_and_save(now: datetime = None) -> dict:
    now = now or datetime.now(timezone.utc)
    save_market_series(now)
    snap = build(now)
    SNAPSHOT_FILE.parent.mkdir(exist_ok=True)
    atomic_json(SNAPSHOT_FILE, snap)
    print(f"✓ Saved index snapshot → {SNAPSHOT_FILE}")
    return snap


def save_breadth_cache(snap: dict) -> None:
    """Write data/breadth_cache.json (Tab 3, alert email, AI chat) from the
    snapshot — breadth uses the very same constituent lists and closes."""
    rows = []
    for r in snap.get("rows", []):
        b = r.get("breadth")
        if not b:
            continue
        rows.append({"Index": r["Index"], **b, "n_list": r.get("constituents"),
                     "list_source": r.get("list_source")})
    cache = OUTPUT_DIR / "breadth_cache.json"
    atomic_json(cache, {"computed_at_et": datetime.now(ET).isoformat(), "rows": rows})
    print(f"✓ Saved breadth for {len(rows)} indices → {cache}")


def load() -> dict:
    """The saved snapshot, or {} if it doesn't exist / is unreadable."""
    try:
        return json.loads(SNAPSHOT_FILE.read_text())
    except (OSError, ValueError):
        return {}


if __name__ == "__main__":
    save_breadth_cache(build_and_save())
