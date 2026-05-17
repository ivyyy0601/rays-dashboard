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
    ["Volatility & Sentiment", "Indices (RSI / Turnover)", "Breadth & A/D", "Options"]
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
    st.subheader("RSI & Turnover by Index")
    st.caption(
        "📅 Each market's data becomes available at (all times in ET):  \n"
        "US ~16:15 (after 4pm close + 15 min feed delay)  ·  HK ~04:00  ·  "
        "A-share ~03:00  ·  Taiwan ~01:30  ·  Korea ~02:30"
    )
    st.caption(
        "**Turnover = volume × closing price per day** (proxy, since indices don't have "
        "direct turnover). Uses index ticker's own volume where available; falls back "
        "to canonical ETF (SOXX/IWM/1306.T) when the index has no volume data."
    )
    rows = []
    rsi_history = {}
    for name, conf in config.INDICES.items():
        df_idx = data.fetch_yf_history(conf["index"], days=config.HISTORY_DAYS)
        df_etf = data.fetch_yf_history(conf["etf"], days=config.HISTORY_DAYS)
        if df_idx.empty:
            rows.append({"Index": name, "As of": "—", "Close": "—", "RSI(14)": "—",
                         "RSI Alert": "—", "Source": "—", "Turnover": "—", "20d Avg": "—",
                         "Δ vs 20d": "—", "Turnover Alert": "no data"})
            continue
        close = df_idx["Close"]
        rsi_series = indicators.compute_rsi(close, config.RSI_WINDOW)
        rsi_history[name] = rsi_series
        rsi_latest = float(rsi_series.iloc[-1]) if not rsi_series.empty else float("nan")

        # Turnover: prefer index's own volume × close; fall back to ETF if no index volume.
        # KNOWN-BAD tickers: yfinance returns garbage volume for these, force ETF:
        #   ^RUT  → returns ^GSPC's volume (yfinance bug)
        #   ^SOX  → no volume
        FORCE_ETF_TICKERS = {"^RUT", "^SOX"}
        if conf["index"] in FORCE_ETF_TICKERS:
            idx_has_volume = False
        else:
            idx_has_volume = (
                "Volume" in df_idx.columns
                and df_idx["Volume"].tail(5).fillna(0).median() > 0
            )
        if idx_has_volume:
            tov = indicators.turnover_summary(df_idx["Close"], df_idx["Volume"], config.TURNOVER_AVG_WINDOW)
            tov_source = f"📊 Index ({conf['index']})"
        elif not df_etf.empty and "Volume" in df_etf.columns:
            tov = indicators.turnover_summary(df_etf["Close"], df_etf["Volume"], config.TURNOVER_AVG_WINDOW)
            tov_source = f"🔄 ETF Proxy ({conf['etf']})"
        else:
            tov = {"latest": float("nan"), "avg20": float("nan"), "deviation_pct": float("nan")}
            tov_source = "❌ N/A"

        if pd.notna(rsi_latest) and rsi_latest > config.RSI_UPPER:
            rsi_alert = f"OVERBOUGHT ({rsi_latest:.1f})"
        elif pd.notna(rsi_latest) and rsi_latest < config.RSI_LOWER:
            rsi_alert = f"OVERSOLD ({rsi_latest:.1f})"
        else:
            rsi_alert = "—"

        if pd.notna(tov["deviation_pct"]):
            if tov["deviation_pct"] > config.TURNOVER_DEVIATION_PCT:
                tov_alert = f"HIGH (+{tov['deviation_pct']:.1f}%)"
            elif tov["deviation_pct"] < -config.TURNOVER_DEVIATION_PCT:
                tov_alert = f"LOW ({tov['deviation_pct']:.1f}%)"
            else:
                tov_alert = "—"
        else:
            tov_alert = "—"

        rows.append({
            "Index": name,
            "As of": close.index[-1].strftime("%Y-%m-%d"),
            "Close": f"{float(close.iloc[-1]):,.2f}",
            "RSI(14)": f"{rsi_latest:.1f}" if pd.notna(rsi_latest) else "—",
            "RSI Alert": rsi_alert,
            "Turnover Source": tov_source,
            "Turnover": f"{tov['latest']/1e9:,.2f}B" if pd.notna(tov["latest"]) else "—",
            "20d Avg": f"{tov['avg20']/1e9:,.2f}B" if pd.notna(tov["avg20"]) else "—",
            "Δ vs 20d": f"{tov['deviation_pct']:+.1f}%" if pd.notna(tov["deviation_pct"]) else "—",
            "Turnover Alert": tov_alert,
        })

    # Height fits 12 indices + header without scroll
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=38 + 12 * 35)

    with st.expander("📖 Turnover data source legend (per index)"):
        st.markdown("""
**Source meaning:**
- 📊 **Index (ticker)** = uses the index's own `volume × close` directly from yfinance
- 🔄 **ETF Proxy (ticker)** = the index has no/unreliable volume on yfinance, so we use the canonical tracking ETF
- ❌ **N/A** = no volume data available

**Per-index source mapping:**

| Index | Source | Why |
|---|---|---|
| S&P 500 | 📊 Index `^GSPC` | yfinance has aggregate volume |
| Nasdaq 100 | 📊 Index `^NDX` | yfinance has aggregate volume |
| **SOX** | 🔄 ETF `SOXX` | `^SOX` has no volume on yfinance |
| **Russell 2000** | 🔄 ETF `IWM` | yfinance bug: `^RUT` volume = `^GSPC` volume |
| Hang Seng | 📊 Index `^HSI` | yfinance HK volume OK |
| CSI 300 | 📊 Index `000300.SS` | yfinance volume small but internally consistent |
| CSI 1000 | 📊 Index `000852.SS` | uses akshare fallback for full data |
| ChiNext | 📊 Index `399006.SZ` | uses akshare fallback for full data |
| Nikkei 225 | 📊 Index `^N225` | yfinance JP volume OK |
| Topix | 📊 Index `1308.T` | iShares Topix ETF (^TPX missing on yfinance) |
| Taiwan | 📊 Index `^TWII` | yfinance TW volume OK |
| KOSPI 200 | 📊 Index `^KS200` | yfinance KR volume OK |

**Why ETF proxies for SOX & Russell 2000?**
Indices themselves don't trade — they're calculated values. Some yfinance tickers (`^SOX`, `^RUT`)
return missing or wrong volume data. We use the canonical tracking ETF (SOXX, IWM) which has
real, audited trading volume. Bloomberg uses the same approach.

**Why some absolute B numbers look weird (e.g. Hang Seng 80,796B)?**
yfinance's "volume" for index tickers represents the SUM of constituent shares traded across
the index. Multiplying by the index level gives a number with no fixed unit — it's only
meaningful in **relative terms** (today vs 20-day average). The ±10% deviation alert is
mathematically correct regardless of absolute scale.
""")

    st.subheader("RSI History")
    pick = st.radio("Index", list(config.INDICES.keys()), horizontal=True, label_visibility="collapsed")
    if pick in rsi_history:
        s = rsi_history[pick].dropna()
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
        """Return (sort_key, display_str, is_proxy)."""
        theo = config.INDEX_THEORETICAL_SIZE.get(name)
        if not theo or not n:
            return (0, "—", False)
        pct = n / theo * 100
        is_proxy = pct < 50
        label = f"⚠️ {pct:.0f}% (proxy)" if is_proxy else f"{pct:.0f}%"
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
