"""
Market Sentiment Dashboard.
Run with:  streamlit run dashboard.py
"""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import config
import data
import indicators
import index_snapshot
import ai_analysis

ET = ZoneInfo("America/New_York")
CACHE_FILE = Path(__file__).parent / "data" / "breadth_cache.json"
PAGE_INDEX_SNAPSHOT = index_snapshot.load()
PAGE_BREADTH_CACHE = {
    'computed_at_et': PAGE_INDEX_SNAPSHOT.get('validated_at_et') or PAGE_INDEX_SNAPSHOT.get('built_at_et'),
    'rows': [dict(Index=r['Index'], **r['breadth'], n_list=r.get('constituents'),
                  list_source=r.get('list_source'))
             for r in PAGE_INDEX_SNAPSHOT.get('rows',[]) if r.get('breadth')],
}

# No live fetching: 19:00 ET prefetch, 20:00 ET validation/publication,
# or separate GitHub Actions feeds (AAII, Barchart).

st.set_page_config(page_title="Market Sentiment Dashboard", layout="wide")


# ---- Top navigation shared by the three dashboards on this server ----
_SITES = [("Stock Sentiment", "/dashboard/"), ("Market Sentiment", "/sentiment/"),
          ("ETF Tracker", "/etf/")]


def _site_nav(current: str):
    """A row of links to the three dashboards; `current` is highlighted."""
    links = "".join(
        f'<a href="{url}" target="_self" class="site-nav-btn{" current" if url == current else ""}">{label}</a>'
        for label, url in _SITES
    )
    st.markdown(
        "<style>"
        ".site-nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 12px}"
        ".site-nav-btn{padding:6px 14px;border:1px solid #d0d4dc;border-radius:8px;"
        "text-decoration:none!important;color:#31333f!important;font-size:0.9rem}"
        ".site-nav-btn:hover{border-color:#ff4b4b;color:#ff4b4b!important}"
        ".site-nav-btn.current{background:#31333f;border-color:#31333f;color:#fff!important}"
        "</style>"
        f'<div class="site-nav">{links}</div>',
        unsafe_allow_html=True,
    )


_site_nav("/sentiment/")
st.title("Market Sentiment Dashboard")

now_et = datetime.now(ET)
now_str = now_et.strftime('%Y-%m-%d %H:%M:%S')

# Show when the cron last actually wrote data (mtime of the cache file)
import os
cache_file_path = Path(__file__).parent / "data" / "breadth_cache.json"
if cache_file_path.exists():
    last_update_dt = datetime.fromtimestamp(cache_file_path.stat().st_mtime, tz=ET)
    last_update_str = last_update_dt.strftime('%Y-%m-%d %H:%M ET')
else:
    last_update_str = "(no data yet — first cron pending)"

st.caption(
    f"🕐 **Page opened: {now_str} ET**  ·  "
    f"📦 **Breadth cache written: {last_update_str}** — prefetch at 19:00 ET; validation starts at 20:00 ET. "
    f"Publication and email follow validation; incomplete data is flagged. Panels show their own observation or retrieval dates; "
    f"AAII and options follow separate source schedules."
)

st.info(
    "📌 All times shown are **New York time (ET)**. "
    "Each indicator's panel shows when its latest data point became available."
)

tab1, tab2, tab3, tab4 = st.tabs(
    ["Volatility & Sentiment", "Indices (RSI / Turnover)", "Breadth & A/D", "Options"]
)

def _fit_width(values, px_per_char=7, padding=24, min_px=60, max_px=1400):
    """Column width in pixels that fits the longest text in `values`."""
    longest = max((len(str(v)) for v in values), default=0)
    return int(min(max(longest * px_per_char + padding, min_px), max_px))


def _text_columns(df, names):
    """column_config sizing each named text column to its content."""
    return {n: st.column_config.TextColumn(width=_fit_width(df[n])) for n in names if n in df}


def _data_check(r, notes_key="notes"):
    """Short data-quality verdict for an index-snapshot row; full notes are in
    Tab 2's expander. Tab 3 passes "data_notes" — only the constituent-list /
    volume checks, since breadth doesn't use the index close."""
    notes = r.get(notes_key, r.get("notes")) or []
    if notes:
        return "⚠️ " + notes[0]
    if r.get("list_change"):
        return "✓ · constituents changed " + r["list_change"]
    return "✓"


# ============ TAB 1 ============
with tab1:
    # ---- VIX ----
    st.subheader("VIX")
    st.caption("📅 From the daily validated snapshot — check the observation date for freshness")
    vix = data.load_market_series(config.VIX_TICKER)
    vix = vix[vix.index >= vix.index.max() - pd.Timedelta(days=config.HISTORY_DAYS)] if not vix.empty else vix
    if not vix.empty:
        latest = float(vix["Close"].iloc[-1])
        latest_date_dt = vix.index[-1]
        et_available = latest_date_dt.strftime("%Y-%m-%d") + " 16:15 ET"
        c1, c2 = st.columns([1, 3])
        with c1:
            st.metric("Latest VIX", f"{latest:.2f}")
            st.caption(f"Trading date: **{latest_date_dt.strftime('%Y-%m-%d')}**")
            if latest > config.VIX_ALERT_THRESHOLD:
                st.error(f"ALERT: above {config.VIX_ALERT_THRESHOLD}")
            else:
                st.success(f"Below {config.VIX_ALERT_THRESHOLD}")
        with c2:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=vix.index, y=vix["Close"], name="VIX",
                hovertemplate="<b>%{x|%Y-%m-%d}</b><br>VIX: %{y:.2f}<extra></extra>",
            ))
            fig.add_hline(y=config.VIX_ALERT_THRESHOLD, line_dash="dash", line_color="red")
            fig.update_layout(height=280, margin=dict(l=0, r=0, t=10, b=0),
                              xaxis=dict(hoverformat="%Y-%m-%d", tickformat="%Y-%m-%d"))
            st.plotly_chart(fig, width="stretch")
    else:
        st.warning("VIX data unavailable.")

    # ---- GLD/VIX 10-week momentum (Zac Markovich's contrarian bottom signal) ----
    st.subheader("GLD / VIX — Weekly 10-Period Momentum")
    st.caption(
        "📅 From the daily validated snapshot (weekly bars per Zac Markovich's note; the current "
        "week's bar updates with each new completed US session)"
    )
    st.caption(
        "Logic: weekly GLD/VIX ratio minus its value 10 weeks ago. "
        "Drops below **-7** historically marked SPY bottoms (2008, 2020, 2022). "
        "Negative momentum = VIX spiking faster than gold can keep up = extreme panic."
    )
    gld_long = data.load_market_series(config.GLD_TICKER)
    vix_long = data.load_market_series(config.VIX_TICKER)

    if not gld_long.empty and not vix_long.empty:
        gld_w = gld_long["Close"].resample("W-FRI").last()
        vix_w = vix_long["Close"].resample("W-FRI").last()
        ratio_w = (gld_w / vix_w).dropna()
        momentum_10w = (ratio_w - ratio_w.shift(10)).dropna()
        latest_mom = float(momentum_10w.iloc[-1])
        latest_ratio = float(ratio_w.iloc[-1])
        latest_week_end = momentum_10w.index[-1].strftime("%Y-%m-%d")
        latest_daily_dt = max(gld_long.index[-1], vix_long.index[-1])
        et_available_gld = latest_daily_dt.strftime("%Y-%m-%d") + " 16:15 ET"

        c1, c2 = st.columns([1, 3])
        with c1:
            st.metric("Weekly GLD/VIX", f"{latest_ratio:.2f}")
            st.metric("10-Week Momentum", f"{latest_mom:+.2f}")
            st.caption(
                f"Week ending: **{latest_week_end}** (Fri).  \n"
                f"Daily data through: **{latest_daily_dt.strftime('%Y-%m-%d')}**."
            )
            if latest_mom < -7:
                st.error(f"BOTTOM SIGNAL: momentum below -7 (currently {latest_mom:.2f})")
            else:
                st.success("No signal (momentum above -7)")
            st.caption(f"Min ever: {momentum_10w.min():.2f}  ·  Max ever: {momentum_10w.max():.2f}")

        with c2:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=momentum_10w.index, y=momentum_10w.values,
                name="10-Week Momentum", line=dict(color="purple"),
                hovertemplate="<b>Week ending %{x|%Y-%m-%d}</b><br>Momentum: %{y:+.2f}<extra></extra>",
            ))
            fig.add_hline(y=-7, line_dash="dash", line_color="red",
                         annotation_text="-7 signal", annotation_position="right")
            fig.add_hline(y=0, line_color="gray", line_width=1)
            fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0),
                             yaxis_title="Momentum (ratio − ratio 10w ago)",
                             xaxis=dict(hoverformat="%Y-%m-%d", tickformat="%Y-%m-%d"))
            st.plotly_chart(fig, width="stretch")

        with st.expander("Show underlying weekly GLD/VIX ratio"):
            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(
                x=ratio_w.index, y=ratio_w.values, name="Weekly GLD/VIX",
                hovertemplate="<b>Week ending %{x|%Y-%m-%d}</b><br>GLD/VIX: %{y:.2f}<extra></extra>",
            ))
            fig2.update_layout(height=260, margin=dict(l=0, r=0, t=10, b=0),
                              xaxis=dict(hoverformat="%Y-%m-%d", tickformat="%Y-%m-%d"))
            st.plotly_chart(fig2, width="stretch")

        st.caption("Source logic: https://x.com/Zac_Markovich/status/2030370942892048797")

    # ---- AAII ----
    st.subheader("AAII Investor Sentiment")
    st.caption("📅 Updates weekly, every Thursday ~10:00 ET")
    aaii = data.fetch_aaii_sentiment()
    if aaii.empty:
        st.info(
            "Auto-fetch blocked by AAII (403). Workaround: download "
            "[sentiment.xls](https://www.aaii.com/files/surveys/sentiment.xls) "
            "in your browser, then upload it below. The file will be saved "
            "to disk so you only need to upload once per week."
        )
        uploaded = st.file_uploader("Upload AAII sentiment.xls", type=["xls", "xlsx"], key="aaii_upload")
        if uploaded is not None:
            aaii = data.parse_aaii_upload(uploaded)
            if not aaii.empty:
                st.success(f"✓ Saved to disk — {len(aaii)} rows. Refresh-safe.")

    if not aaii.empty:
        latest = aaii.iloc[-1]
        latest_aaii_dt = pd.to_datetime(latest["Date"])
        latest_aaii_date = latest_aaii_dt.strftime("%Y-%m-%d")
        st.caption(
            f"Survey week ending: **{latest_aaii_date}**  ·  "
            f"Updates every Thursday ~10:00 ET."
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Bullish", f"{latest['Bullish']*100:.1f}%")
        c2.metric("Neutral", f"{latest['Neutral']*100:.1f}%")
        c3.metric("Bearish", f"{latest['Bearish']*100:.1f}%")
        c4.metric("Net (Bull-Bear)", f"{latest['Net (Bull-Bear)']*100:+.1f}%")

        fig = go.Figure()
        recent = aaii.tail(104)
        fig.add_trace(go.Scatter(
            x=recent["Date"], y=recent["Bullish"] * 100, name="Bullish",
            line=dict(color="green"),
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Bullish: %{y:.1f}%<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=recent["Date"], y=recent["Bearish"] * 100, name="Bearish",
            line=dict(color="red"),
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Bearish: %{y:.1f}%<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=recent["Date"], y=recent["Net (Bull-Bear)"] * 100, name="Net",
            line=dict(color="blue", dash="dot"),
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Net: %{y:+.1f}%<extra></extra>",
        ))
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), yaxis_title="%",
                         xaxis=dict(hoverformat="%Y-%m-%d", tickformat="%Y-%m-%d"))
        st.plotly_chart(fig, width="stretch")

# ============ TAB 2 ============
with tab2:
    st.subheader("RSI & Turnover by Index")
    st.caption("Data checks cover freshness/completeness, not independent certification. "
               "ETF/Wikipedia constituent lists are proxies; official daily membership and "
               "independent price/volume reconciliation remain unverified. "
               "Historical comparisons use the selected current list, not historical index membership.")
    snap = PAGE_INDEX_SNAPSHOT
    snap_rows = snap.get("rows", [])
    if not snap_rows:
        st.warning("The daily index snapshot hasn't been built yet. "
                   "The scheduled pipeline prefetches at 19:00 ET and validates/publishes from 20:00 ET.")
    else:
        built = pd.to_datetime(snap["built_at_et"]).strftime("%Y-%m-%d %H:%M ET")
        st.caption(
            f"📸 Daily snapshot built **{built}**. Price and activity dates are shown per row "
            "(nothing is fetched live). **Volume** and **Turnover** sum the available data for the selected constituent list; "
            "Estimated turnover = Σ close × volume, shown in USD (not actual executed trading value). "
            "**Δ vs 20d** and **Alert** use turnover: the latest session vs the average of the "
            "20 sessions before it (local currency)."
        )

        if snap.get("corrected_at_utc"):
            st.caption(f"Data correction applied: {snap['corrected_at_utc']}. See per-row dates and source notes.")

    def _fmt_num(v, prefix=""):
        """Humanize: 9.27B, 187.70M, 325,945."""
        if v is None or not pd.notna(v):
            return "—"
        if v >= 1e12:
            return f"{prefix}{v/1e12:,.2f}T"
        if v >= 1e9:
            return f"{prefix}{v/1e9:,.2f}B"
        if v >= 1e6:
            return f"{prefix}{v/1e6:,.2f}M"
        return f"{prefix}{v:,.0f}"

    rows = []
    for r in snap_rows:
        dev = r.get("turnover_dev")
        ok = r.get("data_ok")
        rows.append({
            "Index": r["Index"],
            "Close date": r.get("price_date") or "—",
            "Close": f"{r['close']:,.2f}" if r.get("close") is not None else "—",
            "RSI(14)": f"{r['rsi']:.1f}" if r.get("rsi") is not None else "—",
            "RSI Alert": r.get("rsi_alert", "—"),
            "Data date": r.get("activity_date") or "—",
            "Coverage": (f"{r['constituents_traded']}/{r['constituents']}"
                         if r.get("constituents_traded") is not None else "—"),
            "Volume": _fmt_num(r.get("volume")),
            "Turnover (USD)": _fmt_num(r.get("turnover_usd"), "$"),
            "20d Avg (USD)": _fmt_num(r.get("turnover_avg20_usd"), "$"),
            # A row that failed its checks shows no Δ, so a bad number can't read as a signal
            "Δ vs 20d": f"{dev:+.1f}%" if (dev is not None and ok) else "—",
            "Alert": r.get("turnover_alert", "—"),
            "Data check": _data_check(r),
            "Coverage note": r.get("turnover_note") or "—",
        })

    # Stash for the AI sidebar chat (RSI + turnover Δ per index)
    st.session_state["ai_tab2_rows"] = rows

    if rows:
        df_rows = pd.DataFrame(rows)
        st.dataframe(df_rows, width="stretch", hide_index=True, height=38 + len(rows) * 35,
                     column_config=_text_columns(df_rows, ["Data check", "Coverage note"]))
        st.caption(
            "**Coverage** = constituents that traded on the data date ÷ the current constituent "
            "list (the volume / turnover sum covers these). It differs from Tab 3's coverage: a "
            "new listing trades (counted here) but has no 200-day MA yet (not counted there); a "
            "suspended stock has no volume (not counted here) but keeps its price history (counted "
            "there). **Coverage note** names every stock left out and why, from each run's data."
        )

        with st.expander("🔎 Sources, 20-day window & notes per index"):
            st.dataframe(pd.DataFrame([{
                "Index": r["Index"],
                "Close / RSI source": f"{r.get('close_source', '—')} · {r.get('rsi_source', '—')}",
                "Constituents (traded / listed)":
                    f"{r.get('constituents_traded', '—')} / {r.get('constituents', '—')}",
                "List source": r.get("list_source") or "—",
                "List last changed": r.get("list_last_changed") or "—",
                "Turnover (local)": f"{_fmt_num(r.get('turnover_local'))} {r.get('currency', '')}",
                "20d Avg (local)": f"{_fmt_num(r.get('turnover_avg20_local'))} {r.get('currency', '')}",
                "20-day window": f"{r.get('window_start', '—')} → {r.get('window_end', '—')}",
                "Volume cross-check": (
                    f"{r['crosscheck']['ref']} ÷ Σ = {r['crosscheck']['ratio']:.3f} "
                    f"(normal {r['crosscheck']['band'][0]:.2f}–{r['crosscheck']['band'][1]:.2f})"
                    if r.get("crosscheck", {}).get("ratio") else "—"),
                "Notes": "; ".join(r.get("notes", [])) or "—",
            } for r in snap_rows]), width="stretch", hide_index=True)

    with st.expander("📖 How this table is built"):
        st.markdown("""
**One daily snapshot** (19:00 ET prefetch; 20:00 ET validation, then publication). Every index
uses its **latest completed session** — a market that is trading at that moment contributes its
previous session. The table, the RSI chart, the alert email and the AI chat all read the same file.

| Column | How |
|---|---|
| Close, RSI(14) | Yahoo daily close (A-shares: Sina via AkShare); RSI computed locally on the same closes. **Topix** and **KOSPI 200**: TradingView's dated index snapshot (close + RSI) |
| Volume | Σ of every constituent's share volume that day |
| Turnover | Σ of every constituent's close × volume, in local currency; shown converted to USD |
| Δ vs 20d / Alert | turnover of the latest session vs the **average of the 20 sessions before it**, in local currency (exchange-rate moves aren't trading activity). Alert at ±10% |

**Why constituent sums:** the "index volume" fields on Yahoo / Sina were checked against official
figures and don't measure the index itself (^NDX and Sina's ChiNext report the whole exchange /
board, ^GSPC is revised to a wider number overnight, ^N225 uses an unknown unit, RTY=F is futures).
Constituent lists and vendor coverage can differ from the current official index membership.

**Turnover is close × volume**, an estimate, not the exchange-reported cash traded.
TOPIX uses the official dated JPX monthly weights, which are published with a lag.

**Constituent lists** are re-fetched every run from the index publisher, exchange or a
tracking ETF's daily holdings (SPY, Nasdaq, Nasdaq index data for SOX, iShares/Vanguard for
Russell, Nikkei, JPX, CSIndex, CNI, TWSE), so index reviews flow through automatically. Each
run is compared with the last saved list; changes show in **Data check** and are logged to
`data/constituent_changes.csv`.

**Data checks** — any of these blanks the row's Δ and suppresses its alert:
- the constituent list came from a fallback source (may be stale), or hasn't changed for longer
  than the index's normal review cycle (the source may have stopped updating);
- a session counts only if ≥ 90% of the listed constituents traded that day (latest day and
  every day in the 20-day window), so holidays / partial downloads can't distort the average;
- constituent list shorter than expected (a fallback source);
- turnover identical to the previous session (feed duplicate) or < 40% of the 20-day median
  (incomplete day).
- **Hang Seng / KOSPI 200** (lists from Wikipedia — no official source): Yahoo's index volume
  ÷ our constituent sum outside its normal band → the list is probably out of date.
""")

    st.subheader("RSI History")
    pick = st.radio("Index", list(config.INDICES.keys()), horizontal=True, label_visibility="collapsed")
    hist = next((r.get("rsi_history") for r in snap_rows if r["Index"] == pick), None)
    if hist and hist.get("values"):
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=pd.to_datetime(hist["dates"]), y=hist["values"], name=f"{pick} RSI",
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>RSI: %{y:.1f}<extra></extra>",
        ))
        fig.add_hline(y=config.RSI_UPPER, line_dash="dash", line_color="red")
        fig.add_hline(y=config.RSI_LOWER, line_dash="dash", line_color="green")
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), yaxis_range=[0, 100],
                         xaxis=dict(hoverformat="%Y-%m-%d", tickformat="%Y-%m-%d"))
        st.plotly_chart(fig, width="stretch")
    elif pick in config.TV_SNAPSHOT_INDICES:
        st.info(f"{pick} uses TradingView's daily index snapshot only — no index history is "
                "available, and an ETF history is not substituted.")
    else:
        st.info(f"No RSI history for {pick} in this snapshot.")

# ============ TAB 3 ============
with tab3:
    st.subheader("Breadth: % of Stocks Above / Below Moving Averages")
    st.caption(
        "**Columns:** `% > 50MA` and `% > 200MA` show single-MA breadth (matches StreetStats / "
        "Barchart format). `% Above Both` and `% Below Both` show the intersection (the boss's "
        "specific requirement)."
    )

    def _fmt_pct(v):
        return f"{v:.1f}%" if v is not None else "—"

    def _coverage_info(name: str, n: int, n_list=None):
        """Return (sort_key, display_str, is_proxy).

        Coverage = names with enough history for the 200-day MA ÷ the index's
        current constituent list (same list as Tab 2). Three states:
          - n == 0        → constituent source unavailable (data feed down)
          - pct < 85%     → partial (flag as approximate)
          - pct >= 85%    → full coverage
        """
        theo = n_list or config.INDEX_THEORETICAL_SIZE.get(name)
        if not theo:
            return (0, "—", False)
        if not n:
            return (0, "❌ no data", True)
        pct = n / theo * 100
        is_proxy = pct < 85
        if pct < 85:
            label = f"⚠️ {n}/{theo} ({pct:.0f}%, partial)"
        else:
            label = f"{n}/{theo} ({pct:.0f}%)"
        return (pct, label, is_proxy)

    def _render_rows(cache_rows):
        checks = {r["Index"]: _data_check(r, "data_notes")
                  for r in PAGE_INDEX_SNAPSHOT.get("rows", [])}
        order = {name: i for i, name in enumerate(config.INDICES)}
        rows = []
        for r in sorted(cache_rows, key=lambda r: order.get(r["Index"], len(order))):
            n = r.get("n_stocks", 0) or 0
            _, cov_str, is_proxy = _coverage_info(r["Index"], n, r.get("n_list"))
            rows.append({
                "Index": ("⚠️ " if is_proxy else "") + r["Index"],
                "Coverage": cov_str,
                "% > 50MA": _fmt_pct(r.get("pct_above_short")),
                "% > 200MA": _fmt_pct(r.get("pct_above_long")),
                "% Above Both": _fmt_pct(r.get("pct_above_both")),
                "% Below Both": _fmt_pct(r.get("pct_below_both")),
                "% Up Today": _fmt_pct(r.get("pct_up")),
                "% Down Today": _fmt_pct(r.get("pct_down")),
                "As of": r.get("as_of") or "—",
                "Data check": checks.get(r["Index"], "—"),
                "Coverage note": r.get("coverage_note") or "—",
            })
        df = pd.DataFrame(rows)
        # Height fits all 12 indices + header without scroll
        st.dataframe(df, width="stretch", hide_index=True, height=38 + 12 * 35,
                     column_config=_text_columns(df, ["Data check", "Coverage note"]))
        st.caption(
            "**Coverage** = constituents with 200 days of price history ÷ the index's current "
            "constituent list — the same list and download as Tab 2's volume / turnover. "
            "**Coverage note** explains every name not counted (generated from the data each run)."
        )
        st.caption(
            "**As of** = the latest completed trading session (same date as Tab 2's turnover). "
            "Markets differ by local holidays and time zones. **Data check** = the same "
            "constituent-list checks as Tab 2 (details in Tab 2's notes)."
        )

    # Read cache file populated by update_cache.py (cron / launchd job)
    cache_data = PAGE_BREADTH_CACHE

    if cache_data and cache_data.get("rows"):
        computed_at = datetime.fromisoformat(cache_data["computed_at_et"])
        age_hours = (datetime.now(ET) - computed_at).total_seconds() / 3600
        if age_hours < 26:
            st.success(
                f"📅 **Auto-updated daily**  ·  Last computed: "
                f"**{computed_at.strftime('%Y-%m-%d %H:%M ET')}** ({age_hours:.1f} hours ago)"
            )
        else:
            st.warning(
                f"⚠️ Cache is stale ({age_hours:.0f} hours old). Last computed: "
                f"{computed_at.strftime('%Y-%m-%d %H:%M ET')}. The scheduled job may have failed."
            )
        _render_rows(cache_data["rows"])

        # ---- 30-day Advance/Decline chart ----
        st.markdown("---")
        st.subheader("📈 Daily Advance / Decline — Last 30 Trading Days")
        st.caption(
            "% of index constituents that closed up vs down each day. "
            "Net = % Up − % Down (positive = bullish day, negative = bearish day)."
        )

        ad_idx = st.radio(
            "Pick index",
            list(config.INDICES.keys()),
            horizontal=True,
            key="ad_history_radio",
            label_visibility="collapsed",
        )
        ad_data = next(
            (r.get("ad_history", []) for r in cache_data["rows"] if r["Index"] == ad_idx),
            []
        )

        if ad_data:
            ad_df = pd.DataFrame(ad_data)
            ad_df["date"] = pd.to_datetime(ad_df["date"])

            fig_ad = go.Figure()
            # Up bars (green, positive)
            fig_ad.add_trace(go.Bar(
                x=ad_df["date"], y=ad_df["pct_up"], name="% Up",
                marker_color="#16a34a",
                hovertemplate="<b>%{x|%Y-%m-%d}</b><br>% Up: %{y:.1f}%<extra></extra>",
            ))
            # Down bars (red, plotted as negative for visual symmetry around 0)
            fig_ad.add_trace(go.Bar(
                x=ad_df["date"], y=-ad_df["pct_down"], name="% Down",
                marker_color="#dc2626",
                hovertemplate="<b>%{x|%Y-%m-%d}</b><br>% Down: %{customdata:.1f}%<extra></extra>",
                customdata=ad_df["pct_down"],
            ))
            # Net line (blue)
            fig_ad.add_trace(go.Scatter(
                x=ad_df["date"], y=ad_df["net"], name="Net (Up − Down)",
                line=dict(color="#1d4ed8", width=2.5),
                hovertemplate="<b>%{x|%Y-%m-%d}</b><br>Net: %{y:+.1f}%<extra></extra>",
            ))
            fig_ad.add_hline(y=0, line_color="gray", line_width=1)

            fig_ad.update_layout(
                barmode="relative",
                height=400,
                margin=dict(l=0, r=0, t=10, b=0),
                yaxis_title="% of constituents",
                xaxis=dict(hoverformat="%Y-%m-%d", tickformat="%m-%d"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(fig_ad, width="stretch")

            latest = ad_df.iloc[-1]
            c1, c2, c3 = st.columns(3)
            c1.metric("Latest % Up",   f"{latest['pct_up']:.1f}%")
            c2.metric("Latest % Down", f"{latest['pct_down']:.1f}%")
            c3.metric("Latest Net",    f"{latest['net']:+.1f}%")
        else:
            st.info(f"No 30-day A/D history for {ad_idx} yet. Will appear after next cron run.")
    else:
        st.info(
            "📦 No cache file yet. Run `python update_cache.py` once (takes 3–8 min) "
            "to populate `data/breadth_cache.json`. After that it will display here automatically. "
            "Schedule it via cron / launchd to refresh daily — see deploy notes."
        )

# ============ TAB 4 ============
with tab4:
    st.subheader("S&P 500 Put / Call Ratio")
    st.caption(
        "Scheduled SPX data snapshot — not a live feed.  \n"
        "Source: [barchart.com/stocks/quotes/\\$SPX/put-call-ratios]"
        "(https://www.barchart.com/stocks/quotes/$SPX/put-call-ratios)"
    )
    pc = data.fetch_putcall_ratio()
    if pc and pc.get("vol_ratio") is not None:
        asof_display = None
        if pc.get("asof"):
            try:
                asof_dt = pd.to_datetime(pc["asof"])
                if asof_dt.tzinfo is not None:
                    asof_dt = asof_dt.tz_convert("America/New_York")
                asof_display = asof_dt.strftime("%Y-%m-%d %H:%M ET")
            except Exception:
                asof_display = str(pc["asof"])
        st.caption(
            f"Fetched at: **{asof_display or 'unknown'}**  ·  Source: **{pc.get('source', 'unknown')}**"
        )

        st.caption("Fetch time is not the exchange observation time. Refresh this page after a sync.")
        if pc.get("stale"):
            st.warning("Update overdue: this snapshot was fetched more than 96 hours ago. Showing last validated SPX data.")

        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Volume P/C Ratio", f"{pc['vol_ratio']:.2f}",
                     help="Snapshot put volume / call volume. The source trading date is not supplied.")
            if pc['vol_ratio'] > 1.2:
                st.info("Put volume is higher than call volume in this snapshot.")
            elif pc['vol_ratio'] < 0.7:
                st.info("Call volume is higher than put volume in this snapshot.")
            else:
                st.info("Neutral range")
        with c2:
            st.metric("Open Interest P/C Ratio", f"{pc['oi_ratio']:.2f}",
                     help="Cumulative put OI / call OI — measures positioning")
        with c3:
            st.metric("Source", pc.get("source", "unknown"), help="Numeric snapshots are synced by the GitHub Actions workflow; a server run alone does not refresh them.")

        # Volume / OI summary
        st.write("**Barchart SPX reported totals:**")
        summary = pd.DataFrame([{
            "Total Call Volume": f"{pc['total_call_vol']:,}",
            "Total Put Volume":  f"{pc['total_put_vol']:,}",
            "Total Call OI":     f"{pc['total_call_oi']:,}",
            "Total Put OI":      f"{pc['total_put_oi']:,}",
        }])
        st.dataframe(summary, width="stretch", hide_index=True)

        # Per-expiration breakdown removed (only relevant when using SPY fallback, not Barchart $SPX)

        st.caption(
            "**How to read:**  \n"
            "• **Volume P/C** compares put and call trading volume in this snapshot.  \n"
            "• Volume includes both buyers and sellers; it does not establish directional positioning.  \n"
            "• **OI P/C** shows accumulated positioning over time"
        )
    else:
        st.warning("Validated SPX data is unavailable. SPY is not substituted. Source references:")
        st.markdown(
            "- https://www.barchart.com/stocks/quotes/\\$SPX/put-call-ratios\n"
            "- https://en.macromicro.me/collections/34/us-stock-relative/449/us-cboe-options-put-call-ratio\n"
            "- https://www.cboe.com/us/options/market_statistics/daily/"
        )

    st.markdown(
        "### [View on Barchart ↗]"
        "(https://www.barchart.com/stocks/quotes/$SPX/put-call-ratios)"
    )


# ============ AI ANALYSIS SIDEBAR ============
def _build_ai_snapshot():
    """Assemble a cross-tab snapshot for the AI chat from the daily-run files."""
    snap = {}
    # Tab 3 — breadth from the daily cache
    valid_indices = {r['Index'] for r in PAGE_INDEX_SNAPSHOT.get('rows',[]) if r.get('data_ok')}
    snap['breadth_rows'] = [r for r in PAGE_BREADTH_CACHE['rows'] if r['Index'] in valid_indices]
    snap['data_warnings'] = [r['Index'] + ': ' + '; '.join(r.get('notes',[]))
                             for r in PAGE_INDEX_SNAPSHOT.get('rows',[]) if not r.get('data_ok')]
    # Tab 2 — RSI + volume Δ captured during this run's render
    snap["tab2_rows"] = [r for r in st.session_state.get("ai_tab2_rows", []) if r.get('Index') in valid_indices]
    # Tab 1 — VIX, put/call, AAII (all @st.cache_data → cheap on rerun)
    try:
        vix = data.load_market_series(config.VIX_TICKER)
        if not vix.empty:
            snap["vix_close"] = float(vix["Close"].iloc[-1])
    except Exception:
        pass
    try:
        snap["putcall"] = data.fetch_putcall_ratio() or {}
    except Exception:
        pass
    try:
        aaii = data.fetch_aaii_sentiment()
        if not aaii.empty:
            r = aaii.iloc[-1]
            snap["aaii_latest"] = {"Bullish": float(r["Bullish"]),
                                   "Neutral": float(r["Neutral"]),
                                   "Bearish": float(r["Bearish"])}
    except Exception:
        pass
    return snap


with st.sidebar:
    st.header("🤖 AI Market Analyst")
    st.caption("Ask about the current dashboard data — VIX/sentiment, RSI & volume, breadth.")

    if ai_analysis.get_api_key() is None:
        st.info(
            "AI chat is not configured. Add an Anthropic API key to enable it:\n\n"
            "1. Get a key at console.anthropic.com\n"
            "2. Create `anthropic_key.json` next to the app:\n"
            "   `{\"api_key\": \"sk-ant-...\"}`\n"
            "   (or set the `ANTHROPIC_API_KEY` env var)\n"
            "3. Restart the app."
        )
    else:
        if "ai_messages" not in st.session_state:
            st.session_state.ai_messages = []

        c1, c2 = st.columns([3, 1])
        with c1:
            analyze = st.button("📋 Analyze today", use_container_width=True)
        with c2:
            if st.button("Clear", use_container_width=True):
                st.session_state.ai_messages = []
                st.rerun()

        # Render history
        for m in st.session_state.ai_messages:
            with st.chat_message(m["role"]):
                st.markdown(m["content"])

        prompt = st.chat_input("Ask about the data…")
        if analyze and not prompt:
            prompt = ("Give me a concise read of the current market data across all tabs — "
                      "what's stretched, where signals agree or diverge, and the net risk posture.")

        if prompt:
            st.session_state.ai_messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)
            snapshot = _build_ai_snapshot()
            with st.chat_message("assistant"):
                try:
                    reply = st.write_stream(
                        ai_analysis.stream_chat(st.session_state.ai_messages, snapshot)
                    )
                except Exception as e:
                    reply = f"⚠️ AI request failed: {type(e).__name__}: {e}"
                    st.error(reply)
            st.session_state.ai_messages.append({"role": "assistant", "content": reply})
