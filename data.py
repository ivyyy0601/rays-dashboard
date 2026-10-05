"""All data fetching. Cached aggressively — reruns won't redownload."""
import logging
import subprocess
import warnings
from datetime import datetime, timedelta
from pathlib import Path
import io

import pandas as pd
import requests
import streamlit as st
import yfinance as yf

import config

# Silence yfinance "no data found" / "1 Failed download" noise — we handle empty data gracefully
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)


# ---------- Price history ----------

AKSHARE_ONLY_INDICES = {"000300.SS": "sh000300", "000852.SS": "sh000852", "399006.SZ": "sz399006"}


def akshare_index_source(ticker: str) -> str:
    return f"Sina via AkShare ({AKSHARE_ONLY_INDICES[ticker]})"


def completed_daily_rows(frame, name: str, now=None):
    """Exclude the current local-market session until close plus 30 minutes.

    Dates come from the feed; this is a conservative regular-session cutoff,
    not a replacement for an exchange calendar or a feed-finality guarantee.
    """
    markets = {
        "Hang Seng": ("Asia/Hong_Kong", 16, 30),
        "CSI 300": ("Asia/Shanghai", 15, 30),
        "CSI 1000": ("Asia/Shanghai", 15, 30),
        "ChiNext": ("Asia/Shanghai", 15, 30),
        "Nikkei 225": ("Asia/Tokyo", 16, 0),
        "Topix": ("Asia/Tokyo", 16, 0),
        "Taiwan": ("Asia/Taipei", 14, 0),
        "KOSPI 200": ("Asia/Seoul", 16, 0),
    }
    zone, hour, minute = markets.get(name, ("America/New_York", 16, 30))
    current = pd.Timestamp.now(tz=zone) if now is None else pd.Timestamp(now).tz_convert(zone)
    cutoff = current.normalize() + pd.Timedelta(hours=hour, minutes=minute)
    idx = pd.DatetimeIndex(frame.index)
    dates = (idx.tz_convert(zone) if idx.tz is not None else idx).date
    mask = (dates < current.date()) | ((dates == current.date()) & (current >= cutoff))
    return frame.loc[mask].copy()

MARKET_SERIES_FILE = Path(__file__).parent / "data" / "market_series.json"


def load_market_series(ticker: str) -> pd.DataFrame:
    """Daily closes for VIX / GLD written by the daily run (index_snapshot.py) —
    latest completed US session. DataFrame[Close], empty if not available."""
    try:
        version = MARKET_SERIES_FILE.stat().st_mtime_ns
    except FileNotFoundError:
        return pd.DataFrame()
    return _load_market_series_cached(ticker, version)


@st.cache_data(show_spinner=False)
def _load_market_series_cached(ticker: str, file_version) -> pd.DataFrame:
    import json
    s = json.loads(MARKET_SERIES_FILE.read_text())["series"].get(ticker)
    if not s:
        return pd.DataFrame()
    return pd.DataFrame({"Close": s["close"]}, index=pd.DatetimeIndex(pd.to_datetime(s["dates"])))


def _parse_topix_snapshot(payload, now=None, symbol="TSE:TOPIX", market="Topix", zone="Asia/Tokyo"):
    """Validate a daily index snapshot. Never invent history or observation dates."""
    import math
    entries = payload.get("data", [])
    if len(entries) != 1 or entries[0].get("s") != symbol:
        raise ValueError(f"Missing {symbol} index")
    name, description, kind, close, rsi, timestamp, prev_close, prev_rsi = entries[0]["d"]
    if name != symbol.split(":")[1] or kind != "index":
        raise ValueError(f"Not the {symbol} index")
    close, rsi = float(close), float(rsi)
    if not math.isfinite(close) or close <= 0 or not math.isfinite(rsi) or not 0 <= rsi <= 100:
        raise ValueError("Invalid close or RSI")
    observation = pd.to_datetime(timestamp, unit="s", utc=True)
    if pd.isna(observation):
        raise ValueError("Missing observation date")
    day = observation.tz_convert(zone).normalize().tz_localize(None)
    frame = pd.DataFrame({"Close": [close], "RSI14": [rsi]}, index=pd.DatetimeIndex([day], name="Date"))
    frame = completed_daily_rows(frame, market, now=now)
    frame.attrs["history_source"] = f"TradingView {symbol} (daily index snapshot)"
    if frame.empty:
        # The session is still trading: its close/RSI are live, not final. Hand
        # back the previous (completed) session's values instead — TradingView's
        # close[1] / RSI[1]. The caller dates them (TradingView gives no date).
        try:
            pc, pr = float(prev_close), float(prev_rsi)
            if math.isfinite(pc) and pc > 0 and math.isfinite(pr) and 0 <= pr <= 100:
                frame.attrs["previous_session"] = {"close": pc, "rsi": pr,
                                                   "trading_day": day.strftime("%Y-%m-%d")}
        except (TypeError, ValueError):
            pass
    return frame


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_kospi_snapshot():
    try:
        response = requests.post("https://scanner.tradingview.com/korea/scan", json={
            "symbols": {"tickers": ["KRX:KOSPI200"], "query": {"types": []}},
            "columns": ["name", "description", "type", "close", "RSI", "time", "close[1]", "RSI[1]"],
        }, timeout=20)
        response.raise_for_status()
        return _parse_topix_snapshot(response.json(), symbol="KRX:KOSPI200", market="KOSPI 200", zone="Asia/Seoul")
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError):
        logging.getLogger(__name__).warning("KOSPI 200 index snapshot unavailable; no proxy fallback")
        return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_topix_snapshot():
    try:
        response = requests.post("https://scanner.tradingview.com/japan/scan", json={
            "symbols": {"tickers": ["TSE:TOPIX"], "query": {"types": []}},
            "columns": ["name", "description", "type", "close", "RSI", "time", "close[1]", "RSI[1]"],
        }, timeout=20)
        response.raise_for_status()
        return _parse_topix_snapshot(response.json())
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError):
        logging.getLogger(__name__).warning("TOPIX index snapshot unavailable; ETF fallback disabled")
        return pd.DataFrame()


def _fetch_index_via_akshare(ticker: str, days: int) -> pd.DataFrame:
    """Fallback for indexes where yfinance is broken. Returns yfinance-shape DataFrame."""
    try:
        import akshare as ak
        if ticker == "000300.SS":
            df = ak.stock_zh_index_daily(symbol="sh000300")
        elif ticker == "399006.SZ":
            df = ak.stock_zh_index_daily(symbol="sz399006")
        elif ticker == "000852.SS":
            df = ak.stock_zh_index_daily(symbol="sh000852")
        elif ticker == "^HSI":
            df = ak.stock_hk_index_daily_em(symbol="HSI")
            # akshare HSI uses 'latest' instead of 'close'
            if "latest" in df.columns and "close" not in df.columns:
                df = df.rename(columns={"latest": "close"})
        else:
            return pd.DataFrame()

        if df.empty or "date" not in df.columns:
            return pd.DataFrame()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        # Standardize columns to yfinance shape
        rename = {"open": "Open", "high": "High", "low": "Low",
                  "close": "Close", "volume": "Volume"}
        df = df.rename(columns=rename)
        # Ensure Volume column exists (HSI from akshare may not have it)
        if "Volume" not in df.columns:
            df["Volume"] = 0
        # Trim to requested days
        cutoff = pd.Timestamp.now() - pd.Timedelta(days=days)
        result = df[df.index >= cutoff][["Open", "High", "Low", "Close", "Volume"]].copy()
        result.attrs["history_source"] = (
            akshare_index_source(ticker) if ticker in AKSHARE_ONLY_INDICES
            else "Eastmoney via AkShare (HSI)"
        )
        return result
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=90000, show_spinner=False)  # 25 hours — refreshed by daily cron
def fetch_tv_rsi(name: str) -> float:
    """Fetch TradingView's own daily RSI(14) directly (no local calculation).

    Returns the same number shown on tradingview.com. Note: during a market's
    open hours this is the LIVE value of the still-forming daily candle; after
    close it is the end-of-day value. Returns None if the symbol is unknown, the
    library is missing, or TradingView errors/rate-limits — caller then falls
    back to the locally-computed RSI so the table never goes blank.
    """
    import time
    triple = config.TV_SYMBOLS.get(name)
    if triple is None:
        return None
    try:
        from tradingview_ta import TA_Handler, Interval
    except ImportError:
        return None
    symbol, exchange, screener = triple
    for attempt in range(3):
        try:
            analysis = TA_Handler(
                symbol=symbol, exchange=exchange, screener=screener,
                interval=Interval.INTERVAL_1_DAY,
            ).get_analysis()
            rsi = analysis.indicators.get("RSI")
            return float(rsi) if rsi is not None else None
        except Exception as e:
            # "Can't access TradingView's API" == HTTP 429 rate limit → back off & retry
            if "Can't access" in str(e) and attempt < 2:
                time.sleep(20 * (attempt + 1))
                continue
            return None
    return None


# Yahoo daily bars for every index constituent, downloaded once per process by
# the index snapshot (index_snapshot.py), which derives Tab 2's volume / turnover
# and Tab 3's breadth from them.
_YF_DAILY = {}   # ticker -> DataFrame[Close, Volume], naive local dates


def yf_daily(tickers: list, days: int = 500) -> dict:
    """{ticker: DataFrame[Close, Volume]} for `days` back, including the latest
    bars (Yahoo's `end` is exclusive, so end = now + 2 days). Downloads only
    what isn't cached yet, in chunks, and backs off when Yahoo rate-limits."""
    import time
    have = {t for t, df in _YF_DAILY.items() if df.attrs.get("days", 0) >= days}
    missing = [t for t in dict.fromkeys(tickers) if t not in have]
    start, end = datetime.now() - timedelta(days=days), datetime.now() + timedelta(days=2)
    for i in range(0, len(missing), 200):
        todo = missing[i:i + 200]
        for attempt in range(4):
            try:
                df = yf.download(todo, start=start, end=end, progress=False, auto_adjust=False,
                                 group_by="ticker", threads=True)
            except Exception:
                df = pd.DataFrame()
            for t in todo:
                try:
                    x = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                    x = x[["Close", "Volume"]].dropna(subset=["Close"])
                except Exception:
                    continue
                if x.empty:
                    continue
                idx = pd.DatetimeIndex(x.index)
                x.index = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
                x = x[~x.index.duplicated(keep="last")]
                x.attrs["days"] = days
                _YF_DAILY[t] = x
            todo = [t for t in todo if t not in _YF_DAILY]
            # A few names are always missing (delisted); many missing = rate limit
            if len(todo) <= 0.05 * 200 or attempt == 3:
                break
            time.sleep(30 * (attempt + 1))
        time.sleep(1)
    return {t: _YF_DAILY[t] for t in tickers if t in _YF_DAILY}


# ---------- Constituents ----------

_WIKI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}

# Which source each constituent fetcher actually used on its last call. A value
# containing "fallback" means the primary (official / ETF) source failed — the
# index snapshot flags that, since a fallback list can be stale.
CONSTITUENT_SOURCE = {}


def _wiki_tables(url: str) -> list:
    """Fetch Wikipedia page with proper headers, return parsed tables."""
    r = requests.get(url, headers=_WIKI_HEADERS, timeout=15)
    r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))


@st.cache_data(ttl=86400)
def constituents_sp500() -> list:
    """S&P 500 constituents from SPY's daily holdings file (State Street);
    Wikipedia as fallback. The ETF must track index changes, so its holdings
    follow every rebalance."""
    try:
        r = requests.get("https://www.ssga.com/us/en/intermediary/library-content/products/"
                         "fund-data/etfs/us/holdings-daily-us-en-spy.xlsx",
                         headers=_WIKI_HEADERS, timeout=30)
        df = pd.read_excel(io.BytesIO(r.content), skiprows=4)
        tickers = [t.replace(".", "-") for t in df["Ticker"].dropna().astype(str)
                   if t.replace(".", "").isalpha()]
        if len(tickers) >= 490:
            CONSTITUENT_SOURCE["S&P 500"] = "SSGA SPY holdings"
            return tickers
    except Exception:
        pass
    try:
        tables = _wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        CONSTITUENT_SOURCE["S&P 500"] = "Wikipedia (fallback)"
        return tables[0]["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
    except Exception:
        return []


@st.cache_data(ttl=86400)
def constituents_nasdaq100() -> list:
    """Nasdaq-100 constituents from Nasdaq's own list; Wikipedia as fallback."""
    try:
        r = requests.get("https://api.nasdaq.com/api/quote/list-type/nasdaq100",
                         headers={**_WIKI_HEADERS, "Accept": "application/json"}, timeout=25)
        rows = r.json()["data"]["data"]["rows"]
        if len(rows) >= 95:
            CONSTITUENT_SOURCE["Nasdaq 100"] = "Nasdaq official list"
            return [row["symbol"].replace(".", "-") for row in rows]
    except Exception:
        pass
    try:
        # The constituents table moved off the main Nasdaq-100 article in 2026.
        CONSTITUENT_SOURCE["Nasdaq 100"] = "Wikipedia (fallback)"
        tables = _wiki_tables("https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies")
        for t in tables:
            for col in t.columns:
                name = str(col).lower()
                if "ticker" in name or "symbol" in name:
                    return t[col].dropna().astype(str).tolist()
    except Exception:
        pass
    return []


@st.cache_data(ttl=86400)
def constituents_sox() -> list:
    """PHLX Semiconductor (SOX) constituents from Nasdaq's index weighting data
    for the latest weekday; a static list only as fallback (it goes stale — in
    Oct 2026 7 of its 30 names had already left the index)."""
    for back in range(0, 8):
        day = datetime.now() - timedelta(days=back)
        if day.weekday() >= 5:
            continue
        try:
            r = requests.post(
                "https://indexes.nasdaqomx.com/Index/WeightingData",
                data={"id": "SOX", "tradeDate": day.strftime("%Y-%m-%dT00:00:00.000"),
                      "timeOfDay": "SOD"},
                headers={**_WIKI_HEADERS, "X-Requested-With": "XMLHttpRequest",
                         "Referer": "https://indexes.nasdaqomx.com/Index/Weighting/SOX"},
                timeout=25)
            rows = r.json().get("aaData") or []
            tickers = [row["Symbol"].replace(".", "-") for row in rows if row.get("Symbol")]
            if len(tickers) >= 25:
                CONSTITUENT_SOURCE["SOX"] = f"Nasdaq index weighting ({day:%Y-%m-%d})"
                return tickers
        except Exception:
            continue
    CONSTITUENT_SOURCE["SOX"] = "static list (fallback)"
    return [
        "NVDA", "AVGO", "TSM", "AMD", "QCOM", "TXN", "MU", "INTC", "ADI", "KLAC",
        "LRCX", "AMAT", "MRVL", "NXPI", "MCHP", "ON", "MPWR", "SWKS", "STM",
        "ASML", "TER", "ENTG", "COHR", "RMBS", "QRVO", "UMC", "ASX", "ARM",
        "CRDO", "WOLF",
    ]


@st.cache_data(ttl=86400)
def constituents_hsi() -> list:
    try:
        tables = _wiki_tables("https://en.wikipedia.org/wiki/Hang_Seng_Index")
        for t in tables:
            for col in t.columns:
                name = str(col).lower()
                if "ticker" in name or "code" in name or "symbol" in name:
                    codes = t[col].astype(str).str.extract(r"(\d+)")[0].dropna()
                    if len(codes) >= 30:
                        return [c.zfill(4) + ".HK" for c in codes]
    except Exception:
        pass
    return []


@st.cache_data(ttl=86400)
def constituents_kospi200() -> list:
    """KOSPI 200 constituents from Wikipedia. Returns yfinance-formatted tickers."""
    try:
        tables = _wiki_tables("https://en.wikipedia.org/wiki/KOSPI_200")
        for t in tables:
            if "Symbol" in t.columns and len(t) > 100:
                codes = t["Symbol"].astype(str).str.zfill(6)
                return [c + ".KS" for c in codes]
    except Exception:
        pass
    return []


@st.cache_data(ttl=86400)
def constituents_taiwan() -> list:
    """All TWSE listed stocks via openapi (~1,100 stocks).

    Falls back to top 50 hardcoded list if TWSE API fails.
    """
    try:
        # TWSE openapi (sometimes has SSL cert issues)
        r = requests.get(
            "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_AVG_ALL",
            headers=_WIKI_HEADERS, timeout=20, verify=False,
        )
        if r.status_code == 200:
            data = r.json()
            # 4-digit pure stock codes (filter ETFs, REITs, warrants etc.)
            stocks = [d for d in data if d.get("Code", "").isdigit() and len(d["Code"]) == 4]
            tickers = [d["Code"] + ".TW" for d in stocks]
            if len(tickers) >= 500:
                return tickers
    except Exception:
        pass

    # Fallback: hardcoded top 50
    return [
        "2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "2412.TW",
        "6505.TW", "2881.TW", "2882.TW", "1303.TW", "1301.TW", "2891.TW",
        "3711.TW", "2886.TW", "2884.TW", "2885.TW", "5871.TW", "1216.TW",
        "2002.TW", "3008.TW", "3034.TW", "2207.TW", "2357.TW", "2880.TW",
        "1326.TW", "2890.TW", "5880.TW", "2912.TW", "2379.TW", "3045.TW",
        "2892.TW", "4904.TW", "2887.TW", "2603.TW", "3037.TW", "2615.TW",
        "2609.TW", "2474.TW", "1101.TW", "1102.TW", "2105.TW", "9910.TW",
        "2618.TW", "2823.TW", "3231.TW", "1590.TW", "9904.TW",
    ]


@st.cache_data(ttl=86400)
def constituents_csi300() -> list:
    """CSI 300 constituents from CSIndex (via akshare). Returns yfinance tickers."""
    return _csindex_constituents("000300")


@st.cache_data(ttl=86400)
def constituents_chinext() -> list:
    """ChiNext Index (创业板指) constituents from CNI, the index publisher;
    Sina via akshare as fallback."""
    for _ in range(3):  # CNI is intermittently slow (30 s+) — retry before falling back
        try:
            r = requests.get("https://www.cnindex.com.cn/sample-detail/detail",
                             params={"indexcode": "399006", "dateStr": "", "pageNum": 1, "rows": 200},
                             headers=_WIKI_HEADERS, timeout=45)
            rows = r.json()["data"]["rows"]
            codes = [str(x["seccode"]).zfill(6) for x in rows if x.get("seccode")]
            if len(codes) >= 95:
                CONSTITUENT_SOURCE["ChiNext"] = f"CNI official ({rows[0].get('dateStr')})"
                return [c + ".SZ" for c in codes]
        except Exception:
            continue
    try:
        CONSTITUENT_SOURCE["ChiNext"] = "Sina via akshare (fallback)"
        import akshare as ak
        df = ak.index_stock_cons_sina(symbol="399006")
        codes = df["code"].astype(str).str.zfill(6) if "code" in df.columns else df.iloc[:, 0].astype(str).str.zfill(6)
        return [c + ".SZ" for c in codes]
    except Exception:
        return []


def _ishares_holdings(product_id: str, fund_name: str) -> list:
    """Generic iShares ETF holdings CSV scraper."""
    url = (f"https://www.ishares.com/us/products/{product_id}/etf/"
           f"1467271812596.ajax?fileType=csv&fileName={fund_name}_holdings&dataType=fund")
    try:
        r = requests.get(url, headers=_WIKI_HEADERS, timeout=20)
        lines = r.text.split("\n")
        header_idx = next((i for i, l in enumerate(lines) if l.startswith("Ticker,")), None)
        if header_idx is None:
            return []
        df = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])))
        if "Asset Class" in df.columns:
            df = df[df["Asset Class"] == "Equity"]
        return df["Ticker"].dropna().astype(str).str.strip().tolist()
    except Exception:
        return []


def _vanguard_holdings(fund: str, expected: int = 1900) -> list:
    """Vanguard ETF equity holdings via the public profile API (paginated,
    500/page). Used for Russell 2000 (VTWO) since iShares blocked CSV download."""
    base = ("https://investor.vanguard.com/investment-products/etfs/profile/"
            f"api/{fund}/portfolio-holding/stock")
    headers = {**_WIKI_HEADERS, "Referer": "https://investor.vanguard.com/"}

    def _find_list(o):
        if isinstance(o, list) and o and isinstance(o[0], dict) and "ticker" in o[0]:
            return o
        if isinstance(o, dict):
            for v in o.values():
                r = _find_list(v)
                if r:
                    return r
        return None

    tickers = set()
    for start in range(1, expected + 600, 500):
        try:
            r = requests.get(f"{base}?start={start}&count=500", headers=headers, timeout=25)
            lst = _find_list(r.json()) or []
        except Exception:
            break
        if not lst:
            break
        before = len(tickers)
        for h in lst:
            t = (h.get("ticker") or "").strip()
            if t and t.replace(".", "").replace("-", "").isalnum() and 1 <= len(t) <= 6:
                tickers.add(t)
        if len(tickers) == before:  # page returned only dupes → wrapped around, stop
            break
        if len(tickers) >= expected:
            break
    return sorted(tickers)


@st.cache_data(ttl=86400)
def constituents_russell2000() -> list:
    """Russell 2000 constituents.

    Primary: Vanguard VTWO holdings API (~1,945 names).
    Fallback: iShares IWM/IWO/IWN union (often blocked now, kept as backup)."""
    vt = _vanguard_holdings("vtwo", expected=1900)
    if len(vt) >= 1000:
        return [t.replace(".", "-") for t in vt]  # BRK.B → BRK-B for yfinance
    all_tickers = set()
    for pid, name in [("239710", "IWM"), ("239709", "IWO"), ("239712", "IWN")]:
        for t in _ishares_holdings(pid, name):
            if t.isalpha() and 1 <= len(t) <= 5:
                all_tickers.add(t)
    return sorted(all_tickers)


@st.cache_data(ttl=86400)
def constituents_csi1000() -> list:
    """CSI 1000 constituents from CSIndex (via akshare). Returns yfinance tickers."""
    return _csindex_constituents("000852")


def _csindex_constituents(code: str) -> list:
    """CSIndex's official list for `code`. Retried: the request fails
    intermittently (a one-off empty list blanked CSI 1000 for a whole run)."""
    import time
    for attempt in range(3):
        try:
            import akshare as ak
            df = ak.index_stock_cons_csindex(symbol=code)
            col = "成分券代码" if "成分券代码" in df.columns else df.columns[0]
            codes = df[col].astype(str).str.zfill(6)
            # SH codes start with 6; SZ with 0 / 3
            tickers = [c + (".SS" if c.startswith("6") else ".SZ") for c in codes]
            if tickers:
                return tickers
        except Exception:
            pass
        time.sleep(5 * (attempt + 1))
    return []


@st.cache_data(ttl=86400)
def constituents_nikkei225() -> list:
    """Full Nikkei 225 constituents.

    Primary: Nikkei official weight CSV (all 225, free + public).
    Fallback 1: the component HTML page. Fallback 2: hardcoded top-50 proxy.
    """
    # Primary: official weight CSV (the HTML page is 403-blocked, but this isn't)
    try:
        r = requests.get(
            "https://indexes.nikkei.co.jp/nkave/archives/file/"
            "nikkei_stock_average_weight_en.csv",
            headers=_WIKI_HEADERS, timeout=20,
        )
        if r.status_code == 200 and len(r.text) > 2000:
            df = pd.read_csv(io.StringIO(r.text))
            code_col = next((c for c in df.columns if "code" in str(c).lower()), None)
            if code_col is not None:
                # Codes can be alphanumeric (e.g. 285A, 543A) — \d{4} dropped them
                codes = df[code_col].astype(str).str.extract(r"([0-9]{3}[0-9A-Z])")[0].dropna().unique()
                tickers = [c + ".T" for c in codes]
                if len(tickers) >= 200:
                    CONSTITUENT_SOURCE["Nikkei 225"] = "Nikkei official weight file"
                    return tickers
    except Exception:
        pass

    # Fallback 1: component HTML page
    try:
        r = requests.get(
            "https://indexes.nikkei.co.jp/en/nkave/index/component?idx=nk225",
            headers=_WIKI_HEADERS, timeout=15
        )
        if r.status_code == 200:
            import re as _re
            codes = _re.findall(r'>\s*([0-9]{3}[0-9A-Z])\s*<', r.text)
            seen = set()
            uniq = []
            for c in codes:
                if c not in seen:
                    seen.add(c)
                    uniq.append(c)
            if len(uniq) >= 200:
                CONSTITUENT_SOURCE["Nikkei 225"] = "Nikkei component page"
                return [c + ".T" for c in uniq]
    except Exception:
        pass

    # Fallback 2: hardcoded top 50 (proxy)
    CONSTITUENT_SOURCE["Nikkei 225"] = "static top-50 list (fallback)"
    return [
        "7203.T", "6758.T", "6861.T", "9984.T", "8035.T", "9432.T",
        "8306.T", "6098.T", "9433.T", "7974.T", "8316.T", "4063.T",
        "8058.T", "8001.T", "9983.T", "6501.T", "6902.T", "6594.T",
        "7741.T", "4502.T", "6981.T", "7267.T", "8002.T", "9020.T",
        "8031.T", "4503.T", "6273.T", "6367.T", "9101.T", "6326.T",
        "7011.T", "9434.T", "6752.T", "6201.T", "8411.T", "7751.T",
        "7270.T", "6954.T", "7269.T", "5108.T", "8766.T", "4661.T",
        "9022.T", "5401.T", "8053.T", "6503.T", "6857.T", "7733.T",
        "4452.T",
    ]


@st.cache_data(ttl=86400)
def constituents_topix() -> list:
    """Topix constituents = TSE Prime market full list (~1,574 stocks).

    Source: JPX (Japan Exchange Group) official Excel file, free + public.
    Topix index IS the TSE Prime market post-2022 reorganization.
    Falls back to EWJ ETF holdings (~180 stocks), then Nikkei 225, if JPX fails.
    """
    try:
        # JPX switched this file from .xls to .xlsx in 2026 (the .xls URL now 404s).
        url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
        r = requests.get(url, headers=_WIKI_HEADERS, timeout=30)
        if r.status_code == 200 and len(r.content) > 50000:
            df = pd.read_excel(io.BytesIO(r.content), sheet_name=0)
            # Filter to "プライム（内国株式）" = TSE Prime domestic stocks
            market_col = next((c for c in df.columns if "市場" in str(c) or "Market" in str(c)), None)
            code_col = next((c for c in df.columns if "コード" in str(c) or "Code" in str(c)), None)
            if market_col and code_col:
                prime = df[df[market_col].astype(str).str.contains("プライム.*内国|Prime.*Domestic", regex=True, na=False)]
                codes = prime[code_col].dropna().astype(str).str.zfill(4)
                # Codes are 4 chars; newer listings are alphanumeric (e.g. "285A").
                tickers = [c + ".T" for c in codes if c.isalnum() and len(c) == 4]
                if len(tickers) >= 1000:
                    CONSTITUENT_SOURCE["Topix"] = "JPX listed companies (Prime)"
                    return tickers
    except Exception:
        pass

    # Fallback 1: EWJ (~180 stocks)
    ewj = _ishares_holdings("239665", "EWJ")
    valid = [t + ".T" for t in ewj if t.isdigit() and len(t) == 4]
    if len(valid) >= 100:
        CONSTITUENT_SOURCE["Topix"] = "iShares EWJ holdings (fallback)"
        return valid
    # Fallback 2: Nikkei 225
    CONSTITUENT_SOURCE["Topix"] = "Nikkei 225 list (fallback)"
    return constituents_nikkei225()


CONSTITUENT_FETCHERS = {
    "S&P 500":     constituents_sp500,
    "Nasdaq 100":  constituents_nasdaq100,
    "SOX":         constituents_sox,
    "Russell 2000":constituents_russell2000,  # Full ~2000 from iShares IWM CSV
    "Hang Seng":   constituents_hsi,
    "CSI 300":     constituents_csi300,
    "CSI 1000":    constituents_csi1000,      # Full 1000 from akshare
    "ChiNext":     constituents_chinext,
    "Nikkei 225":  constituents_nikkei225,    # Top 50 by weight (proxy)
    "Topix":       constituents_topix,        # Top 50 (proxy, overlaps Nikkei)
    "Taiwan":      constituents_taiwan,        # Top 50 by market cap
    "KOSPI 200":   constituents_kospi200,      # Full 200 from Wikipedia
}


# ---------- AAII ----------

def _parse_aaii_xls(content: bytes) -> pd.DataFrame:
    df = pd.read_excel(io.BytesIO(content), sheet_name=0, skiprows=3)
    df = df.dropna(how="all").dropna(axis=1, how="all")
    df.columns = [str(c).strip() for c in df.columns]
    date_col = next((c for c in df.columns if "date" in c.lower()), df.columns[0])
    bull_col = next((c for c in df.columns if "bull" in c.lower()), None)
    neut_col = next((c for c in df.columns if "neutral" in c.lower()), None)
    bear_col = next((c for c in df.columns if "bear" in c.lower()), None)
    if not (bull_col and bear_col):
        return pd.DataFrame()
    cols = [date_col, bull_col]
    names = ["Date", "Bullish"]
    if neut_col:
        cols.append(neut_col); names.append("Neutral")
    cols.append(bear_col); names.append("Bearish")
    out = df[cols].copy()
    out.columns = names
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    out = out.dropna(subset=["Date"])
    for c in names[1:]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    if "Neutral" not in out.columns:
        out["Neutral"] = 1 - out["Bullish"] - out["Bearish"]
    out["Net (Bull-Bear)"] = out["Bullish"] - out["Bearish"]
    return out.sort_values("Date").reset_index(drop=True)


AAII_LOCAL_FILE = Path(__file__).parent / "data" / "aaii_sentiment.xls"


def fetch_aaii_sentiment() -> pd.DataFrame:
    """Invalidate parsed data whenever the uploaded file changes."""
    try:
        stat = AAII_LOCAL_FILE.stat()
        version = (stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        version = None
    return _fetch_aaii_sentiment_cached(version)


@st.cache_data(ttl=3600)
def _fetch_aaii_sentiment_cached(file_version) -> pd.DataFrame:
    """Returns DataFrame with columns Date, Bullish, Neutral, Bearish, Net.

    Order:
    1. Local file at data/aaii_sentiment.xls (pushed by GitHub Actions or
       uploaded manually via dashboard) — most reliable, survives restarts.
    2. curl (works on Mac, blocked by Imperva on Hetzner).
    3. requests (last resort).
    """
    # Method 1: local file (preferred — persists across refreshes/restarts)
    if AAII_LOCAL_FILE.exists() and AAII_LOCAL_FILE.stat().st_size > 10000:
        try:
            return _parse_aaii_xls(AAII_LOCAL_FILE.read_bytes())
        except Exception:
            pass

    url = "https://www.aaii.com/files/surveys/sentiment.xls"

    # Method 2: curl
    try:
        result = subprocess.run([
            "curl", "-s", "-L",
            "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/17.0 Safari/605.1.15",
            "-H", "Accept: */*",
            "-H", "Accept-Language: en-US,en;q=0.9",
            "-H", "Referer: https://www.aaii.com/sentimentsurvey",
            url,
        ], capture_output=True, timeout=30)
        if result.returncode == 0 and len(result.stdout) > 10000:
            # Cache to disk so future refreshes don't re-fetch
            AAII_LOCAL_FILE.parent.mkdir(exist_ok=True)
            AAII_LOCAL_FILE.write_bytes(result.stdout)
            return _parse_aaii_xls(result.stdout)
    except Exception:
        pass

    # Method 3: requests
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.aaii.com/sentimentsurvey",
        })
        if r.status_code == 200 and len(r.content) > 10000:
            AAII_LOCAL_FILE.parent.mkdir(exist_ok=True)
            AAII_LOCAL_FILE.write_bytes(r.content)
            return _parse_aaii_xls(r.content)
    except Exception:
        pass

    return pd.DataFrame()


def parse_aaii_upload(file) -> pd.DataFrame:
    """Parse a manually uploaded sentiment.xls file AND save it to disk
    so it persists across browser refreshes and Streamlit restarts."""
    try:
        content = file.read()
        AAII_LOCAL_FILE.parent.mkdir(exist_ok=True)
        AAII_LOCAL_FILE.write_bytes(content)
        # Clear the cache so next call picks up the new file
        if hasattr(fetch_aaii_sentiment, "clear"):
            fetch_aaii_sentiment.clear()
        return _parse_aaii_xls(content)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=90000)  # 25 hours — daily cron
def fetch_barchart_pc_chart() -> str:
    """Screenshot Barchart's $SPX Put/Call chart via headless browser.

    Bypasses CSP/CORS by rendering server-side with Playwright.
    Returns path to local PNG, or '' if failed.
    """
    from pathlib import Path
    out_path = Path(__file__).parent / "data" / "barchart_pc.png"
    out_path.parent.mkdir(exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport={"width": 1600, "height": 1200},
                user_agent="Mozilla/5.0 Chrome/120",
            )
            page = context.new_page()
            page.goto(
                "https://www.barchart.com/stocks/quotes/$SPX/put-call-ratios",
                wait_until="load", timeout=45000,
            )
            page.wait_for_timeout(10000)
            # The big SVG (index 1) is the price + P/C chart
            chart = page.locator("svg").nth(1)
            chart.screenshot(path=str(out_path))
            browser.close()
        return str(out_path)
    except Exception as e:
        # Fallback: return cached file if exists, else empty
        if out_path.exists():
            return str(out_path)
        return ""


@st.cache_data(ttl=3600)
def fetch_stockcharts_image(symbol: str, period: str) -> str:
    """Download StockCharts chart PNG server-side (bypass CORS) and save locally.

    period like 'yr=0&mn=6' for 6 months.
    Returns local path to saved image, or '' if failed.
    """
    import os
    from pathlib import Path
    cache_dir = Path(__file__).parent / "data" / "stockcharts"
    cache_dir.mkdir(parents=True, exist_ok=True)
    safe_sym = symbol.replace("$", "")
    safe_period = period.replace("=", "_").replace("&", "_")
    fname = cache_dir / f"{safe_sym}_{safe_period}.png"
    url = f"https://stockcharts.com/c-sc/sc?s={symbol}&p=D&{period}&dy=0&i=p"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200 and len(r.content) > 5000 and r.content.startswith(b"\x89PNG"):
            fname.write_bytes(r.content)
            return str(fname)
    except Exception:
        pass
    return ""


# ---------- Put/Call ----------

BARCHART_LOCAL_JSON = Path(__file__).parent / "data" / "barchart_pc.json"


def fetch_putcall_ratio() -> dict:
    """Read the validated SPX snapshot each rerun; never substitute SPY."""
    import json
    import math
    from datetime import timezone
    try:
        cached = json.loads(BARCHART_LOCAL_JSON.read_text())
        if not str(cached.get("source", "")).startswith("Barchart $SPX"):
            return {}
        for key in ("vol_ratio", "oi_ratio", "total_put_vol", "total_call_vol",
                    "total_put_oi", "total_call_oi"):
            value = cached[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return {}
            if not math.isfinite(value) or value < 0:
                return {}
        for numerator, denominator, ratio in (
            ("total_put_vol", "total_call_vol", "vol_ratio"),
            ("total_put_oi", "total_call_oi", "oi_ratio"),
        ):
            if cached[denominator] <= 0:
                return {}
            if abs(cached[numerator] / cached[denominator] - cached[ratio]) > 0.011:
                return {}
        fetched = datetime.fromisoformat(cached["asof"].replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            return {}
        age = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600
        if age < -0.1:
            return {}
        cached["stale"] = age > 96
        cached["age_hours"] = age
        return cached
    except (OSError, ValueError, KeyError, TypeError):
        return {}
