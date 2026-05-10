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

VIX_TICKER = "^VIX"
GLD_TICKER = "GLD"

# Alert thresholds
VIX_ALERT_THRESHOLD = 30
RSI_UPPER = 70
RSI_LOWER = 30
TURNOVER_DEVIATION_PCT = 10  # latest vs 20-day avg

# Lookback / windows
HISTORY_DAYS = 500
RSI_WINDOW = 14
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
