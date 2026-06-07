"""
Alert checking logic. Pure functions — no I/O, easy to test.

Each check_* function returns a list of dicts:
    {"severity": "high"|"medium"|"info", "title": "...", "message": "..."}

The main entry point check_all_alerts() runs every check and returns
a flat list of triggered alerts.
"""
import json
from pathlib import Path

import config


def check_vix(vix_close: float) -> list:
    if vix_close is None:
        return []
    if vix_close > config.VIX_ALERT_THRESHOLD:
        return [{
            "severity": "high",
            "title": f"🚨 VIX Spike: {vix_close:.2f}",
            "message": f"VIX closed at {vix_close:.2f}, above the {config.VIX_ALERT_THRESHOLD} threshold. "
                       f"Indicates elevated fear / volatility in US markets.",
        }]
    return []


def check_gld_vix_momentum(momentum_10w: float) -> list:
    if momentum_10w is None:
        return []
    if momentum_10w < -7:
        return [{
            "severity": "high",
            "title": f"🚨 GLD/VIX Bottom Signal: momentum {momentum_10w:+.2f}",
            "message": f"Weekly GLD/VIX 10-period momentum dropped to {momentum_10w:+.2f}, "
                       f"below the historical -7 threshold. Per Zac Markovich's research, "
                       f"this has marked SPY bottoms in 2008/2020/2022 with 75-85% positive "
                       f"4-12 week forward returns.",
        }]
    return []


def check_rsi(rows: list) -> list:
    """rows: list of {Index, RSI(14)} dicts from Tab 2 table."""
    out = []
    for r in rows:
        try:
            rsi = float(r.get("RSI(14)", "—").replace("%", ""))
        except (ValueError, AttributeError):
            continue
        if rsi > config.RSI_UPPER:
            out.append({
                "severity": "medium",
                "title": f"🟡 RSI Overbought: {r['Index']} = {rsi:.1f}",
                "message": f"{r['Index']} RSI(14) is {rsi:.1f} (>{config.RSI_UPPER}). "
                           f"Historical mean-reversion suggests possible pullback.",
            })
        elif rsi < config.RSI_LOWER:
            out.append({
                "severity": "medium",
                "title": f"🟢 RSI Oversold: {r['Index']} = {rsi:.1f}",
                "message": f"{r['Index']} RSI(14) is {rsi:.1f} (<{config.RSI_LOWER}). "
                           f"Possible bounce opportunity.",
            })
    return out


def check_turnover(rows: list) -> list:
    """rows from Tab 2 table with 'Δ vs 20d' column like '+15.2%' or '-12.0%'.

    Metric is real trading VOLUME vs its 20-day average (no ETF)."""
    out = []
    for r in rows:
        delta_str = r.get("Δ vs 20d", "—").replace("%", "").replace("+", "").strip()
        try:
            delta = float(delta_str)
        except ValueError:
            continue
        if abs(delta) > config.TURNOVER_DEVIATION_PCT:
            direction = "above" if delta > 0 else "below"
            out.append({
                "severity": "info",
                "title": f"📊 Volume {direction.upper()} avg: {r['Index']} {delta:+.1f}%",
                "message": f"{r['Index']} latest trading volume is {delta:+.1f}% from its 20-day "
                           f"average — {'unusual activity / buying interest' if delta > 0 else 'low participation'}.",
            })
    return out


def check_aaii(latest_row: dict) -> list:
    """latest_row: latest AAII row with Bullish/Neutral/Bearish (0-1)."""
    if not latest_row:
        return []
    bull = latest_row.get("Bullish", 0)
    bear = latest_row.get("Bearish", 0)
    net = bull - bear
    out = []
    if bull > 0.50:
        out.append({
            "severity": "medium",
            "title": f"📈 AAII Extreme Bullish: {bull*100:.1f}%",
            "message": f"AAII Bullish reading is {bull*100:.1f}%, above 50%. "
                       f"Historically a contrarian bearish signal at extremes.",
        })
    if bear > 0.50:
        out.append({
            "severity": "medium",
            "title": f"📉 AAII Extreme Bearish: {bear*100:.1f}%",
            "message": f"AAII Bearish reading is {bear*100:.1f}%, above 50%. "
                       f"Historically a contrarian bullish signal at extremes.",
        })
    if net > 0.30:
        out.append({
            "severity": "info",
            "title": f"⚠️ AAII Net very bullish: +{net*100:.1f}%",
            "message": "Net (Bull-Bear) above +30% — sentiment is unusually bullish.",
        })
    elif net < -0.30:
        out.append({
            "severity": "info",
            "title": f"⚠️ AAII Net very bearish: {net*100:+.1f}%",
            "message": "Net (Bull-Bear) below -30% — sentiment is unusually bearish.",
        })
    return out


def check_breadth(rows: list) -> list:
    """rows: list of breadth dicts from cache file."""
    out = []
    for r in rows:
        above = r.get("pct_above_both")
        below = r.get("pct_below_both")
        name = r.get("Index", "?")
        if above is not None and above < 20:
            out.append({
                "severity": "high",
                "title": f"📉 Broad weakness: {name} only {above:.0f}% above both MA",
                "message": f"Less than 20% of {name} stocks are above both 50- and 200-day MAs. "
                           f"Often marks oversold conditions / potential reversal.",
            })
        if above is not None and above > 80:
            out.append({
                "severity": "medium",
                "title": f"📈 Broad strength: {name} {above:.0f}% above both MA",
                "message": f"Over 80% of {name} stocks are above both MAs. "
                           f"Strong trend but possibly extended.",
            })
        if below is not None and below > 50:
            out.append({
                "severity": "high",
                "title": f"📉 Broad breakdown: {name} {below:.0f}% below both MA",
                "message": f"Over 50% of {name} stocks are below both MAs — broad weakness.",
            })
    return out


def check_putcall(pc: dict) -> list:
    if not pc:
        return []
    vol = pc.get("vol_ratio")
    out = []
    if vol is not None:
        if vol > 1.3:
            out.append({
                "severity": "medium",
                "title": f"⚠️ Put/Call Volume Extreme: {vol:.2f}",
                "message": f"S&P 500 put/call volume ratio is {vol:.2f} (>1.3). "
                           f"Heavy put activity — fear / hedging. "
                           f"Often a contrarian bullish signal at extremes.",
            })
        elif vol < 0.7:
            out.append({
                "severity": "medium",
                "title": f"⚠️ Put/Call Volume Extreme: {vol:.2f}",
                "message": f"S&P 500 put/call volume ratio is {vol:.2f} (<0.7). "
                           f"Heavy call activity — euphoria / greed. "
                           f"Often a contrarian bearish signal at extremes.",
            })
    return out


def check_all_alerts(snapshot: dict) -> list:
    """Run every check on a snapshot dict containing all the data.

    snapshot keys:
        vix_close, gld_vix_momentum_10w, tab2_rows, aaii_latest,
        breadth_rows, putcall
    """
    alerts = []
    alerts += check_vix(snapshot.get("vix_close"))
    alerts += check_gld_vix_momentum(snapshot.get("gld_vix_momentum_10w"))
    alerts += check_rsi(snapshot.get("tab2_rows", []))
    alerts += check_turnover(snapshot.get("tab2_rows", []))
    alerts += check_aaii(snapshot.get("aaii_latest", {}))
    alerts += check_breadth(snapshot.get("breadth_rows", []))
    alerts += check_putcall(snapshot.get("putcall", {}))
    return alerts
