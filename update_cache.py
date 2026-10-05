"""
Daily data refresh (run by run_daily.py — systemd rays-daily.timer, 08:00 Hong Kong time):
  1. index snapshot — Tab 2 (close / RSI / volume / turnover) and Tab 3
     breadth, from one download of every index's constituents
     → data/index_snapshot.json, data/breadth_cache.json
  2. put/call history, Barchart chart

The dashboard reads these files on every page load — no manual button click needed.

Usage:  python update_cache.py
"""
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
import data
import index_snapshot

ET = ZoneInfo("America/New_York")
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
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

    # One pass over every index's constituents: Tab 2 (close / RSI / volume /
    # turnover) and Tab 3 (breadth) come from the same lists and downloads.
    print("Building index snapshot (Tab 2 + Tab 3 breadth)...")
    try:
        index_snapshot.save_breadth_cache(index_snapshot.build_and_save())
    except Exception as e:
        print(f"  ⚠️ index snapshot failed — Tab 2/3 keep the previous data: {e}")

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

    print(f"\n✓ Duration: {(datetime.now(ET) - started).total_seconds():.1f} seconds")


if __name__ == "__main__":
    main()
