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
    "Topix":       {"index": "1308.T",    "etf": "1308.T"},      # iShares Topix ETF (corr 0.93 with N225)
    "Taiwan":      {"index": "^TWII",     "etf": "0050.TW"},
    "KOSPI 200":   {"index": "^KS200",    "etf": "069500.KS"},
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
