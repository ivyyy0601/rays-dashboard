"""
Pre-fetch all breadth data and save to a JSON cache file.

Run this daily via cron / launchd / Task Scheduler. The dashboard reads from
the cache file on every page load — no manual button click needed.

Usage:  python update_cache.py
"""
import json
import logging
import sys
import types
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


# Bypass streamlit's @st.cache_data decorator so this works as a plain script
fake_st = types.ModuleType("streamlit")
def _cache_data(*a, **k):
    if a and callable(a[0]):
        return a[0]
    def deco(f): return f
    return deco
_cache_data.clear = lambda: None
fake_st.cache_data = _cache_data
class _NoopProgress:
    def progress(self, *a, **k): pass
    def empty(self): pass
fake_st.progress = lambda *a, **k: _NoopProgress()
fake_st.warning = fake_st.info = fake_st.error = lambda *a, **k: None
sys.modules["streamlit"] = fake_st

# Now we can import dashboard modules
import config
import data
import indicators

ET = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
CACHE_FILE = DATA_DIR / "breadth_cache.json"
PUTCALL_HISTORY = DATA_DIR / "putcall_history.csv"

logging.disable(logging.CRITICAL)


def update_putcall_history():
    """Append today's put/call ratio to the rolling history CSV."""
    import pandas as pd
    pc = data.fetch_putcall_ratio()
    if not pc or pc.get("vol_ratio") is None:
        print("  Put/Call: failed to fetch, skipping history update")
        return
    today = datetime.now(ET).strftime("%Y-%m-%d")
    new_row = {
        "date": today,
        "vol_ratio": pc["vol_ratio"],
        "oi_ratio": pc["oi_ratio"],
        "put_vol": pc.get("total_put_vol"),
        "call_vol": pc.get("total_call_vol"),
        "put_oi": pc.get("total_put_oi"),
        "call_oi": pc.get("total_call_oi"),
        "source": pc.get("source", ""),
    }
    if PUTCALL_HISTORY.exists():
        hist = pd.read_csv(PUTCALL_HISTORY)
        hist = hist[hist["date"] != today]  # remove same-day if running again
        hist = pd.concat([hist, pd.DataFrame([new_row])], ignore_index=True)
    else:
        hist = pd.DataFrame([new_row])
    hist = hist.sort_values("date").reset_index(drop=True)
    hist.to_csv(PUTCALL_HISTORY, index=False)
    print(f"  Put/Call: vol={pc['vol_ratio']:.2f}, oi={pc['oi_ratio']:.2f} → {len(hist)} rows in history")


def main():
    started = datetime.now(ET)
    print(f"[{started.strftime('%Y-%m-%d %H:%M:%S ET')}] Starting cache update...")

    rows = []
    for name in config.INDICES:
        print(f"  Processing {name}...", flush=True)
        fetcher = data.CONSTITUENT_FETCHERS.get(name)
        if fetcher is None:
            print(f"    skipped (no constituent fetcher)")
            continue
        tickers = fetcher()
        if not tickers:
            print(f"    failed to fetch tickers")
            continue
        closes = data.fetch_batch_close(tickers, days=500)
        br = indicators.breadth_above_both_ma(closes, config.MA_SHORT, config.MA_LONG)
        ad = indicators.daily_advance_decline(closes)
        ad_history = indicators.daily_advance_decline_history(closes, days=30)
        as_of = closes.index[-1].strftime("%Y-%m-%d") if not closes.empty else None
        rows.append({
            "Index": name,
            "as_of": as_of,
            "pct_above_short": float(br["pct_above_short"]) if br["pct_above_short"] == br["pct_above_short"] else None,
            "pct_above_long":  float(br["pct_above_long"])  if br["pct_above_long"]  == br["pct_above_long"]  else None,
            "pct_above_both":  float(br["pct_above_both"])  if br["pct_above_both"]  == br["pct_above_both"]  else None,
            "pct_below_both":  float(br["pct_below_both"])  if br["pct_below_both"]  == br["pct_below_both"]  else None,
            "pct_up":          float(ad["pct_up"])          if ad["pct_up"]          == ad["pct_up"]          else None,
            "pct_down":        float(ad["pct_down"])        if ad["pct_down"]        == ad["pct_down"]        else None,
            "n_stocks": int(br["n_stocks"]),
            "ad_history": ad_history,  # list of {date, pct_up, pct_down, net} for last 30 days
        })
        print(f"    n={br['n_stocks']}, above_both={br['pct_above_both']:.1f}%, as_of={as_of}, ad_history={len(ad_history)}d")

    print("\nUpdating Put/Call history...")
    update_putcall_history()

    print("\nCapturing Barchart chart screenshot...")
    try:
        # Force fresh capture (bypass cache if .clear exists)
        if hasattr(data.fetch_barchart_pc_chart, "clear"):
            data.fetch_barchart_pc_chart.clear()
        path = data.fetch_barchart_pc_chart()
        if path:
            print(f"  ✓ Barchart chart saved: {path}")
        else:
            print(f"  ⚠️ Barchart chart capture failed (server likely blocked by Cloudflare — sync from Mac instead)")
    except Exception as e:
        print(f"  ⚠️ Barchart capture exception: {e}")

    finished = datetime.now(ET)
    output = {
        "computed_at_et": finished.isoformat(),
        "duration_seconds": (finished - started).total_seconds(),
        "rows": rows,
    }
    CACHE_FILE.write_text(json.dumps(output, indent=2, default=str))
    print(f"\n✓ Saved {len(rows)} rows to {CACHE_FILE}")
    print(f"✓ Duration: {output['duration_seconds']:.1f} seconds")


if __name__ == "__main__":
    main()
