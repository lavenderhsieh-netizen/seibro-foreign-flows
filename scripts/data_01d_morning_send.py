"""
data_01d_morning_send.py

Daily morning script: fetch the most recent 5 Korean trading days from
SEIBro, validate, and write an English CSV ready to be attached to an
email by the calling Zo agent.

Strategy:
- Pull a 10-calendar-day window ending today.
- If <5 unique trading days come back (Korean holiday week, KSD lag),
  widen the window to 15, 21, then 30 days.
- Keep only the 5 most recent trading days.
- Validate: exactly 5 distinct dates AND each date has both 주식 (Equity)
  and 채권 (Bond) rows (10 rows total).
- Exit non-zero with a clear stderr message on failure so the caller
  does NOT send the email.
- On success, print a JSON summary to stdout for the agent to read.

Outputs:
    CSV at /home/workspace/Documents/seibro_morning_last5bd_<end>.csv
    JSON summary on stdout
"""

# %% imports
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

# Make sibling module importable when run as a script
sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_01c_range_to_csv import (  # noqa: E402
    DISPLAY_MARKETS,
    fetch_range_xml,
    parse_range_rows,
    to_csv_lines,
)

# %% constants
TARGET_DAYS = 5            # business days required
WIDEN_SCHEDULE = [10, 15, 21, 30]   # widening calendar-day windows
RETRY_DELAY_SECONDS = 30   # short sleep between widening passes
OUT_DIR = Path("/home/workspace/Documents")


# %% helpers
def _today_sgt() -> dt.date:
    """Today in Singapore time (UTC+8)."""
    return (dt.datetime.now(dt.UTC) + dt.timedelta(hours=8)).date()


def _attempt_fetch(end: dt.date, window_days: int) -> list[dict]:
    start = end - dt.timedelta(days=window_days)
    start_ymd = start.strftime("%Y%m%d")
    end_ymd = end.strftime("%Y%m%d")
    print(f"  [attempt] window {window_days}d  {start_ymd}..{end_ymd}",
          file=sys.stderr)
    xml_text = fetch_range_xml(start_ymd, end_ymd)
    rows = parse_range_rows(xml_text)
    return rows


def fetch_last_n_business_days(n: int = TARGET_DAYS,
                               end: dt.date | None = None) -> list[dict]:
    """
    Widen the window until we have >=n unique trading days, then slice
    to the n most recent dates.

    Raises RuntimeError if we still don't have n days after the widest
    window.
    """
    end = end or _today_sgt()
    last_rows: list[dict] = []
    last_unique: set[str] = set()
    for i, w in enumerate(WIDEN_SCHEDULE):
        rows = _attempt_fetch(end, w)
        unique_dates = {r.get("SETL_DT", "") for r in rows if r.get("SETL_DT")}
        print(f"  [attempt] window {w}d returned {len(rows)} rows  "
              f"({len(unique_dates)} unique dates)", file=sys.stderr)
        last_rows, last_unique = rows, unique_dates
        if len(unique_dates) >= n:
            break
        if i < len(WIDEN_SCHEDULE) - 1:
            print(f"  [retry] only {len(unique_dates)}<{n} days, "
                  f"sleeping {RETRY_DELAY_SECONDS}s then widening",
                  file=sys.stderr)
            time.sleep(RETRY_DELAY_SECONDS)

    if len(last_unique) < n:
        raise RuntimeError(
            f"only {len(last_unique)} trading days available after widening "
            f"to {WIDEN_SCHEDULE[-1]} calendar days "
            f"(end={end.isoformat()}); KSD likely behind on publish"
        )

    # Keep the n most recent dates
    kept_dates = sorted(last_unique, reverse=True)[:n]
    kept_set = set(kept_dates)
    sliced = [r for r in last_rows if r.get("SETL_DT") in kept_set]
    # Sort by date ascending then by NUM (API ordering = 주식 first, 채권 second)
    sliced.sort(key=lambda r: (r.get("SETL_DT", ""),
                               int(r.get("NUM", "0") or 0)))
    return sliced


def validate(rows: list[dict], n: int = TARGET_DAYS) -> None:
    """Hard checks. Raises ValueError on any violation."""
    if not rows:
        raise ValueError("validation: no rows")
    dates = sorted({r.get("SETL_DT", "") for r in rows})
    if len(dates) != n:
        raise ValueError(
            f"validation: expected {n} distinct dates, got {len(dates)}: {dates}"
        )
    expected_instruments = {"주식", "채권"}
    for d in dates:
        instr = {r.get("SECN_TPCD_NM", "") for r in rows
                 if r.get("SETL_DT") == d}
        if instr != expected_instruments:
            raise ValueError(
                f"validation: date {d} missing instruments; got {instr}"
            )
    expected_rows = n * 2
    if len(rows) != expected_rows:
        raise ValueError(
            f"validation: expected {expected_rows} rows ({n} days x 2 "
            f"instruments), got {len(rows)}"
        )
    # Every amount field must be a number (string from API; we parse)
    amount_keys = [f"{code}{sfx}"
                   for code in ("AA1", "AA2", "AA3", "AA4", "AA5",
                                "AA_ETC", "AA_SUM")
                   for sfx in ("_1", "_2")]
    for r in rows:
        for k in amount_keys:
            v = r.get(k, "")
            if v == "":
                continue
            try:
                float(v)
            except ValueError as e:
                raise ValueError(
                    f"validation: non-numeric {k}={v!r} on {r.get('SETL_DT')}"
                ) from e


# %% main
if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    end = _today_sgt()
    print(f"[morning-send] target: last {TARGET_DAYS} trading days "
          f"ending {end.isoformat()} (SGT)", file=sys.stderr)

    rows = fetch_last_n_business_days(TARGET_DAYS, end=end)
    validate(rows, TARGET_DAYS)
    print(f"[morning-send] validation OK: {len(rows)} rows", file=sys.stderr)

    dates = sorted({r["SETL_DT"] for r in rows})
    first_dash = f"{dates[0][:4]}-{dates[0][4:6]}-{dates[0][6:8]}"
    last_dash = f"{dates[-1][:4]}-{dates[-1][4:6]}-{dates[-1][6:8]}"

    csv_path = OUT_DIR / f"seibro_morning_last5bd_{last_dash}.csv"
    lines = to_csv_lines(rows, english=True)
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    print(f"[morning-send] wrote {csv_path}  ({csv_path.stat().st_size} B)",
          file=sys.stderr)

    summary = {
        "ok": True,
        "csv_path": str(csv_path),
        "trading_days": [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in dates],
        "first_date": first_dash,
        "last_date": last_dash,
        "row_count": len(rows),
        "generated_at_utc": dt.datetime.now(dt.UTC).isoformat(),
    }
    print(json.dumps(summary, ensure_ascii=False))
