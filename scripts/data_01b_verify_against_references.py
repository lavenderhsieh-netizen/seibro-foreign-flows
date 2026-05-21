"""
data_01b_verify_against_references.py

Cross-check our downloader output against the two reference .xls files Romain
shared:
  /home/.z/chat-uploads/01-04-2022-012a894b918a.xls  -> 2022-04-01
  /home/.z/chat-uploads/20-05-2026-16bb2b81a9eb.xls  -> 2026-05-20

The site exports HTML-disguised-as-xls in EUC-KR. We parse both the
reference and our own freshly-downloaded version into the same long-form
(date, instrument, market, side, amount) frame and compare cell by cell.
"""

# %% imports
from __future__ import annotations

import re
from pathlib import Path

import polars as pl

from data_01a_download_flows import (
    DISPLAY_MARKET_ORDER,
    download_day,
    parse_xml_to_long,
)


# %% reference parsing
def parse_reference_xls(path: Path) -> pl.DataFrame:
    """Parse the SEIBro HTML-XLS export into our long-form schema."""
    raw = path.read_bytes()
    # Try EUC-KR (default for SEIBro), fall back to CP949.
    for enc in ("euc-kr", "cp949", "utf-8"):
        try:
            html = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise RuntimeError(f"cannot decode {path}")

    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.DOTALL)
    parsed: list[list[str]] = []
    for row in rows:
        cells = re.findall(r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", row, re.DOTALL)
        parsed.append([re.sub(r"<[^>]+>", "", c).strip() for c in cells])

    # Header rows (2): top has the markets, sub has 매도/매수.
    # Data rows: ['YYYYMMDD', '주식'/'채권', 14 amounts...]
    data_rows = [r for r in parsed if len(r) == 16 and re.fullmatch(r"\d{8}", r[0])]
    if not data_rows:
        raise RuntimeError(f"no data rows found in {path}")

    out: list[dict] = []
    for r in data_rows:
        ymd = r[0]
        instrument = r[1]
        amounts = r[2:]
        # Cells are ordered: for each market in DISPLAY_MARKET_ORDER, sell then buy.
        for i, market in enumerate(DISPLAY_MARKET_ORDER):
            for j, side in enumerate(("sell", "buy")):
                v = amounts[i * 2 + j]
                amt = 0.0 if v == "" else float(v)
                out.append({
                    "obs_date": f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}",
                    "instrument": instrument,
                    "market": market,
                    "side": side,
                    "amount_musd": amt,
                })
    return pl.DataFrame(out).with_columns(pl.col("obs_date").str.to_date())


# %% diff
def diff_long(ref: pl.DataFrame, ours: pl.DataFrame, tol: float = 1e-6) -> pl.DataFrame:
    """Return rows where amounts disagree by more than tol."""
    key = ["obs_date", "instrument", "market", "side"]
    j = ref.rename({"amount_musd": "ref"}).join(
        ours.select([*key, "amount_musd"]).rename({"amount_musd": "ours"}),
        on=key, how="full", coalesce=True,
    )
    j = j.with_columns(
        (pl.col("ref").fill_null(0.0) - pl.col("ours").fill_null(0.0)).abs().alias("delta")
    )
    return j.filter(pl.col("delta") > tol).sort(key)


# %% main
REFERENCES = {
    "2022-04-01": Path("/home/.z/chat-uploads/01-04-2022-012a894b918a.xls"),
    "2026-05-20": Path("/home/.z/chat-uploads/20-05-2026-16bb2b81a9eb.xls"),
}


if __name__ == "__main__":
    pl.Config.set_tbl_rows(40)
    all_ok = True
    for obs, ref_path in REFERENCES.items():
        print(f"\n=== {obs}  (ref: {ref_path.name}) ===")
        ref_df = parse_reference_xls(ref_path)

        # Re-download fresh and use the parsed XML, not the parquet file,
        # so verification doesn't depend on filesystem state.
        out_paths = download_day(obs)
        xml_text = out_paths["xml"].read_text(encoding="utf-8")
        ours_df = parse_xml_to_long(xml_text)

        bad = diff_long(ref_df, ours_df)
        if bad.is_empty():
            print(f"  OK — {ref_df.height} cells match (max delta = 0)")
        else:
            all_ok = False
            print(f"  MISMATCH — {bad.height} cells differ:")
            print(bad)

    print("\n" + ("ALL OK" if all_ok else "DIFFERENCES FOUND"))
