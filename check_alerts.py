"""
Daily alert check — pulls latest data, evaluates all alert conditions,
sends a single summary email if anything triggered.

Run after update_cache.py (either via cron or chained automatically).
Configure SMTP credentials in email_config.json (see email_config.example.json).
"""
import json
import logging
import smtplib
import sys
import types
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo


# Mock streamlit so we can import data.py / indicators.py as a plain script
fake_st = types.ModuleType("streamlit")
def _cache_data(*a, **k):
    if a and callable(a[0]):
        return a[0]
    def deco(f): return f
    return deco
_cache_data.clear = lambda: None
fake_st.cache_data = _cache_data
class _NoopProgress:
    def progress(self, *a, **k): pass
    def empty(self): pass
fake_st.progress = lambda *a, **k: _NoopProgress()
fake_st.warning = fake_st.info = fake_st.error = lambda *a, **k: None
sys.modules["streamlit"] = fake_st

import alerts
import config
import data
import indicators


ET = ZoneInfo("America/New_York")
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
CACHE_FILE = DATA_DIR / "breadth_cache.json"
EMAIL_CONFIG = ROOT / "email_config.json"
ALERT_LOG = DATA_DIR / "alert_log.csv"

logging.disable(logging.CRITICAL)


def build_snapshot() -> dict:
    """Pull latest data needed for alert evaluation."""
    snapshot = {}

    # VIX
    vix = data.fetch_yf_history(config.VIX_TICKER, days=200)
    snapshot["vix_close"] = float(vix["Close"].iloc[-1]) if not vix.empty else None

    # GLD/VIX 10-week momentum
    gld = data.fetch_yf_history(config.GLD_TICKER, days=300)
    if not gld.empty and not vix.empty:
        gld_w = gld["Close"].resample("W-FRI").last()
        vix_w = vix["Close"].resample("W-FRI").last()
        ratio_w = (gld_w / vix_w).dropna()
        if len(ratio_w) > 10:
            snapshot["gld_vix_momentum_10w"] = float(ratio_w.iloc[-1] - ratio_w.iloc[-11])

    # AAII
    aaii_df = data.fetch_aaii_sentiment()
    if not aaii_df.empty:
        latest = aaii_df.iloc[-1]
        snapshot["aaii_latest"] = {
            "Bullish": float(latest["Bullish"]),
            "Neutral": float(latest["Neutral"]),
            "Bearish": float(latest["Bearish"]),
        }

    # Tab 2: RSI + Turnover for each index (SAME logic as dashboard.py)
    FORCE_ETF_TICKERS = {"^RUT", "^SOX"}  # yfinance bugs for these
    tab2_rows = []
    for name, conf in config.INDICES.items():
        df_idx = data.fetch_yf_history(conf["index"], days=500)
        df_etf = data.fetch_yf_history(conf["etf"], days=500)
        if df_idx.empty:
            continue
        rsi = indicators.compute_rsi(df_idx["Close"], config.RSI_WINDOW)
        rsi_latest = float(rsi.iloc[-1]) if not rsi.empty else None

        # Match dashboard's logic: prefer index volume, fall back to ETF
        if conf["index"] in FORCE_ETF_TICKERS:
            idx_has_volume = False
        else:
            idx_has_volume = (
                "Volume" in df_idx.columns
                and df_idx["Volume"].tail(5).fillna(0).median() > 0
            )
        if idx_has_volume:
            tov = indicators.turnover_summary(df_idx["Close"], df_idx["Volume"], 20)
        elif not df_etf.empty and "Volume" in df_etf.columns:
            tov = indicators.turnover_summary(df_etf["Close"], df_etf["Volume"], 20)
        else:
            tov = {"deviation_pct": None}
        delta = tov.get("deviation_pct")
        tab2_rows.append({
            "Index": name,
            "RSI(14)": f"{rsi_latest:.1f}" if rsi_latest is not None else "—",
            "Δ vs 20d": f"{delta:+.1f}%" if delta is not None and delta == delta else "—",
        })
    snapshot["tab2_rows"] = tab2_rows

    # Breadth — read from cache (already computed by update_cache.py)
    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())
            snapshot["breadth_rows"] = cache.get("rows", [])
        except Exception:
            pass

    # Put/Call
    snapshot["putcall"] = data.fetch_putcall_ratio() or {}

    return snapshot


def send_email(subject: str, body_html: str, body_text: str, cfg: dict):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg["from_email"]
    msg["To"] = ", ".join(cfg["to_emails"])
    msg.attach(MIMEText(body_text, "plain"))
    msg.attach(MIMEText(body_html, "html"))

    with smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"]) as server:
        server.starttls()
        server.login(cfg["from_email"], cfg["app_password"])
        server.send_message(msg)


def format_email(triggered: list, ts: datetime) -> tuple:
    """Returns (text_body, html_body)."""
    text_lines = [
        f"Market Sentiment Dashboard — Daily Alert Summary",
        f"Generated: {ts.strftime('%Y-%m-%d %H:%M ET')}",
        "",
        f"{len(triggered)} alert(s) triggered:",
        "",
    ]
    html_parts = [f"""
    <div style='font-family:-apple-system,sans-serif; max-width:600px;'>
      <h2 style='color:#1a1a1a;'>📊 Market Sentiment Daily Alert</h2>
      <p style='color:#666;'>Generated: {ts.strftime('%Y-%m-%d %H:%M ET')}</p>
      <p><b>{len(triggered)} alert(s) triggered:</b></p>
    """]
    severity_color = {"high": "#dc2626", "medium": "#d97706", "info": "#2563eb"}
    for a in triggered:
        text_lines.append(f"  [{a['severity'].upper()}] {a['title']}")
        text_lines.append(f"    {a['message']}")
        text_lines.append("")
        c = severity_color.get(a["severity"], "#666")
        html_parts.append(f"""
        <div style='border-left:4px solid {c}; padding:8px 12px; margin:12px 0; background:#f9fafb;'>
          <div style='font-weight:600; color:{c};'>{a['title']}</div>
          <div style='color:#374151; margin-top:4px;'>{a['message']}</div>
        </div>
        """)
    html_parts.append("<p style='color:#999; font-size:12px;'>— Rays Market Dashboard</p></div>")
    return "\n".join(text_lines), "".join(html_parts)


def append_log(triggered: list, ts: datetime):
    """Append triggered alerts to alert_log.csv for audit trail."""
    import csv
    DATA_DIR.mkdir(exist_ok=True)
    new_file = not ALERT_LOG.exists()
    with open(ALERT_LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["timestamp_et", "severity", "title", "message"])
        for a in triggered:
            w.writerow([ts.isoformat(), a["severity"], a["title"], a["message"]])


def main():
    ts = datetime.now(ET)
    print(f"[{ts.strftime('%Y-%m-%d %H:%M:%S ET')}] Running alert check...")

    snapshot = build_snapshot()
    print(f"  Snapshot built: VIX={snapshot.get('vix_close')}, "
          f"AAII={'yes' if snapshot.get('aaii_latest') else 'no'}, "
          f"breadth_rows={len(snapshot.get('breadth_rows', []))}")

    triggered = alerts.check_all_alerts(snapshot)
    print(f"  {len(triggered)} alert(s) triggered.")
    for a in triggered:
        print(f"    [{a['severity']}] {a['title']}")

    append_log(triggered, ts)

    if not triggered:
        print("  No alerts — skipping email.")
        return

    if not EMAIL_CONFIG.exists():
        print(f"  ⚠️  {EMAIL_CONFIG} not found — see email_config.example.json. Skipping email.")
        return

    cfg = json.loads(EMAIL_CONFIG.read_text())
    text_body, html_body = format_email(triggered, ts)
    subject = f"📊 {len(triggered)} Market Alert(s) — {ts.strftime('%Y-%m-%d')}"
    try:
        send_email(subject, html_body, text_body, cfg)
        print(f"  ✓ Email sent to {cfg['to_emails']}")
    except Exception as e:
        print(f"  ✗ Email send failed: {e}")


if __name__ == "__main__":
    main()
