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

    # Volume Δ from the daily cache (same source as dashboard Tab 2).
    vol_cache = {}
    if CACHE_FILE.exists():
        try:
            _c = json.loads(CACHE_FILE.read_text())
            for r in _c.get("rows", []):
                if r.get("volume_dev") is not None:
                    vol_cache[r["Index"]] = r["volume_dev"]
        except Exception:
            pass

    # Tab 2: RSI + Volume Δ for each index (SAME logic & source as dashboard.py)
    tab2_rows = []
    for name, conf in config.INDICES.items():
        df_idx = data.fetch_yf_history(conf["index"], days=500)
        if df_idx.empty:
            continue
        # RSI(14): pulled directly from TradingView (matches dashboard table &
        # tradingview.com). Falls back to locally-computed RSI if TV is unavailable.
        rsi_latest = data.fetch_tv_rsi(name)
        if rsi_latest is None:
            rsi = indicators.compute_rsi(df_idx["Close"], config.RSI_WINDOW)
            rsi_latest = float(rsi.iloc[-1]) if not rsi.empty else None

        # Volume Δ vs 20d — real trading volume, no ETF. Prefer cache; live fallback.
        if name in vol_cache:
            delta = vol_cache[name]
        else:
            vsum = data.fetch_index_volume_summary(name, 20)
            delta = vsum.get("deviation_pct") if vsum else None
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


def format_email(triggered: list, ts: datetime, ai_summary: str = None) -> tuple:
    """Returns (text_body, html_body). ai_summary, if present, is prepended."""
    text_lines = [
        f"Market Sentiment Dashboard — Daily Brief",
        f"Generated: {ts.strftime('%Y-%m-%d %H:%M ET')}",
        "",
    ]
    html_parts = [f"""
    <div style='font-family:-apple-system,sans-serif; max-width:600px;'>
      <h2 style='color:#1a1a1a;'>📊 Market Sentiment Daily Brief</h2>
      <p style='color:#666;'>Generated: {ts.strftime('%Y-%m-%d %H:%M ET')}</p>
    """]

    # AI summary at the top
    if ai_summary:
        text_lines += ["🤖 AI ANALYSIS", "", ai_summary, "", "—" * 30, ""]
        import html as _html
        safe = _html.escape(ai_summary).replace("\n", "<br>")
        html_parts.append(f"""
        <div style='border:1px solid #c7d2fe; border-radius:8px; padding:14px 16px;
                    margin:12px 0; background:#eef2ff;'>
          <div style='font-weight:700; color:#4338ca; margin-bottom:8px;'>🤖 AI Analysis</div>
          <div style='color:#1e293b; line-height:1.5; white-space:normal;'>{safe}</div>
        </div>
        """)

    text_lines += [f"{len(triggered)} alert(s) triggered:", ""]
    html_parts.append(f"<p><b>{len(triggered)} alert(s) triggered:</b></p>")
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

    # AI daily summary across all tabs (None if no ANTHROPIC_API_KEY configured)
    ai_summary = None
    try:
        import ai_analysis
        ai_summary = ai_analysis.daily_summary(snapshot)
        print(f"  AI summary: {'generated' if ai_summary else 'skipped (no API key)'}")
    except Exception as e:
        print(f"  AI summary failed: {type(e).__name__}: {e}")

    # Send when there are alerts OR an AI brief to deliver. With no alerts and no
    # AI key, preserve the old behavior (no email).
    if not triggered and not ai_summary:
        print("  No alerts and no AI summary — skipping email.")
        return

    if not EMAIL_CONFIG.exists():
        print(f"  ⚠️  {EMAIL_CONFIG} not found — see email_config.example.json. Skipping email.")
        return

    cfg = json.loads(EMAIL_CONFIG.read_text())
    text_body, html_body = format_email(triggered, ts, ai_summary=ai_summary)
    n = len(triggered)
    subject = (f"📊 Market Daily Brief — {ts.strftime('%Y-%m-%d')}" if n == 0
               else f"📊 {n} Market Alert(s) — {ts.strftime('%Y-%m-%d')}")
    try:
        send_email(subject, html_body, text_body, cfg)
        print(f"  ✓ Email sent to {cfg['to_emails']}")
    except Exception as e:
        print(f"  ✗ Email send failed: {e}")


if __name__ == "__main__":
    main()
