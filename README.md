# Global Market Sentiment Dashboard

A full-stack market-sentiment analytics platform that tracks **12 global equity indices** (US, China, Hong Kong, Japan, Taiwan, Korea) across volatility, momentum, volume, and breadth — refreshed automatically every day after the US close, with an integrated **Claude-powered AI analyst**.

Built with Python + Streamlit, deployed 24/7 on a Linux VPS behind nginx, with a cron-driven daily data pipeline and a daily AI market brief delivered by email.

**Live:** http://91.98.37.33/sentiment/

---

## Features

The dashboard is organized into four tabs plus an always-on AI sidebar.

### 1. Volatility & Sentiment
- **VIX** level and history
- **GLD / VIX** 10-week momentum (risk-on / risk-off proxy)
- **AAII Investor Sentiment** (Bullish / Neutral / Bearish, weekly)
- **S&P 500 Put/Call ratio** (volume & open-interest, from Barchart's $SPX)

### 2. Indices — RSI & Turnover
- **Close & RSI(14)**: Yahoo daily close (A-shares: Sina via AkShare) with RSI computed locally on the
  same closes; **Topix / KOSPI 200** use TradingView's dated daily index snapshot
- **Index volume and turnover = Σ of all constituents** (volume, and close × volume). Provider
  "index volume" fields were checked against official figures (Nasdaq, CNI, CSIndex) and don't
  measure the index itself; constituent sums matched every official figure available
- **Turnover** is shown in USD (comparable across markets); its **Δ vs the prior 20-session average**
  (local currency) drives the HIGH/LOW alerts at ±10%
- Built from **one daily snapshot** (`index_snapshot.py`) so close and volume/turnover dates are explicit per row
- **Data checks**: only completed sessions; a session counts only if ≥ 90% of constituents traded;
  short constituent lists, duplicate or incomplete days flagged ⚠️ (flagged rows never alert)
- 3-month RSI history chart

### 3. Breadth & Advance/Decline
- **% of constituents above the 50-day and 200-day moving averages** (single-MA and "above both")
- **Daily advancers vs decliners** and a 30-day net breadth trend
- Computed in the same daily snapshot as Tab 2 — same constituent lists, same download, same latest session
- Per-index coverage (names with a 200-day MA ÷ current constituent list)

### 4. Options
- Barchart **$SPX Put/Call ratio** — volume and open-interest ratios plus reported totals
- Snapshot is validated on read (ratios must match the totals); shows fetch time and warns when older than 96 h
- No SPY substitution — if validated $SPX data is missing, the panel says so and links to Barchart

### AI Market Analyst (sidebar + email)
- **Streaming chat** powered by the **Anthropic Claude API** — answers questions against the *current* dashboard data
- **Daily AI brief** automatically generated and prepended to the alert email

---

## Architecture

```
                         ┌──────────────────────────────┐
                         │  Streamlit app (nginx+systemd)│  ← users, 24/7
                         └───────────────┬──────────────┘
                                         │ reads
                         ┌───────────────▼──────────────┐
                         │  breadth_cache.json (cache)   │
                         └───────────────▲──────────────┘
                                         │ writes (daily)
   08:00 HKT timer ─► run_daily.py ──► update_cache.py ──► check_alerts.py ──► email
                                         │                      │
                  ┌──────────────────────┴───────┐        ai_analysis.py
                  │  data.py — unified data layer │        (Claude API)
                  └──────────────────────────────┘
                     │  multi-source + fallback
        yfinance · akshare · TradingView · index publishers / ETF holdings · Barchart · AAII
```

**Layers**

- **Data layer (`data.py`)** — a unified access layer over 6+ heterogeneous sources with priority-based **automatic fallback**, constituent lists from index publishers / exchanges / tracking-ETF holdings, and caching. Designed around real-world constraints: some sources block datacenter IPs, some return bad/missing fields, and volume units differ across markets.
- **Indicators (`indicators.py`)** — RSI (Wilder), breadth, advance/decline, and volume/turnover summaries (incomplete-bar-safe, baseline excludes the current day).
- **Daily pipeline (`update_cache.py` → `check_alerts.py`)** — fetches all constituents, computes breadth + volume for 12 indices, writes a cache file, evaluates alerts, generates the AI brief, and emails a daily summary.
- **Frontend (`dashboard.py`)** — multi-tab Streamlit UI with cache-first / live-fallback rendering, tiered alerts, and data-freshness labels.
- **AI (`ai_analysis.py`)** — formats a cross-tab snapshot and calls Claude for the streaming chat and the daily brief.
- **Out-of-band sync (`.github/workflows/`)** — a GitHub Action fetches sources that block the server's datacenter IP (AAII, Barchart) and pushes them to the server.

---

## Data Sources

| Data | Primary | Fallback |
|---|---|---|
| Index OHLCV / RSI inputs | yfinance | akshare (CN indices) |
| RSI(14) | locally computed (Wilder); TradingView for Topix / KOSPI 200 | — |
| Constituents' daily prices & volume (all markets) | yfinance (one shared download per day) | — |
| Constituent lists | Wikipedia / exchange APIs | Vanguard (Russell), Nikkei official CSV |
| Index volume & turnover | Σ constituents (Yahoo per-stock data) | — |
| AAII sentiment | aaii.com `.xls` (via GitHub Action) | local cached file |
| $SPX Put/Call | Barchart (via GitHub Action) | none — shows a warning instead of a proxy |

---

## Tech Stack

`Python` · `Streamlit` · `pandas` / `numpy` · `yfinance` · `akshare` · `tradingview-ta` · `Anthropic Claude API` · `Playwright` · `nginx` · `systemd` · `cron` · `GitHub Actions` · `Hetzner Cloud (Ubuntu)`

---

## Project Structure

```
.
├── dashboard.py          # Streamlit app (4 tabs + AI sidebar)
├── data.py               # unified multi-source data layer (fetch + fallback + cache)
├── indicators.py         # RSI, breadth, advance/decline, volume summaries
├── index_snapshot.py     # daily Tab 2 snapshot: close / RSI / volume / turnover + data checks
├── config.py             # indices, tickers, thresholds, windows
├── update_cache.py       # daily: index snapshot, then breadth → write caches
├── check_alerts.py       # daily: evaluate alerts + AI brief → email
├── alerts.py             # alert rules
├── ai_analysis.py        # Claude API: streaming chat + daily summary
├── run_daily.py          # cron entrypoint (update_cache then check_alerts)
├── requirements.txt
├── deploy/               # systemd units (dashboard + daily timer), setup scripts, DEPLOY.md
└── .github/workflows/    # AAII + Barchart sync to server
```

---

## Setup

### Local

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

streamlit run dashboard.py
```

### Configuration (all gitignored — never committed)

| File | Purpose | Required |
|---|---|---|
| `anthropic_key.json` | Claude API key for the AI features | optional (AI degrades gracefully without it) |
| `email_config.json` | SMTP credentials for the daily alert email | optional |
| `tushare_token.json` | Tushare API token (A-share data) | optional |

Copy the examples and fill in your values:

```bash
cp anthropic_key.example.json anthropic_key.json   # {"api_key": "sk-ant-..."}
cp email_config.example.json email_config.json
```

The AI key can also be supplied via the `ANTHROPIC_API_KEY` environment variable.

---

## Deployment (Linux VPS)

Runs on a Hetzner VPS at `/opt/rays` as the systemd service `streamlit`
(`127.0.0.1:8501`, `--server.baseUrlPath sentiment`), behind an nginx that also
serves a second dashboard under `/etf/`.

Full deployment and operations guide (Chinese): **[deploy/DEPLOY.md](deploy/DEPLOY.md)**.

### Daily automation

A systemd timer (`rays-daily.timer`) runs `run_daily.py` every day at **08:00 Hong Kong time**
(after the US close; fixed in HKT, so US daylight saving doesn't move it). Every index uses its
**latest completed session** — a market already trading at 08:00 HKT (Tokyo, Seoul) contributes
its previous session.

1. `update_cache.py` — builds the index snapshot: Tab 2 close / RSI / volume / turnover (`data/index_snapshot.json`) and Tab 3 breadth (`data/breadth_cache.json`) from one download
2. `check_alerts.py` — evaluates alerts, generates the AI brief, and emails the daily summary

The web app always reads the cache, so page loads stay fast regardless of pipeline runtime.

---

## Notes

- Data is **end-of-day** (refreshed daily after the US close), not intraday/streaming.
- Index "volume" units differ across markets, so only the per-row **Δ vs 20-day** is comparable — absolute values are not cross-comparable.
- AAII and Barchart block datacenter IPs; the GitHub Action fetches them from outside and syncs to the server.

---

*Personal project — market sentiment monitoring across global equity indices.*
