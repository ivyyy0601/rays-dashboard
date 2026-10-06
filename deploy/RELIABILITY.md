# Daily pipeline reliability and remaining verification limits

Production runs at 19:00 America/New_York (prefetch) and 20:00 (validation,
bounded retries, publish, email attempt). Timers follow daylight saving time.
Downloads use batches of 50, four workers, and at most three attempts per batch.
Incomplete/missing bars are not zero volume. Date checks use exchange calendars.
The complete snapshot must contain all 12 configured indices exactly once.

GitHub's `Independent daily pipeline health` workflow checks the server outside
the download process at 01:47 and 02:47 UTC. It fails for unreachable servers,
missing daily runs, invalid index rows, or failed/unknown email attempts. Enable
GitHub Actions failure notifications for this repository. GitHub scheduling can
itself be delayed; this is not an independent commercial uptime SLA. Existing
SERVER_IP and SSH_PRIVATE_KEY secrets are reused. The independent health workflow
pins the existing server's public Ed25519 host key; update after a verified rotation.

Run checks without sending email:

    PYTHONPATH=. python -m unittest discover -s tests -v
    python pipeline_health.py

The read-only health check is expected to FAIL while unresolved data problems
remain. Do not remove constituents just to achieve full download coverage.

## Not certified / not yet solved

- ETF holdings and Wikipedia are proxies, not verified daily official membership.
- Taiwan's legacy list includes ETFs; that row remains explicitly invalid.
- Russell has 20 unresolved ticker/day records and TOPIX has 3 in the Oct 5 test.
- A Yahoo index-volume comparison is NOT independent source reconciliation and
  can compare different universes. Repeated Yahoo downloads are not independent.
- Official membership evidence with effective dates and independently sourced
  per-security prices/volume are still required before claiming full correctness.
  FTSE Russell daily constituent services are licensed; access is not supplied
  by this repository. No paid subscription was purchased during this repair.
- Sum(close * daily share volume) is an ESTIMATED trading-value measure, not the
  sum of actual transaction prices * sizes. USD conversion is another estimate.
- Email worker completion is not proof of inbox delivery. An ambiguous SMTP
  attempt is not retried automatically to avoid duplicate mail.

Official references:
https://www.lseg.com/en/ftse-russell/index-resources/indices-subscribe-data
https://twse-regulation.twse.com.tw/ENG/EN/law/DAT08.aspx?FLCODE=FL047579

An Oct 5 single-session benchmark (saved lists, no history downloads) took
643.16 seconds: 6863 / 6891 distinct ticker-session records were available.
This is one observation, not a future execution-time or correctness guarantee.
