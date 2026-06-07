# Global Market Sentiment Dashboard

A full-stack market-sentiment analytics platform that tracks **12 global equity indices** (US, China, Hong Kong, Japan, Taiwan, Korea) across volatility, momentum, volume, and breadth — refreshed automatically every day after the US close, with an integrated **Claude-powered AI analyst**.

Built with Python + Streamlit, deployed 24/7 on a Linux VPS behind nginx, with a cron-driven daily data pipeline and a daily AI market brief delivered by email.

---

## Features

The dashboard is organized into four tabs plus an always-on AI sidebar.

### 1. Volatility & Sentiment
- **VIX** level and history
- **GLD / VIX** 10-week momentum (risk-on / risk-off proxy)
- **AAII Investor Sentiment** (Bullish / Neutral / Bearish, weekly)
- **S&P 500 Put/Call ratio** (volume & open-interest, from Barchart's $SPX)

### 2. Indices — RSI & Volume
- **RSI(14)** pulled directly from TradingView so values match what you see on tradingview.com
- **Real daily trading volume** with **no ETF proxies**:
  - most indices use their own aggregate volume
  - **SOX / Topix** sum their constituents (their own tickers report no usable volume)
  - **Russell 2000** uses front-month futures volume (the index ticker's volume is a data-feed bug)
- Per-index **Δ vs 20-day average** with HIGH/LOW activity alerts
- 3-month RSI history chart (computed on the full series, displayed for the recent window)

### 3. Breadth & Advance/Decline
- **% of constituents above the 50-day and 200-day moving averages** (single-MA and "above both")
- **Daily advancers vs decliners** and a 30-day net breadth trend
- Per-index coverage indicator (constituents fetched ÷ index size)

### 4. Options
- Barchart **$SPX Put/Call ratio** chart (price + volume ratio + open-interest ratio)

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
   cron 19:40 ET ──► run_daily.py ──► update_cache.py ──► check_alerts.py ──► email
                                         │                      │
                  ┌──────────────────────┴───────┐        ai_analysis.py
                  │  data.py — unified data layer │        (Claude API)
                  └──────────────────────────────┘
                     │  multi-source + fallback
        yfinance · akshare · Baostock · TradingView · Vanguard/iShares · Barchart · AAII
```

**Layers**

- **Data layer (`data.py`)** — a unified access layer over 6+ heterogeneous sources with priority-based **automatic fallback** (e.g. A-shares: akshare → Baostock → yfinance), constituent-list fetching, and caching. Designed around real-world constraints: some sources block datacenter IPs, some return bad/missing fields, and volume units differ across markets.
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
| RSI(14) | TradingView | locally computed (Wilder) |
| A-share constituents' prices | akshare | Baostock → yfinance |
| Constituent lists | Wikipedia / exchange APIs | Vanguard (Russell), Nikkei official CSV |
| Volume (SOX/Topix) | constituent aggregation | — |
| Volume (Russell 2000) | futures (`RTY=F`) | — |
| AAII sentiment | aaii.com `.xls` (via GitHub Action) | local cached file |
| $SPX Put/Call | Barchart (via GitHub Action) | SPY options proxy (yfinance) |

---

## Tech Stack

`Python` · `Streamlit` · `pandas` / `numpy` · `yfinance` · `akshare` / `Baostock` · `tradingview-ta` · `Anthropic Claude API` · `Playwright` · `nginx` · `systemd` · `cron` · `GitHub Actions` · `Hetzner Cloud (Ubuntu)`

---

## Project Structure

```
.
├── dashboard.py          # Streamlit app (4 tabs + AI sidebar)
├── data.py               # unified multi-source data layer (fetch + fallback + cache)
├── indicators.py         # RSI, breadth, advance/decline, volume summaries
├── config.py             # indices, tickers, thresholds, windows
├── update_cache.py       # daily: fetch constituents → compute → write cache
├── check_alerts.py       # daily: evaluate alerts + AI brief → email
├── alerts.py             # alert rules
├── ai_analysis.py        # Claude API: streaming chat + daily summary
├── run_daily.py          # cron entrypoint (update_cache then check_alerts)
├── requirements.txt
├── deploy/               # nginx, systemd, cron, server setup scripts
└── .github/workflows/    # AAII + Barchart sync to server
```

---

## Setup

### Local

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium          # for the Barchart chart

streamlit run dashboard.py
```

### Configuration (all gitignored — never committed)

| File | Purpose | Required |
|---|---|---|
| `anthropic_key.json` | Claude API key for the AI features | optional (AI degrades gracefully without it) |
| `email_config.json` | SMTP credentials for the daily alert email | optional |

Copy the examples and fill in your values:

```bash
cp anthropic_key.example.json anthropic_key.json   # {"api_key": "sk-ant-..."}
cp email_config.example.json email_config.json
```

The AI key can also be supplied via the `ANTHROPIC_API_KEY` environment variable.

---

## Deployment (Linux VPS)

```bash
# 1. upload code
bash deploy/upload_to_server.sh root@YOUR_SERVER_IP
# 2. install deps, systemd service, nginx, and the daily cron
ssh root@YOUR_SERVER_IP 'bash /opt/rays/deploy/finalize.sh'
# 3. build the first cache
ssh root@YOUR_SERVER_IP 'sudo -u rays /opt/rays/venv/bin/python /opt/rays/update_cache.py'
```

The dashboard is then served on port 80 via nginx → Streamlit (systemd service `streamlit`).

### Daily automation

A cron job runs `run_daily.py` every day at **19:40 ET** (after the US close):

1. `update_cache.py` — rebuilds breadth + volume for all 12 indices into `data/breadth_cache.json`
2. `check_alerts.py` — evaluates alerts, generates the AI brief, and emails the daily summary

The web app always reads the cache, so page loads stay fast regardless of pipeline runtime.

---

## Notes

- Data is **end-of-day** (refreshed daily after the US close), not intraday/streaming.
- Index "volume" units differ across markets, so only the per-row **Δ vs 20-day** is comparable — absolute values are not cross-comparable.
- AAII and Barchart block datacenter IPs; the GitHub Action fetches them from outside and syncs to the server.

---

*Personal project — market sentiment monitoring across global equity indices.*
