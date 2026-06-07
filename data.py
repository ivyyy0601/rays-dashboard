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

@st.cache_data(ttl=90000, show_spinner=False)  # 25 hours — daily cron
def fetch_yf_history(ticker: str, days: int = 500) -> pd.DataFrame:
    end = datetime.now()
    start = end - timedelta(days=days)
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    # Drop rows where Close is NaN (e.g., today's row when market hasn't closed yet)
    if "Close" in df.columns:
        df = df.dropna(subset=["Close"])
    # Fallback: yfinance is unreliable for some Chinese index tickers — use akshare
    if len(df) < 50 and ticker in ("000300.SS", "399006.SZ", "000852.SS", "^HSI"):
        df_alt = _fetch_index_via_akshare(ticker, days)
        if len(df_alt) > len(df):
            return df_alt
    return df


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
        return df[df.index >= cutoff][["Open", "High", "Low", "Close", "Volume"]]
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


@st.cache_data(ttl=3600, show_spinner="Downloading constituents...")
def fetch_batch_close(tickers: list, days: int = 300) -> pd.DataFrame:
    """Returns a DataFrame of close prices: rows=dates, cols=tickers.

    Routes A-share tickers (.SS / .SZ) to akshare per-stock; everything else
    goes through yfinance batch download.
    """
    if not tickers:
        return pd.DataFrame()
    a_share = [t for t in tickers if t.endswith(".SS") or t.endswith(".SZ")]
    other = [t for t in tickers if not (t.endswith(".SS") or t.endswith(".SZ"))]

    frames = []  # list of single-column DataFrames or Series, then concat once

    # Path 1: yfinance batch for US / HK / TW / KR
    if other:
        ydf = _yf_batch_close(other, days)
        if not ydf.empty:
            frames.append(ydf)

    # Path 2: A-share — akshare/Baostock first (best when run inside China);
    # if that yields little (e.g. those hosts are blocked from a US server),
    # fall back to yfinance, which serves A-shares under the same .SS/.SZ codes.
    if a_share:
        ashare_df = _fetch_ashare_close(a_share, days)
        got = set(ashare_df.columns) if not ashare_df.empty else set()
        if len(got) < 0.5 * len(a_share):
            missing = [s for s in a_share if s not in got]
            yf_ashare = _yf_batch_close(missing, days)
            ashare_df = pd.concat([ashare_df, yf_ashare], axis=1) if not ashare_df.empty else yf_ashare
        if not ashare_df.empty:
            frames.append(ashare_df)

    if not frames:
        return pd.DataFrame()
    closes = pd.concat(frames, axis=1)
    return closes.dropna(how="all")


def _yf_batch_close(tickers: list, days: int) -> pd.DataFrame:
    """Batch close prices from yfinance (rows=dates, cols=tickers)."""
    if not tickers:
        return pd.DataFrame()
    end = datetime.now()
    start = end - timedelta(days=days)
    df = yf.download(tickers, start=start, end=end, progress=False,
                     auto_adjust=False, group_by="ticker", threads=True)
    series_dict = {}
    if isinstance(df.columns, pd.MultiIndex):
        for t in tickers:
            try:
                s = df[t]["Close"]
                if not s.dropna().empty:
                    series_dict[t] = s
            except Exception:
                continue
    elif "Close" in df.columns and len(tickers) == 1:
        series_dict[tickers[0]] = df["Close"]
    return pd.DataFrame(series_dict) if series_dict else pd.DataFrame()


def _fetch_ashare_close_baostock(symbols: list, days: int) -> pd.DataFrame:
    """Fallback A-share fetcher via Baostock when akshare upstream is down."""
    try:
        import baostock as bs
    except ImportError:
        return pd.DataFrame()
    try:
        bs.login()
        end_str = datetime.now().strftime("%Y-%m-%d")
        start_str = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        series_dict = {}
        for sym in symbols:
            code_no_suffix = sym.replace(".SS", "").replace(".SZ", "")
            # Baostock format: "sh.600000" or "sz.000001"
            bs_code = ("sh." if sym.endswith(".SS") else "sz.") + code_no_suffix
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code, "date,close",
                    start_date=start_str, end_date=end_str,
                    frequency="d", adjustflag="2",  # qfq
                )
                if rs.error_code == "0":
                    df = rs.get_data()
                    if not df.empty:
                        df["date"] = pd.to_datetime(df["date"])
                        df["close"] = pd.to_numeric(df["close"], errors="coerce")
                        series_dict[sym] = df.set_index("date")["close"]
            except Exception:
                continue
        bs.logout()
        return pd.DataFrame(series_dict) if series_dict else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _akshare_reachable() -> bool:
    """One quick probe — akshare's upstream (eastmoney) is blocked from non-China
    IPs. If this one call fails, skip the per-stock retry storm and let the caller
    fall back to yfinance/Baostock instead."""
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(symbol="000001", period="daily",
                                start_date="20260101", end_date="20260110", adjust="")
        return not df.empty
    except Exception:
        return False


def _fetch_ashare_close(symbols: list, days: int) -> pd.DataFrame:
    """Fetch A-share daily close. Try akshare first; fall back to Baostock if too many failures."""
    try:
        import akshare as ak
    except ImportError:
        return _fetch_ashare_close_baostock(symbols, days)
    if not _akshare_reachable():
        # akshare upstream unreachable (e.g. US server) → don't hammer it per-stock;
        # return empty so fetch_batch_close uses its yfinance fallback.
        return pd.DataFrame()
    end_str = datetime.now().strftime("%Y%m%d")
    start_str = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    import time
    series_dict = {}
    progress = st.progress(0.0, text=f"Fetching {len(symbols)} A-share stocks via akshare...")
    failures_in_a_row = 0
    for i, sym in enumerate(symbols):
        code = sym.replace(".SS", "").replace(".SZ", "")
        success = False
        for attempt in range(3):
            try:
                df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                        start_date=start_str, end_date=end_str, adjust="qfq")
                if not df.empty and "日期" in df.columns and "收盘" in df.columns:
                    df["日期"] = pd.to_datetime(df["日期"])
                    series_dict[sym] = df.set_index("日期")["收盘"]
                    success = True
                    failures_in_a_row = 0
                    break
            except Exception:
                if attempt < 2:
                    time.sleep(1.0 * (attempt + 1))
        if not success:
            failures_in_a_row += 1
            # If akshare is fully down (15 consecutive fails) → switch to Baostock for remaining
            if failures_in_a_row >= 15:
                remaining = symbols[i:]
                progress.progress(min(1.0, (i + 1) / len(symbols)),
                                 text=f"akshare down — switching to Baostock for remaining {len(remaining)} stocks...")
                bs_data = _fetch_ashare_close_baostock(remaining, days)
                if not bs_data.empty:
                    for col in bs_data.columns:
                        series_dict[col] = bs_data[col]
                progress.empty()
                return pd.DataFrame(series_dict) if series_dict else pd.DataFrame()
        if (i + 1) % 10 == 0 or i == len(symbols) - 1:
            progress.progress((i + 1) / len(symbols), text=f"A-share: {i+1}/{len(symbols)}")
    progress.empty()

    # Final fallback: if akshare collected < 30% of symbols, top up with Baostock
    success_rate = len(series_dict) / len(symbols) if symbols else 0
    if success_rate < 0.3:
        missing = [s for s in symbols if s not in series_dict]
        bs_data = _fetch_ashare_close_baostock(missing, days)
        if not bs_data.empty:
            for col in bs_data.columns:
                series_dict[col] = bs_data[col]

    return pd.DataFrame(series_dict) if series_dict else pd.DataFrame()


# ---------- Real turnover (constituent aggregation) ----------

def _fetch_ashare_turnover_baostock(symbols: list, days: int) -> pd.DataFrame:
    """Fallback A-share turnover (成交额, CNY) via Baostock — used when akshare
    (eastmoney) is unreachable, e.g. from a non-China server IP."""
    try:
        import baostock as bs
    except ImportError:
        return pd.DataFrame()
    try:
        bs.login()
        end_str = datetime.now().strftime("%Y-%m-%d")
        start_str = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        series_dict = {}
        for sym in symbols:
            code = sym.replace(".SS", "").replace(".SZ", "")
            bs_code = ("sh." if sym.endswith(".SS") else "sz.") + code
            try:
                rs = bs.query_history_k_data_plus(
                    bs_code, "date,amount",
                    start_date=start_str, end_date=end_str,
                    frequency="d", adjustflag="3",
                )
                if rs.error_code == "0":
                    df = rs.get_data()
                    if not df.empty:
                        df["date"] = pd.to_datetime(df["date"])
                        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
                        series_dict[sym] = df.set_index("date")["amount"]
            except Exception:
                continue
        bs.logout()
        return pd.DataFrame(series_dict) if series_dict else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _fetch_ashare_turnover(symbols: list, days: int) -> pd.DataFrame:
    """A-share daily turnover (成交额, CNY). Try akshare first; fall back to Baostock."""
    try:
        import akshare as ak
    except ImportError:
        return _fetch_ashare_turnover_baostock(symbols, days)
    end_str = datetime.now().strftime("%Y%m%d")
    start_str = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    import time
    series_dict = {}
    failures_in_a_row = 0
    for i, sym in enumerate(symbols):
        code = sym.replace(".SS", "").replace(".SZ", "")
        success = False
        for attempt in range(2):
            try:
                df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                        start_date=start_str, end_date=end_str, adjust="")
                if not df.empty and "日期" in df.columns and "成交额" in df.columns:
                    df["日期"] = pd.to_datetime(df["日期"])
                    series_dict[sym] = pd.to_numeric(df.set_index("日期")["成交额"], errors="coerce")
                    success = True
                    failures_in_a_row = 0
                    break
            except Exception:
                if attempt < 1:
                    time.sleep(0.8)
        if not success:
            failures_in_a_row += 1
            if failures_in_a_row >= 15:  # akshare down → Baostock for the rest
                bs_data = _fetch_ashare_turnover_baostock(symbols[i:], days)
                for col in bs_data.columns:
                    series_dict[col] = bs_data[col]
                return pd.DataFrame(series_dict) if series_dict else pd.DataFrame()
    # Top up with Baostock if akshare got too little
    if symbols and len(series_dict) / len(symbols) < 0.3:
        missing = [s for s in symbols if s not in series_dict]
        bs_data = _fetch_ashare_turnover_baostock(missing, days)
        for col in bs_data.columns:
            series_dict[col] = bs_data[col]
    return pd.DataFrame(series_dict) if series_dict else pd.DataFrame()


@st.cache_data(ttl=90000, show_spinner=False)  # 25 hours — refreshed by daily cron
def fetch_batch_turnover(tickers: list, days: int = 45) -> pd.DataFrame:
    """Daily turnover per ticker (rows=dates, cols=tickers), native currency.

    Non-A-share = Close × Volume via one batched yfinance call;
    A-share = 成交额 (real turnover field) via akshare, Baostock fallback.
    Summed across constituents, this gives an index's real market turnover.
    """
    if not tickers:
        return pd.DataFrame()
    a_share = [t for t in tickers if t.endswith(".SS") or t.endswith(".SZ")]
    other = [t for t in tickers if not (t.endswith(".SS") or t.endswith(".SZ"))]
    frames = []

    if other:
        end = datetime.now()
        start = end - timedelta(days=days)
        df = yf.download(other, start=start, end=end, progress=False,
                         auto_adjust=False, group_by="ticker", threads=True)
        series_dict = {}
        if isinstance(df.columns, pd.MultiIndex):
            for t in other:
                try:
                    sub = df[t]
                    to = (sub["Close"] * sub["Volume"]).dropna()
                    if not to.empty:
                        series_dict[t] = to
                except Exception:
                    continue
        elif "Close" in df.columns and "Volume" in df.columns and len(other) == 1:
            to = (df["Close"] * df["Volume"]).dropna()
            if not to.empty:
                series_dict[other[0]] = to
        if series_dict:
            frames.append(pd.DataFrame(series_dict))

    if a_share:
        adf = _fetch_ashare_turnover(a_share, days)
        if not adf.empty:
            frames.append(adf)

    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, axis=1).dropna(how="all")


def index_turnover_summary(name: str, days: int = 45, window: int = 20) -> dict:
    """Real index turnover = Σ(constituent daily turnover), plus its Δ vs prior
    `window`-day average. Returns {} if the constituent list is unavailable or
    no turnover data could be fetched (caller then falls back to the ETF proxy).
    """
    import indicators
    fetcher = CONSTITUENT_FETCHERS.get(name)
    if fetcher is None:
        return {}
    tickers = fetcher()
    if not tickers:
        return {}
    tdf = fetch_batch_turnover(tickers, days=days)
    if tdf.empty:
        return {}
    # Total index turnover per day = sum across constituents that traded that day.
    daily = tdf.sum(axis=1, min_count=1).dropna()
    summ = indicators.turnover_series_summary(daily, window)
    if not (summ["latest"] == summ["latest"]):  # NaN guard
        return {}
    return {
        "latest": summ["latest"],
        "avg20": summ["avg20"],
        "deviation_pct": summ["deviation_pct"],
        "n_constituents": int(tdf.shape[1]),
        "as_of": daily.index[-1].strftime("%Y-%m-%d"),
    }


# ---------- Real trading volume (no ETF) ----------

@st.cache_data(ttl=90000, show_spinner=False)  # 25 hours — daily cron
def fetch_constituent_volume_series(name: str, days: int = 90) -> pd.Series:
    """Index trading volume = Σ of its constituents' share volume — used when the
    index ticker has no usable own volume and we want to stay ETF-free:
      - SOX   (^SOX reports no volume)
      - Topix (its yfinance ticker 1308.T is an ETF, not the real index)
    Returns an empty Series if the constituent fetch fails."""
    fetcher = CONSTITUENT_FETCHERS.get(name)
    tickers = fetcher() if fetcher else []
    if not tickers:
        return pd.Series(dtype=float)
    end = datetime.now()
    start = end - timedelta(days=days)
    df = yf.download(tickers, start=start, end=end, progress=False,
                     auto_adjust=False, group_by="ticker", threads=True)
    vols = {}
    if isinstance(df.columns, pd.MultiIndex):
        for t in tickers:
            try:
                v = df[t]["Volume"].dropna()
                if not v.empty:
                    vols[t] = v
            except Exception:
                continue
    elif "Volume" in df.columns and len(tickers) == 1:
        vols[tickers[0]] = df["Volume"].dropna()
    if not vols:
        return pd.Series(dtype=float)
    return pd.DataFrame(vols).sum(axis=1, min_count=1).dropna()


def fetch_index_volume_summary(name: str, window: int = 20) -> dict:
    """Real index trading VOLUME (no ETF) + its Δ vs the prior `window` average.

    Source per index:
      - Russell 2000 → RTY=F futures contract volume (^RUT volume is a yfinance bug)
      - SOX          → Σ of 30 constituents' share volume (^SOX reports no volume)
      - all others   → the index ticker's own aggregate share volume (direct)

    Drops incomplete trailing bars and excludes today from the baseline (via
    indicators.turnover_series_summary). Returns {} if no usable volume series.
    """
    import indicators
    days = config.HISTORY_DAYS
    if name == "Russell 2000":
        df = fetch_yf_history("RTY=F", days=days)
        ser = df["Volume"] if (not df.empty and "Volume" in df.columns) else None
        unit, source = "contracts", "RTY=F futures"
    elif name in ("SOX", "Topix"):
        # ^SOX has no volume; Topix's 1308.T ticker is an ETF — sum constituents instead.
        ser = fetch_constituent_volume_series(name, days=90)
        n = len(CONSTITUENT_FETCHERS[name]()) if CONSTITUENT_FETCHERS.get(name) else 0
        unit, source = "shares", f"Σ{n} constituents"
    else:
        conf = config.INDICES.get(name, {})
        df = fetch_yf_history(conf.get("index", ""), days=days)
        ser = df["Volume"] if (not df.empty and "Volume" in df.columns) else None
        unit, source = "shares", f"Index {conf.get('index', '')}"
    if ser is None or len(ser) == 0:
        return {}
    s = ser.replace(0, pd.NA).dropna()
    if len(s) < 3:
        return {}
    summ = indicators.turnover_series_summary(s, window)
    if not (summ["latest"] == summ["latest"]):  # NaN guard
        return {}
    return {
        "latest": summ["latest"],
        "avg20": summ["avg20"],
        "deviation_pct": summ["deviation_pct"],
        "unit": unit,
        "source": source,
        "as_of": s.index[-1].strftime("%Y-%m-%d"),
    }


# ---------- Constituents ----------

_WIKI_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}


def _wiki_tables(url: str) -> list:
    """Fetch Wikipedia page with proper headers, return parsed tables."""
    r = requests.get(url, headers=_WIKI_HEADERS, timeout=15)
    r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))


@st.cache_data(ttl=86400)
def constituents_sp500() -> list:
    try:
        tables = _wiki_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
        return tables[0]["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
    except Exception:
        return []


@st.cache_data(ttl=86400)
def constituents_nasdaq100() -> list:
    try:
        tables = _wiki_tables("https://en.wikipedia.org/wiki/Nasdaq-100")
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
    # PHLX Semiconductor Sector — top ~30 names. Update from
    # https://www.nasdaq.com/market-activity/quotes/sox if needed.
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
    """CSI 300 constituents via akshare. Returns yfinance-formatted tickers."""
    try:
        import akshare as ak
        df = ak.index_stock_cons_csindex(symbol="000300")
        # Code column is the stock code; SH (6xxxxx) or SZ (0xxxxx / 3xxxxx)
        codes = df["成分券代码"].astype(str).str.zfill(6) if "成分券代码" in df.columns else df.iloc[:, 0].astype(str).str.zfill(6)
        out = []
        for c in codes:
            suffix = ".SS" if c.startswith("6") else ".SZ"
            out.append(c + suffix)
        return out
    except Exception:
        return []


@st.cache_data(ttl=86400)
def constituents_chinext() -> list:
    """ChiNext (创业板) top constituents via akshare."""
    try:
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
    """CSI 1000 constituents via akshare."""
    try:
        import akshare as ak
        df = ak.index_stock_cons_csindex(symbol="000852")
        codes = df["成分券代码"].astype(str).str.zfill(6) if "成分券代码" in df.columns else df.iloc[:, 0].astype(str).str.zfill(6)
        out = []
        for c in codes:
            suffix = ".SS" if c.startswith("6") else ".SZ"
            out.append(c + suffix)
        return out
    except Exception:
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
                codes = df[code_col].astype(str).str.extract(r"(\d{4})")[0].dropna().unique()
                tickers = [c + ".T" for c in codes]
                if len(tickers) >= 200:
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
            codes = _re.findall(r'>\s*(\d{4})\s*<', r.text)
            seen = set()
            uniq = []
            for c in codes:
                if c not in seen:
                    seen.add(c)
                    uniq.append(c)
            if len(uniq) >= 200:
                return [c + ".T" for c in uniq]
    except Exception:
        pass

    # Fallback 2: hardcoded top 50 (proxy)
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
        url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls"
        r = requests.get(url, headers=_WIKI_HEADERS, timeout=30)
        if r.status_code == 200 and len(r.content) > 50000:
            df = pd.read_excel(io.BytesIO(r.content), sheet_name=0)
            # Filter to "プライム（内国株式）" = TSE Prime domestic stocks
            market_col = next((c for c in df.columns if "市場" in str(c) or "Market" in str(c)), None)
            code_col = next((c for c in df.columns if "コード" in str(c) or "Code" in str(c)), None)
            if market_col and code_col:
                prime = df[df[market_col].astype(str).str.contains("プライム.*内国|Prime.*Domestic", regex=True, na=False)]
                codes = prime[code_col].dropna().astype(str).str.zfill(4)
                tickers = [c + ".T" for c in codes if c.isdigit() and len(c) == 4]
                if len(tickers) >= 1000:
                    return tickers
    except Exception:
        pass

    # Fallback 1: EWJ (~180 stocks)
    ewj = _ishares_holdings("239665", "EWJ")
    valid = [t + ".T" for t in ewj if t.isdigit() and len(t) == 4]
    if len(valid) >= 100:
        return valid
    # Fallback 2: Nikkei 225
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


@st.cache_data(ttl=3600)
def fetch_aaii_sentiment() -> pd.DataFrame:
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


@st.cache_data(ttl=90000)  # 25 hours
def fetch_putcall_ratio() -> dict:
    """S&P 500 ($SPX) put/call ratio scraped directly from Barchart.

    Same source the boss linked to in his email:
        https://www.barchart.com/stocks/quotes/$SPX/put-call-ratios

    Order:
    0. Local JSON at data/barchart_pc.json (pushed by GitHub Actions) — preferred,
       because server's datacenter IP is blocked by Cloudflare on barchart.com.
    1. Direct curl scrape (works from residential IPs only).
    2. SPY options chain (yfinance) as last-resort fallback — different instrument,
       different numbers; only useful as a rough proxy.
    """
    # Method 0: local JSON pushed by GitHub Actions (preferred — bypasses Cloudflare)
    if BARCHART_LOCAL_JSON.exists():
        try:
            import json as _json
            import time as _time
            age_hours = (_time.time() - BARCHART_LOCAL_JSON.stat().st_mtime) / 3600
            if age_hours < 96:  # accept up to 96h — covers Fri→Mon weekend gap (Barchart cron is Mon-Fri) and most single-day holidays
                cached = _json.loads(BARCHART_LOCAL_JSON.read_text())
                if cached.get("vol_ratio") and cached.get("oi_ratio"):
                    return cached
        except Exception:
            pass

    # Method 1: Barchart $SPX (authoritative — same numbers the boss sees)
    try:
        ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
              "Safari/605.1.15 Chrome/120.0")
        result = subprocess.run(
            ["curl", "-sL", "-A", ua,
             "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
             "-H", "Accept-Language: en-US,en;q=0.9",
             "https://www.barchart.com/stocks/quotes/%24SPX/put-call-ratios"],
            capture_output=True, text=True, timeout=20
        )
        html = result.stdout
        if len(html) > 50000:
            import re
            def grab(label_pattern):
                m = re.search(label_pattern + r'</span></div><div[^>]*>([\d,]+(?:\.\d+)?)', html)
                if m:
                    return m.group(1).replace(",", "")
                # Looser pattern
                m = re.search(label_pattern + r'.{0,200}?>\s*([\d,]+(?:\.\d+)?)\s*<', html, re.DOTALL)
                return m.group(1).replace(",", "") if m else None

            put_vol  = grab(r'Put Volume Total')
            call_vol = grab(r'Call Volume Total')
            vol_pc   = grab(r'Put/Call Volume Ratio')
            put_oi   = grab(r'Put Open Interest Total')
            call_oi  = grab(r'Call Open Interest Total')
            oi_pc    = grab(r'Put/Call Open Interest Ratio')

            if vol_pc and oi_pc:
                return {
                    "source": "Barchart $SPX",
                    "vol_ratio": float(vol_pc),
                    "oi_ratio":  float(oi_pc),
                    "total_call_vol": int(float(call_vol)) if call_vol else 0,
                    "total_put_vol":  int(float(put_vol))  if put_vol  else 0,
                    "total_call_oi":  int(float(call_oi))  if call_oi  else 0,
                    "total_put_oi":   int(float(put_oi))   if put_oi   else 0,
                    "by_expiry": [],  # Barchart doesn't break down by expiration on free page
                    "asof": datetime.now().strftime("%Y-%m-%d %H:%M"),
                }
    except Exception:
        pass

    # Method 2 (fallback): SPY options via yfinance
    try:
        tk = yf.Ticker("SPY")
        expirations = tk.options[:4]
        if not expirations:
            return {}
        total_call_vol = total_put_vol = 0
        total_call_oi = total_put_oi = 0
        by_expiry = []
        for exp in expirations:
            try:
                chain = tk.option_chain(exp)
                cv = int(chain.calls["volume"].fillna(0).sum())
                pv = int(chain.puts["volume"].fillna(0).sum())
                co = int(chain.calls["openInterest"].fillna(0).sum())
                po = int(chain.puts["openInterest"].fillna(0).sum())
                total_call_vol += cv; total_put_vol += pv
                total_call_oi  += co; total_put_oi  += po
                by_expiry.append({
                    "expiration": exp,
                    "call_vol": cv, "put_vol": pv, "call_oi": co, "put_oi": po,
                    "vol_ratio": pv / cv if cv > 0 else None,
                    "oi_ratio":  po / co if co > 0 else None,
                })
            except Exception:
                continue
        if not by_expiry:
            return {}
        return {
            "source": "SPY options (fallback)",
            "vol_ratio": total_put_vol / total_call_vol if total_call_vol > 0 else None,
            "oi_ratio":  total_put_oi  / total_call_oi  if total_call_oi  > 0 else None,
            "total_call_vol": total_call_vol, "total_put_vol": total_put_vol,
            "total_call_oi":  total_call_oi,  "total_put_oi":  total_put_oi,
            "by_expiry": by_expiry,
            "asof": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
    except Exception:
        return {}
