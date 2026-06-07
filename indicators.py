import pandas as pd
import numpy as np


def compute_rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False).mean()
    # avg_loss == 0 with avg_gain > 0 → rs = inf → rsi = 100 (max overbought, correct)
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def turnover_summary(close: pd.Series, volume: pd.Series, window: int = 20) -> dict:
    """Compute turnover (close × volume), 20-day rolling avg, and % deviation."""
    # Forward-fill volume to handle scattered NaN (e.g. holidays / data gaps)
    turnover = (close * volume.replace(0, np.nan).ffill()).dropna()
    if turnover.empty or len(turnover) < 2:
        return {"latest": np.nan, "avg20": np.nan, "deviation_pct": np.nan}
    # min_periods=window//2 → tolerate up to 10 missing days within the window
    avg = turnover.rolling(window, min_periods=window // 2).mean().iloc[-1]
    latest = turnover.iloc[-1]
    deviation = (latest / avg - 1) * 100 if avg and avg > 0 else np.nan
    return {"latest": float(latest), "avg20": float(avg), "deviation_pct": float(deviation)}


def turnover_series_summary(turnover: pd.Series, window: int = 20) -> dict:
    """Given a daily turnover series, return latest / prior-window avg / deviation%.

    Two correctness fixes:
    1. Drop trailing bars whose value is < 40% of the recent median (incomplete /
       not-yet-settled bars that otherwise fire false LOW alerts).
    2. Compare the latest value against the PRIOR `window` average (excludes today),
       so a spike isn't diluted by being part of its own baseline.
    """
    s = turnover.dropna()
    if len(s) < 3:
        return {"latest": np.nan, "avg20": np.nan, "deviation_pct": np.nan, "n_dropped": 0}
    med = s.tail(window).median()
    n_dropped = 0
    while len(s) > 2 and s.iloc[-1] < 0.4 * med:
        s = s.iloc[:-1]
        n_dropped += 1
    latest = s.iloc[-1]
    prior = s.iloc[-(window + 1):-1] if len(s) > window else s.iloc[:-1]
    avg = prior.mean() if len(prior) else np.nan
    dev = (latest / avg - 1) * 100 if avg and avg > 0 else np.nan
    return {
        "latest": float(latest),
        "avg20": float(avg) if pd.notna(avg) else np.nan,
        "deviation_pct": float(dev) if pd.notna(dev) else np.nan,
        "n_dropped": n_dropped,
    }


def turnover_clean(close: pd.Series, volume: pd.Series, window: int = 20) -> dict:
    """Turnover = close × volume, with two correctness fixes:

    1. Drop trailing bars whose volume is < 40% of the recent median — these are
       incomplete / not-yet-settled bars that otherwise produce false LOW alerts.
    2. Compare the latest value against the PRIOR `window` average (excludes the
       latest bar), so a spike isn't diluted by being part of its own baseline.

    Returns latest, avg (prior window), deviation_pct, and n_dropped.
    """
    df = pd.DataFrame({"c": close, "v": volume}).dropna()
    df["v"] = df["v"].replace(0, np.nan)
    df = df.dropna(subset=["v"])
    if len(df) < 3:
        return {"latest": np.nan, "avg20": np.nan, "deviation_pct": np.nan, "n_dropped": 0}
    return turnover_series_summary(df["c"] * df["v"], window)


def breadth_above_both_ma(prices_df: pd.DataFrame, short: int = 50, long: int = 200) -> dict:
    """prices_df: index=dates, columns=tickers, values=close prices.

    Returns 4 percentages:
      - pct_above_short:  % of stocks above the short MA (e.g., 50-day)
      - pct_above_long:   % of stocks above the long MA (e.g., 200-day)
      - pct_above_both:   % above BOTH (intersection — boss's requirement)
      - pct_below_both:   % below BOTH (intersection — boss's requirement)
    """
    if prices_df.empty:
        return {"pct_above_short": np.nan, "pct_above_long": np.nan,
                "pct_above_both": np.nan, "pct_below_both": np.nan, "n_stocks": 0}
    # Forward-fill scattered NaN gaps (holidays, halts, stale data) so a single
    # missing day in the 200-day window doesn't kill the entire rolling MA.
    filled = prices_df.ffill()
    ma_s = filled.rolling(short).mean().iloc[-1]
    ma_l = filled.rolling(long).mean().iloc[-1]
    last = filled.iloc[-1]
    valid = ma_s.notna() & ma_l.notna() & last.notna()
    n = int(valid.sum())
    if n == 0:
        return {"pct_above_short": np.nan, "pct_above_long": np.nan,
                "pct_above_both": np.nan, "pct_below_both": np.nan, "n_stocks": 0}
    above_short = ((last > ma_s) & valid).sum()
    above_long = ((last > ma_l) & valid).sum()
    above_both = ((last > ma_s) & (last > ma_l) & valid).sum()
    below_both = ((last < ma_s) & (last < ma_l) & valid).sum()
    return {
        "pct_above_short": above_short / n * 100,
        "pct_above_long": above_long / n * 100,
        "pct_above_both": above_both / n * 100,
        "pct_below_both": below_both / n * 100,
        "n_stocks": n,
    }


def daily_advance_decline(prices_df: pd.DataFrame) -> dict:
    if prices_df.shape[0] < 2:
        return {"pct_up": np.nan, "pct_down": np.nan, "advancers": 0, "decliners": 0}
    pct = prices_df.iloc[-1] / prices_df.iloc[-2] - 1
    advancers = (pct > 0).sum()
    decliners = (pct < 0).sum()
    total = advancers + decliners
    return {
        "pct_up": advancers / total * 100 if total else np.nan,
        "pct_down": decliners / total * 100 if total else np.nan,
        "advancers": int(advancers),
        "decliners": int(decliners),
    }


def daily_advance_decline_history(prices_df: pd.DataFrame, days: int = 30) -> list:
    """Compute % up / % down for each of the last N trading days.

    Returns list of dicts: [{date, pct_up, pct_down, net}, ...]
    """
    if prices_df.empty or prices_df.shape[0] < 2:
        return []
    # Forward-fill to bridge minor data gaps (1-2 missing days)
    filled = prices_df.ffill()
    pct = filled.pct_change().tail(days)
    out = []
    for date, row in pct.iterrows():
        valid = row.notna()
        n = int(valid.sum())
        if n == 0:
            continue
        ups = int(((row > 0) & valid).sum())
        downs = int(((row < 0) & valid).sum())
        total = ups + downs  # ignore unchanged
        if total == 0:
            continue
        pct_up = ups / total * 100
        pct_down = downs / total * 100
        out.append({
            "date": date.strftime("%Y-%m-%d"),
            "pct_up": float(pct_up),
            "pct_down": float(pct_down),
            "net": float(pct_up - pct_down),
        })
    return out
