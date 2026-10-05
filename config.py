# Each index: ticker for RSI/price, ETF for turnover fallback.
# Turnover = volume × closing price per day (per boss request).
#   - Use index ticker's volume × close where yfinance has it.
#   - Fall back to ETF where index has no volume data (SOX, Russell 2000, Topix).
INDICES = {
    "S&P 500":     {"index": "^GSPC",     "etf": "SPY"},
    "Nasdaq 100":  {"index": "^NDX",      "etf": "QQQ"},
    "SOX":         {"index": "^SOX",      "etf": "SOXX"},        # ^SOX has no volume
    "Russell 2000":{"index": "^RUT",      "etf": "IWM"},         # ^RUT has no volume
    "Hang Seng":   {"index": "^HSI",      "etf": "2800.HK"},
    "CSI 300":     {"index": "000300.SS", "etf": "510300.SS"},
    "CSI 1000":    {"index": "000852.SS", "etf": "560010.SS"},
    "ChiNext":     {"index": "399006.SZ", "etf": "159915.SZ"},
    "Nikkei 225":  {"index": "^N225",     "etf": "1321.T"},
    "Topix":       {"index": "TSE:TOPIX", "etf": "1308.T"},      # True index; ETF only for explicitly ETF-based features
    "Taiwan":      {"index": "^TWII",     "etf": "0050.TW"},
    "KOSPI 200":   {"index": "KRX:KOSPI200", "etf": "069500.KS"},
}

# TradingView symbols for RSI — table RSI(14) is pulled DIRECTLY from TradingView's
# own RSI (the number shown on tradingview.com), not computed locally.
# Tuple = (symbol, exchange, screener). All 12 verified against scanner.tradingview.com.
TV_SYMBOLS = {
    "S&P 500":      ("SPX",      "SP",     "america"),
    "Nasdaq 100":   ("NDX",      "NASDAQ", "america"),
    "SOX":          ("SOX",      "NASDAQ", "america"),
    "Russell 2000": ("RUT",      "TVC",    "america"),
    "Hang Seng":    ("HSI",      "HSI",    "hongkong"),
    "CSI 300":      ("000300",   "SSE",    "china"),
    "CSI 1000":     ("000852",   "SSE",    "china"),
    "ChiNext":      ("399006",   "SZSE",   "china"),
    "Nikkei 225":   ("NI225",    "TVC",    "japan"),
    "Topix":        ("TOPIX",    "TSE",    "japan"),
    "Taiwan":       ("IX0001",   "TWSE",   "taiwan"),
    "KOSPI 200":    ("KOSPI200", "KRX",    "korea"),
}

# Currency of each tracking ETF — turnover (ETF close × volume) is computed in
# the ETF's native currency, then converted to USD for cross-market comparison.
ETF_CURRENCY = {
    "SPY": "USD", "QQQ": "USD", "SOXX": "USD", "IWM": "USD",
    "2800.HK": "HKD",
    "510300.SS": "CNY", "560010.SS": "CNY", "159915.SZ": "CNY",
    "1321.T": "JPY", "1308.T": "JPY",
    "0050.TW": "TWD", "069500.KS": "KRW",
}

VIX_TICKER = "^VIX"
GLD_TICKER = "GLD"

# Alert thresholds
VIX_ALERT_THRESHOLD = 30
RSI_UPPER = 70
RSI_LOWER = 30
TURNOVER_DEVIATION_PCT = 10  # latest vs 20-day avg

# Lookback / windows
HISTORY_DAYS = 500          # fetch span (kept long for RSI warmup + 20d turnover avg)
RSI_WINDOW = 14
RSI_CHART_DAYS = 60         # RSI History chart display span (~3 months of trading days)
TURNOVER_AVG_WINDOW = 20
MA_SHORT = 50
MA_LONG = 200

# Theoretical constituent counts per index (for coverage % calculation in Tab 3)
INDEX_THEORETICAL_SIZE = {
    "S&P 500":     503,
    "Nasdaq 100":  101,
    "SOX":         30,
    "Russell 2000":1923,
    "Hang Seng":   85,
    "CSI 300":     300,
    "CSI 1000":    1000,
    "ChiNext":     100,
    "Nikkei 225":  225,
    "Topix":       1574,    # TSE Prime market = Topix (post-2022 reorganization)
    "Taiwan":      1100,    # TWSE listed stocks ~1,100 (full list via TWSE openapi)
    "KOSPI 200":   200,
}

# ---------- Tab 2 snapshot (index_snapshot.py) ----------

# Close + RSI come from TradingView's dated daily index snapshot for these
# (Yahoo has no usable series for the real index; no ETF proxy, no history).
TV_SNAPSHOT_INDICES = {"Topix", "KOSPI 200"}

# Index volume & turnover = Σ of every constituent's daily volume / close × volume.
# Provider "index volume" fields were checked against official figures and are
# not the index's own volume (^NDX/^GSPC/Sina ChiNext report a wider market,
# ^N225 an unknown unit, RTY=F futures contracts). Value = minimum list size
# expected — a shorter list means a fallback/broken source and is flagged.
CONSTITUENT_MIN = {
    "S&P 500": 495, "Nasdaq 100": 98, "SOX": 28, "Russell 2000": 1800,
    "Hang Seng": 80, "CSI 300": 290, "CSI 1000": 950, "ChiNext": 95,
    "Nikkei 225": 220, "Topix": 1400, "Taiwan": 1000, "KOSPI 200": 190,
}
# A session counts (latest day and every day in the 20-day window) only if at
# least this share of the listed constituents reported volume that day.
CONSTITUENT_COVERAGE_MIN = 0.9

# Trading currency of each index's constituents, and its Yahoo FX ticker
# (quoted as units of currency per 1 USD) for the USD turnover column.
INDEX_CURRENCY = {
    "S&P 500": "USD", "Nasdaq 100": "USD", "SOX": "USD", "Russell 2000": "USD",
    "Hang Seng": "HKD", "CSI 300": "CNY", "CSI 1000": "CNY", "ChiNext": "CNY",
    "Nikkei 225": "JPY", "Topix": "JPY", "Taiwan": "TWD", "KOSPI 200": "KRW",
}
FX_TICKERS = {"HKD": "HKD=X", "CNY": "CNY=X", "JPY": "JPY=X", "TWD": "TWD=X", "KRW": "KRW=X"}

# Constituent-list monitoring: every run diffs today's list with the last saved
# one (data/constituents/), logs changes to data/constituent_changes.csv, and
# warns if a list hasn't changed for longer than its index normally goes
# between reviews — a sign the source stopped updating.
MAX_DAYS_LIST_UNCHANGED = {
    "S&P 500": 120, "Nasdaq 100": 400, "SOX": 400, "Russell 2000": 400,
    "Hang Seng": 120, "CSI 300": 200, "CSI 1000": 200, "ChiNext": 200,
    "Nikkei 225": 200, "Topix": 60, "Taiwan": 60, "KOSPI 200": 200,
}

# Cross-check for constituent lists that come from Wikipedia (no official
# source): every run compares Yahoo's index-level volume with our Σ
# constituents on the same day. A ratio outside the normal band means the list
# is probably out of date (a missing or extra heavyweight) → row flagged, no
# alert. Bands from 20 sessions of observations (Oct 2026): ^HSI ran 1.05–1.11;
# ^KS200 (reported in thousands of shares) 1.01.
# index: (Yahoo ticker, unit multiplier, (low, high))
VOLUME_CROSSCHECK = {
    "Hang Seng": ("^HSI", 1, (1.02, 1.15)),
    "KOSPI 200": ("^KS200", 1000, (0.95, 1.07)),
}
