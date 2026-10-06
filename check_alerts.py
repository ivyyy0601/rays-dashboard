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
import index_snapshot


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
    vix = data.load_market_series(config.VIX_TICKER)
    snapshot["vix_close"] = float(vix["Close"].iloc[-1]) if not vix.empty else None

    # GLD/VIX 10-week momentum
    gld = data.load_market_series(config.GLD_TICKER)
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

    # Tab 2: RSI + turnover Δ from the daily index snapshot (same file as the
    # dashboard table). A row that failed its data checks carries no Δ.
    from reliability import validate_row, expected_session
    idx_snap = index_snapshot.load()
    now = datetime.now(ET)
    for r in idx_snap.get('rows', []):
        validate_row(r, now)
    snapshot['data_warnings'] = [r['Index'] + ': ' + '; '.join(r.get('notes', []))
                                 for r in idx_snap.get('rows', []) if not r.get('data_ok')]
    if not idx_snap.get('rows'):
        snapshot['data_warnings'].append('Index snapshot unavailable; no index signals evaluated')
    try:
        target = expected_session('S&P 500', now)
        if vix.empty or vix.index[-1].strftime('%Y-%m-%d') != target:
            snapshot['vix_close'] = None
            snapshot.pop('gld_vix_momentum_10w', None)
            snapshot['data_warnings'].append('VIX data is not from latest completed US session')
        if gld.empty or gld.index[-1].strftime('%Y-%m-%d') != target:
            snapshot.pop('gld_vix_momentum_10w', None)
            snapshot['data_warnings'].append('GLD data is not from latest completed US session')
    except Exception as exc:
        snapshot['vix_close'] = None
        snapshot.pop('gld_vix_momentum_10w', None)
        snapshot['data_warnings'].append(f'US calendar validation unavailable: {exc}')
    tab2_rows = []
    for r in idx_snap.get("rows", []):
        dev = r.get("turnover_dev")
        tab2_rows.append({
            "Index": r["Index"],
            "RSI(14)": f"{r['rsi']:.1f}" if r.get("rsi") is not None and r.get('price_fresh') else "—",
            "Turnover (USD)": f"${r['turnover_usd'] / 1e9:,.1f}B" if r.get("turnover_usd") and r.get('data_ok') else "—",
            "Δ vs 20d": f"{dev:+.1f}%" if (dev is not None and r.get("data_ok")) else "—",
        })
    snapshot["tab2_rows"] = tab2_rows

    # Breadth — read from cache (already computed by update_cache.py)
    snapshot['breadth_rows'] = [dict(Index=r['Index'], **r['breadth'])
                                for r in idx_snap.get('rows', [])
                                if r.get('data_ok') and r.get('breadth')]

    # Put/Call
    pc = data.fetch_putcall_ratio() or {}
    if pc:
        try:
            target = expected_session('S&P 500', now)
            fetched = datetime.fromisoformat(pc['asof'].replace('Z', '+00:00'))
            if fetched.tzinfo is None or pc.get('stale') or fetched.astimezone(ET).date().isoformat() < target:
                raise ValueError('source retrieval predates latest completed US session')
        except Exception as exc:
            snapshot['data_warnings'].append(f'SPX put/call withheld: {exc}')
            pc = {}
    else:
        snapshot['data_warnings'].append('SPX put/call snapshot unavailable')
    snapshot['putcall'] = pc

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
    if snapshot.get('data_warnings'):
        import html
        triggered.append({'severity': 'info', 'title': '⚠️ Data quality warning — affected signals withheld',
                          'message': html.escape(' | '.join(snapshot['data_warnings']))})
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
        raise RuntimeError('Email configuration missing; daily email was not sent')

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
        raise


if __name__ == "__main__":
    main()
