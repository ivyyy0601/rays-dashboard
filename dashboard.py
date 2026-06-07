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
import ai_analysis

ET = ZoneInfo("America/New_York")
CACHE_FILE = Path(__file__).parent / "data" / "breadth_cache.json"

# No auto-refresh — user wants daily updates, not intraday. Manual "Refresh now"
# button still works. Cron job at 5:30 AM ET handles daily breadth + alert email.

st.set_page_config(page_title="Market Sentiment Dashboard", layout="wide")
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

header_c1, header_c2 = st.columns([4, 1])
with header_c1:
    st.caption(
        f"🕐 **Page opened: {now_str} ET**  ·  "
        f"📦 **Data last updated by cron: {last_update_str}** (daily at 19:40 ET). "
        f"Click 🔄 to force fresh fetch (lightweight data only)."
    )
with header_c2:
    if st.button("🔄 Refresh now", help="Force re-download lightweight data (VIX, RSI, etc.)"):
        st.cache_data.clear()
        st.rerun()

st.info(
    "📌 All times shown are **New York time (ET)**. "
    "Each indicator's panel shows when its latest data point became available."
)

tab1, tab2, tab3, tab4 = st.tabs(
    ["Volatility & Sentiment", "Indices (RSI / Volume)", "Breadth & A/D", "Options"]
)

# ============ TAB 1 ============
with tab1:
    # ---- VIX ----
    st.subheader("VIX")
    st.caption("📅 Updates daily after US market close (~16:15 ET)")
    vix = data.fetch_yf_history(config.VIX_TICKER, days=config.HISTORY_DAYS)
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
        "📅 Updates daily after US close ~16:15 ET (uses weekly bars per Zac Markovich's "
        "note, but the current week's bar updates with every new US daily close)"
    )
    st.caption(
        "Logic: weekly GLD/VIX ratio minus its value 10 weeks ago. "
        "Drops below **-7** historically marked SPY bottoms (2008, 2020, 2022). "
        "Negative momentum = VIX spiking faster than gold can keep up = extreme panic."
    )
    gld_long = data.fetch_yf_history(config.GLD_TICKER, days=5500)
    vix_long = data.fetch_yf_history(config.VIX_TICKER, days=5500)

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
    st.subheader("RSI & Volume by Index")
    st.caption(
        "📅 Each market's data becomes available at (all times in ET):  \n"
        "US ~16:15 (after 4pm close + 15 min feed delay)  ·  HK ~04:00  ·  "
        "A-share ~03:00  ·  Taiwan ~01:30  ·  Korea ~02:30"
    )
    st.caption(
        "**Volume = real daily trading volume** (no ETF). Each index uses its own "
        "aggregate volume; SOX sums its 30 constituents (^SOX has none) and Russell 2000 "
        "uses RTY=F futures (^RUT volume is a yfinance bug). Units differ (shares / "
        "contracts), so compare only the per-row **Δ vs 20d**, not absolute values."
    )
    def _fmt_vol(v):
        """Humanize share volume: 9.27B, 187.70M, 325,945."""
        if not pd.notna(v):
            return "—"
        if v >= 1e9:
            return f"{v/1e9:,.2f}B"
        if v >= 1e6:
            return f"{v/1e6:,.2f}M"
        return f"{v:,.0f}"

    def _fmt_amt(v):
        """Humanize a turnover amount (native currency): 19.70B, 444.0M, 8.4M."""
        if not pd.notna(v):
            return "—"
        if v >= 1e9:
            return f"{v/1e9:,.2f}B"
        if v >= 1e6:
            return f"{v/1e6:,.1f}M"
        return f"{v:,.0f}"

    # Volume from the daily cache (heavy constituent sums like Topix run in cron).
    # Indices missing here (e.g. Russell) are computed live below.
    _vol_cache = {}
    if CACHE_FILE.exists():
        try:
            _c = json.loads(CACHE_FILE.read_text())
            for r in _c.get("rows", []):
                if r.get("volume_dev") is not None:
                    _vol_cache[r["Index"]] = r
        except Exception:
            _vol_cache = {}

    rows = []
    rsi_history = {}
    for name, conf in config.INDICES.items():
        df_idx = data.fetch_yf_history(conf["index"], days=config.HISTORY_DAYS)
        if df_idx.empty:
            rows.append({"Index": name, "As of": "—", "Close": "—", "RSI(14)": "—",
                         "RSI Alert": "—", "Volume Source": "—", "Volume": "—",
                         "20d Avg": "—", "Δ vs 20d": "—", "Vol Alert": "no data"})
            continue
        close = df_idx["Close"]
        # History chart: RSI computed locally (TradingView gives no historical series).
        rsi_series = indicators.compute_rsi(close, config.RSI_WINDOW)
        rsi_history[name] = rsi_series
        # Table RSI(14): pulled directly from TradingView (matches tradingview.com).
        # Falls back to the locally-computed value only if TradingView is unavailable.
        rsi_latest = data.fetch_tv_rsi(name)
        if rsi_latest is None:
            rsi_latest = float(rsi_series.iloc[-1]) if not rsi_series.empty else float("nan")

        # Trading VOLUME (real, no ETF). Prefer the daily cache (so heavy sums like
        # Topix don't run on page load); compute live for anything not cached
        # (e.g. Russell 2000 → RTY=F futures, fast). Source per index:
        #   Russell 2000 → RTY=F futures (^RUT volume is a yfinance bug)
        #   SOX / Topix  → Σ constituents (their own tickers have no usable volume)
        #   all others   → the index ticker's own aggregate volume
        vc = _vol_cache.get(name)
        if vc:
            vsum = {"latest": vc.get("volume_latest"), "avg20": vc.get("volume_avg20"),
                    "deviation_pct": vc.get("volume_dev"),
                    "source": vc.get("volume_source") or "?", "unit": vc.get("volume_unit") or ""}
        else:
            vsum = data.fetch_index_volume_summary(name, config.TURNOVER_AVG_WINDOW)
        vdev = vsum.get("deviation_pct") if vsum else None
        vol_source = vsum.get("source", "❌ N/A") if vsum else "❌ N/A"
        vol_unit = vsum.get("unit", "") if vsum else ""

        if pd.notna(rsi_latest) and rsi_latest > config.RSI_UPPER:
            rsi_alert = f"OVERBOUGHT ({rsi_latest:.1f})"
        elif pd.notna(rsi_latest) and rsi_latest < config.RSI_LOWER:
            rsi_alert = f"OVERSOLD ({rsi_latest:.1f})"
        else:
            rsi_alert = "—"

        if vdev is not None and vdev == vdev:
            if vdev > config.TURNOVER_DEVIATION_PCT:
                vol_alert = f"HIGH (+{vdev:.1f}%)"
            elif vdev < -config.TURNOVER_DEVIATION_PCT:
                vol_alert = f"LOW ({vdev:.1f}%)"
            else:
                vol_alert = "—"
        else:
            vol_alert = "—"

        rows.append({
            "Index": name,
            "As of": close.index[-1].strftime("%Y-%m-%d"),
            "Close": f"{float(close.iloc[-1]):,.2f}",
            "RSI(14)": f"{rsi_latest:.1f}" if pd.notna(rsi_latest) else "—",
            "RSI Alert": rsi_alert,
            "Volume Source": f"{vol_source} ({vol_unit})" if vsum else "❌ N/A",
            "Volume": _fmt_vol(vsum.get("latest")) if vsum else "—",
            "20d Avg": _fmt_vol(vsum.get("avg20")) if vsum else "—",
            "Δ vs 20d": f"{vdev:+.1f}%" if (vdev is not None and vdev == vdev) else "—",
            "Vol Alert": vol_alert,
        })

    # Stash for the AI sidebar chat (RSI + volume Δ per index)
    st.session_state["ai_tab2_rows"] = rows

    # Height fits 12 indices + header without scroll
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=38 + 12 * 35)

    with st.expander("📖 How Volume is calculated"):
        st.markdown("""
**Real daily trading volume — no ETF anywhere.** The `Volume Source` column shows each row's
source:

| Indices | Source | Unit |
|---|---|---|
| S&P 500, Nasdaq 100, Hang Seng, Nikkei 225, Topix, Taiwan, KOSPI 200, CSI 300/1000, ChiNext | the index ticker's **own aggregate volume** (sum of constituent shares traded), straight from the data feed | shares |
| **SOX** | **Σ of its 30 constituents'** volume — `^SOX` reports no volume of its own | shares |
| **Russell 2000** | **RTY=F futures** contract volume — `^RUT`'s reported volume is a yfinance bug (it returns `^GSPC`'s number) | contracts |

**Units differ** (shares vs contracts, and shares mean different things across markets), so
**don't compare the absolute `Volume` / `20d Avg` across rows** — only the per-row **`Δ vs 20d`**
is meaningful, and that ratio is unit-agnostic.

**Two correctness fixes:**
- **Incomplete bars dropped** — a not-yet-settled last bar (< 40% of the recent median) is
  excluded, so it can't fire a false `LOW` alert.
- **Baseline excludes today** — today's value is compared against the *prior* 20-day average,
  not an average that already contains it.

*Why not "turnover" (volume × price)?* An index has no real per-share price (only a point
level), so `level × volume` is a meaningless number. Real turnover needs summing every
constituent's `price × volume`; trading **volume** is published directly and is the clean,
ETF-free activity signal.
""")

    st.subheader("RSI History")
    pick = st.radio("Index", list(config.INDICES.keys()), horizontal=True, label_visibility="collapsed")
    if pick in rsi_history:
        # Compute RSI on the full fetched series (accurate warmup), then show
        # only the most recent RSI_CHART_DAYS trading days.
        s = rsi_history[pick].dropna().tail(config.RSI_CHART_DAYS)
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, name=f"{pick} RSI",
            hovertemplate="<b>%{x|%Y-%m-%d}</b><br>RSI: %{y:.1f}<extra></extra>",
        ))
        fig.add_hline(y=config.RSI_UPPER, line_dash="dash", line_color="red")
        fig.add_hline(y=config.RSI_LOWER, line_dash="dash", line_color="green")
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), yaxis_range=[0, 100],
                         xaxis=dict(hoverformat="%Y-%m-%d", tickformat="%Y-%m-%d"))
        st.plotly_chart(fig, width="stretch")

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

    def _coverage_info(name: str, n: int):
        """Return (sort_key, display_str, is_proxy).

        Distinguishes three states so the table never just shows a bare blank:
          - n == 0        → constituent source unavailable (data feed down)
          - pct < 85%     → partial list (flag as approximate)
          - pct >= 85%    → full coverage
        """
        theo = config.INDEX_THEORETICAL_SIZE.get(name)
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
        rows = []
        for r in cache_rows:
            n = r.get("n_stocks", 0) or 0
            sort_key, cov_str, is_proxy = _coverage_info(r["Index"], n)
            rows.append({
                "_sort": sort_key,
                "Index": ("⚠️ " if is_proxy else "") + r["Index"],
                "Coverage": cov_str,
                "% > 50MA": _fmt_pct(r.get("pct_above_short")),
                "% > 200MA": _fmt_pct(r.get("pct_above_long")),
                "% Above Both": _fmt_pct(r.get("pct_above_both")),
                "% Below Both": _fmt_pct(r.get("pct_below_both")),
                "% Up Today": _fmt_pct(r.get("pct_up")),
                "% Down Today": _fmt_pct(r.get("pct_down")),
                "As of": r.get("as_of") or "—",
            })
        # Sort by coverage descending
        rows.sort(key=lambda r: r["_sort"], reverse=True)
        df = pd.DataFrame(rows).drop(columns=["_sort"])
        # Height fits all 12 indices + header without scroll
        st.dataframe(df, width="stretch", hide_index=True, height=38 + 12 * 35)
        st.caption(
            "**Coverage** = constituents fetched ÷ index theoretical size. "
            "All 12 indices ≥ 86% covered. Missing stocks are usually recently-listed "
            "ones that don't have 200 days of history yet (excluded from MA computation)."
        )
        st.caption(
            "**As of** = trading date of underlying close prices. Different markets close on "
            "different days due to local holidays / time zones (e.g. US data may be 1 day "
            "older than HK data depending on weekday)."
        )

    # Read cache file populated by update_cache.py (cron / launchd job)
    cache_data = None
    if CACHE_FILE.exists():
        try:
            cache_data = json.loads(CACHE_FILE.read_text())
        except Exception:
            cache_data = None

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
        st.caption(
            "**Coverage** = constituents fetched ÷ index theoretical size. "
            "All 12 indices ≥ 86% covered (most ≥ 95%). Missing stocks are recent IPOs "
            "without 200-day history."
        )
        st.caption(
            "**As of** = trading date of underlying close prices. Different markets close "
            "on different days due to local holidays / time zones."
        )

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

    # Manual refresh button (also useful if cache is stale and you can't wait for cron)
    with st.expander("⚙️ Manual recompute (only if cron job hasn't run yet)"):
        if st.button("Recompute now (3–8 minutes)"):
            with st.spinner("Computing breadth for 12 indices..."):
                rows = []
                for name in config.INDICES:
                    fetcher = data.CONSTITUENT_FETCHERS.get(name)
                    if fetcher is None:
                        continue
                    tickers = fetcher()
                    if not tickers:
                        continue
                    closes = data.fetch_batch_close(tickers, days=500)
                    br = indicators.breadth_above_both_ma(closes, config.MA_SHORT, config.MA_LONG)
                    ad = indicators.daily_advance_decline(closes)
                    as_of = closes.index[-1].strftime("%Y-%m-%d") if not closes.empty else None
                    rows.append({
                        "Index": name, "as_of": as_of,
                        "pct_above_short": br["pct_above_short"] if pd.notna(br["pct_above_short"]) else None,
                        "pct_above_long":  br["pct_above_long"]  if pd.notna(br["pct_above_long"])  else None,
                        "pct_above_both":  br["pct_above_both"]  if pd.notna(br["pct_above_both"])  else None,
                        "pct_below_both":  br["pct_below_both"]  if pd.notna(br["pct_below_both"])  else None,
                        "pct_up":          ad["pct_up"]          if pd.notna(ad["pct_up"])          else None,
                        "pct_down":        ad["pct_down"]        if pd.notna(ad["pct_down"])        else None,
                        "n_stocks": br["n_stocks"],
                    })
                CACHE_FILE.parent.mkdir(exist_ok=True)
                CACHE_FILE.write_text(json.dumps({
                    "computed_at_et": datetime.now(ET).isoformat(),
                    "rows": rows,
                }, indent=2, default=str))
                st.success("Done. Refresh the page to see updated data.")
                st.rerun()

# ============ TAB 4 ============
with tab4:
    st.subheader("S&P 500 Put / Call Ratio")
    st.caption(
        "📅 Live during US market hours, frozen after 16:00 ET close.  \n"
        "Source: [barchart.com/stocks/quotes/\\$SPX/put-call-ratios]"
        "(https://www.barchart.com/stocks/quotes/$SPX/put-call-ratios)"
    )
    pc = data.fetch_putcall_ratio()
    if pc and pc.get("vol_ratio") is not None:
        c1, c2, c3 = st.columns(3)
        with c1:
            st.metric("Volume P/C Ratio", f"{pc['vol_ratio']:.2f}",
                     help="Today's put volume / call volume — measures the day's flow")
            if pc['vol_ratio'] > 1.2:
                st.error("⚠️ Bearish: heavy put buying")
            elif pc['vol_ratio'] < 0.7:
                st.success("📈 Bullish: heavy call buying")
            else:
                st.info("Neutral range")
        with c2:
            st.metric("Open Interest P/C Ratio", f"{pc['oi_ratio']:.2f}",
                     help="Cumulative put OI / call OI — measures positioning")
        with c3:
            st.metric("Source", "Barchart $SPX", help="Auto-scraped, refreshed by daily cron")

        # Volume / OI summary
        st.write("**Aggregate (next 4 expirations):**")
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
            "• **Volume P/C > 1.2** → heavy put buying today (fear / hedging) — often contrarian bullish at extremes  \n"
            "• **Volume P/C < 0.7** → heavy call buying (greed) — often contrarian bearish at extremes  \n"
            "• **OI P/C** shows accumulated positioning over time"
        )
    else:
        st.warning("Could not fetch SPY options data. Manual references:")
        st.markdown(
            "- https://www.barchart.com/stocks/quotes/\\$SPX/put-call-ratios\n"
            "- https://en.macromicro.me/collections/34/us-stock-relative/449/us-cboe-options-put-call-ratio\n"
            "- https://www.cboe.com/us/options/market_statistics/daily/"
        )

    # ---- Barchart's actual chart, captured server-side via Playwright ----
    st.markdown("---")
    st.subheader("📈 SPX vs. Put/Call Ratios — Historical Chart")
    st.caption(
        "Live snapshot of Barchart's official chart "
        "(stock price + Volume Ratio + Open Interest Ratio over the last ~10 months). "
        "Updated daily at 8:00 PM ET."
    )
    chart_path = data.fetch_barchart_pc_chart()
    if chart_path:
        st.image(chart_path, width="stretch")
    else:
        st.warning(
            "Could not capture Barchart chart. Open it directly: "
            "[barchart.com](https://www.barchart.com/stocks/quotes/$SPX/put-call-ratios)"
        )


# ============ AI ANALYSIS SIDEBAR ============
def _build_ai_snapshot():
    """Assemble a cross-tab snapshot for the AI chat from cache + cached live data."""
    snap = {}
    # Tab 3 — breadth from the daily cache
    if CACHE_FILE.exists():
        try:
            snap["breadth_rows"] = json.loads(CACHE_FILE.read_text()).get("rows", [])
        except Exception:
            pass
    # Tab 2 — RSI + volume Δ captured during this run's render
    snap["tab2_rows"] = st.session_state.get("ai_tab2_rows", [])
    # Tab 1 — VIX, put/call, AAII (all @st.cache_data → cheap on rerun)
    try:
        vix = data.fetch_yf_history(config.VIX_TICKER, days=60)
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
