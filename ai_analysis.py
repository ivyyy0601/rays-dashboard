"""
AI market analysis powered by Claude (Anthropic API).

Two entry points:
  - daily_summary(snapshot)            → one-shot text summary for the alert email
  - stream_chat(messages, snapshot)    → streaming generator for the dashboard sidebar chat

The API key is read from ANTHROPIC_API_KEY, or from anthropic_key.json
({"api_key": "..."}) next to this file. Both are gitignored. If neither is
present, get_client() returns None and callers degrade gracefully (no crash).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

MODEL = "claude-opus-4-8"
_KEY_FILE = Path(__file__).parent / "anthropic_key.json"


def get_api_key() -> str | None:
    """Resolve the Anthropic API key from env or anthropic_key.json. None if unset."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key.strip()
    if _KEY_FILE.exists():
        try:
            return json.loads(_KEY_FILE.read_text()).get("api_key", "").strip() or None
        except Exception:
            return None
    return None


def get_client():
    """Return an Anthropic client, or None if no key / SDK unavailable."""
    key = get_api_key()
    if not key:
        return None
    try:
        import anthropic
    except ImportError:
        return None
    return anthropic.Anthropic(api_key=key)


def format_snapshot(snapshot: dict) -> str:
    """Render the cross-tab market snapshot as compact text for the model.

    `snapshot` is the dict built by check_alerts.build_snapshot() (also assembled
    live in the dashboard). Missing keys are simply skipped.
    """
    lines = []

    # Tab 1 — Volatility & Sentiment
    vol = []
    if snapshot.get("vix_close") is not None:
        vol.append(f"VIX close: {snapshot['vix_close']:.2f}")
    if snapshot.get("gld_vix_momentum_10w") is not None:
        vol.append(f"GLD/VIX 10-week momentum: {snapshot['gld_vix_momentum_10w']:+.2f}")
    aaii = snapshot.get("aaii_latest")
    if aaii:
        vol.append(
            f"AAII sentiment — Bullish {aaii['Bullish']*100:.0f}%, "
            f"Neutral {aaii['Neutral']*100:.0f}%, Bearish {aaii['Bearish']*100:.0f}%"
        )
    pc = snapshot.get("putcall") or {}
    if pc.get("vol_ratio") is not None:
        vol.append(f"S&P put/call volume ratio: {pc['vol_ratio']:.2f}")
    if pc.get("oi_ratio") is not None:
        vol.append(f"S&P put/call open-interest ratio: {pc['oi_ratio']:.2f}")
    if vol:
        lines.append("## Volatility & Sentiment\n" + "\n".join(f"- {v}" for v in vol))

    # Tab 2 — Indices (RSI / Volume)
    rows = snapshot.get("tab2_rows") or []
    if rows:
        body = "\n".join(
            f"- {r['Index']}: RSI(14) {r.get('RSI(14)', '—')}, "
            f"volume Δ vs 20d {r.get('Δ vs 20d', '—')}"
            for r in rows
        )
        lines.append("## Indices — RSI & Volume\n" + body)

    # Tab 3 — Breadth & A/D
    breadth = snapshot.get("breadth_rows") or []
    if breadth:
        def _p(v):
            return f"{v:.0f}%" if isinstance(v, (int, float)) else "—"
        body = "\n".join(
            f"- {r.get('Index')}: % >50MA {_p(r.get('pct_above_short'))}, "
            f"% >200MA {_p(r.get('pct_above_long'))}, "
            f"% up today {_p(r.get('pct_up'))} / down {_p(r.get('pct_down'))} "
            f"(as of {r.get('as_of') or '—'})"
            for r in breadth
        )
        lines.append("## Breadth & Advance/Decline\n" + body)

    return "\n\n".join(lines) if lines else "(no market data available)"


_SYSTEM = (
    "You are a buy-side market strategist writing a concise daily internal brief. "
    "You are given a cross-asset snapshot covering volatility & sentiment (VIX, AAII, "
    "put/call), index momentum (RSI, real trading-volume deviation vs the 20-day average), "
    "and market breadth (% of constituents above the 50/200-day moving averages, advancers "
    "vs decliners). Interpret the data as a whole: call out what is stretched (overbought/"
    "oversold RSI, breadth extremes, unusual volume), where signals agree or diverge across "
    "regions (US / China / HK / Japan / Taiwan / Korea), and the net risk posture. Be "
    "specific and quantitative, cite the numbers, and never invent data that is not present. "
    "Note that index volume figures are in each market's native unit and are comparable only "
    "as a per-row Δ vs 20d, not in absolute terms across rows."
)


def daily_summary(snapshot: dict, max_tokens: int = 4000) -> str | None:
    """One-shot Claude summary of the snapshot for the alert email. None on failure."""
    client = get_client()
    if client is None:
        return None
    context = format_snapshot(snapshot)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=_SYSTEM,
            messages=[{
                "role": "user",
                "content": (
                    "Today's market snapshot:\n\n" + context +
                    "\n\nWrite the daily brief: 1) a one-line headline risk read, "
                    "2) 3-6 bullet observations across volatility/sentiment, indices, and "
                    "breadth, 3) one line on the net posture. Keep it tight."
                ),
            }],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip() or None
    except Exception as e:
        return f"(AI summary unavailable: {type(e).__name__})"


def stream_chat(messages: list, snapshot: dict):
    """Yield text chunks for the dashboard sidebar chat.

    `messages` is the running [{role, content}] history (user/assistant turns).
    The current market snapshot is injected as cached system context so every
    question is answered against live data. Yields nothing if no client.
    """
    client = get_client()
    if client is None:
        return
    context = format_snapshot(snapshot)
    system = [
        {"type": "text", "text": _SYSTEM},
        {
            "type": "text",
            "text": "Current dashboard data the user is looking at:\n\n" + context,
            "cache_control": {"type": "ephemeral"},
        },
    ]
    with client.messages.stream(
        model=MODEL,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium"},
        system=system,
        messages=messages,
    ) as stream:
        for text in stream.text_stream:
            yield text
