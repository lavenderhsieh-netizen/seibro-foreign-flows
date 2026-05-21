"""
data_01a_download_flows.py

Download SEIBro 외화증권예탁결제 전체내역 (foreign-securities settlement
flows) for a single trading day.

Endpoint discovered by sniffing the WebSquare SPA at
  https://seibro.or.kr/websquare/control.jsp?w2xPath=/IPORTAL/user/ann/BIP_CNST02001V.xml&menuNo=861

The page makes a `POST callServletService.jsp` XHR with an XML body. No auth.

Outputs (under /home/workspace/Data/seibro/bronze/):
  - raw_xml/  data_01a__flows__YYYY-MM-DD__<ts>__<git>.xml  (server response)
  - flows/    data_01a__flows__YYYY-MM-DD__<ts>__<git>.parquet  (long-form tidy)
  - xls/      data_01a__flows__YYYY-MM-DD__<ts>__<git>.xls  (HTML replica)

Each downstream piece is independent so a partial failure doesn't lose the raw.
"""

# %% imports
from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx
import polars as pl

# %% constants
SEIBRO_URL = "https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp"
SEIBRO_REFERER = (
    "https://seibro.or.kr/websquare/control.jsp"
    "?w2xPath=/IPORTAL/user/ann/BIP_CNST02001V.xml&menuNo=861"
)

# API code -> Korean market name. Order here is the order the API returns.
MARKET_MAP = {
    "AA1": "미국",
    "AA2": "유로시장",
    "AA3": "일본",
    "AA4": "중국",
    "AA5": "홍콩",
    "AA_ETC": "기타국가",
    "AA_SUM": "총합계",
}

# Display column order in the SEIBro Excel export.
DISPLAY_MARKET_ORDER = ["유로시장", "미국", "일본", "홍콩", "중국", "기타국가", "총합계"]

BRONZE_ROOT = Path("/home/workspace/Data/seibro/bronze")


# %% functions
def _git_hash() -> str:
    """Short git hash of THIS file's repo, or 'nogit' if outside a repo."""
    try:
        out = subprocess.run(
            ["git", "-C", str(Path(__file__).parent), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        return out.stdout.strip() or "nogit"
    except Exception:
        return "nogit"


def _ymd_compact(d: dt.date | str) -> str:
    """Accept date or 'YYYY-MM-DD' / 'YYYYMMDD' and return 'YYYYMMDD'."""
    if isinstance(d, dt.date):
        return d.strftime("%Y%m%d")
    s = str(d).replace("-", "").replace("/", "")
    if not re.fullmatch(r"\d{8}", s):
        raise ValueError(f"bad date format: {d!r}")
    return s


def _ymd_dash(d: dt.date | str) -> str:
    s = _ymd_compact(d)
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def build_request_body(obs_date: str) -> str:
    """Build the WebSquare XML body for a single-day amount (결제금액) query."""
    ymd = _ymd_compact(obs_date)
    return (
        '<reqParam action="frsecSetlCusdList" '
        'task="ksd.safe.bip.cnst.Ann.process.AnnSearchPTask">'
        '<PG_START value="1"/><PG_END value="10"/>'
        f'<ic_start value="{ymd}"/><ic_end value="{ymd}"/>'
        f'<bDate1 value="{ymd}"/>'
        '<S_TYPE value="2"/>'      # 1=count, 2=amount, 3=holdings
        '<GIGAN value="1"/>'       # 1=day, 2=month, 3=qtr, 4=year
        '<MENU_NO value="861"/>'
        '<CMM_BTN_ABBR_NM value="total_search,openall,print,hwp,word,pdf,seach,xls,"/>'
        '<W2XPATH value="/IPORTAL/user/ann/BIP_CNST02001V.xml"/>'
        '</reqParam>'
    )


def fetch_day_xml(obs_date: str, timeout: float = 30.0) -> str:
    """POST the XHR and return the UTF-8 XML response text."""
    body = build_request_body(obs_date)
    headers = {
        "Content-Type": 'application/xml; charset="UTF-8"',
        "submissionid": "submission_frsecSetlCusdList",
        "Referer": SEIBRO_REFERER,
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
    }
    r = httpx.post(SEIBRO_URL, content=body.encode("utf-8"),
                   headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.content.decode("utf-8")


def parse_xml_to_long(xml_text: str) -> pl.DataFrame:
    """
    Parse the WebSquare <vector> response into a long-form Polars frame:
      obs_date | instrument | market | side | amount_musd

    side ∈ {"sell","buy"} from suffix _1 / _2.
    """
    root = ET.fromstring(xml_text)
    rows: list[dict] = []
    for data_el in root.findall("data"):
        result = data_el.find("result")
        if result is None:
            continue
        # Each child is <SOMETHING value="..."/>
        kv = {child.tag: child.attrib.get("value", "") for child in result}
        obs_date = kv.get("SETL_DT", "")
        instrument = kv.get("SECN_TPCD_NM", "")
        for api_code, market in MARKET_MAP.items():
            for suffix, side in (("_1", "sell"), ("_2", "buy")):
                key = f"{api_code}{suffix}"
                if key in kv:
                    raw = kv[key].strip()
                    if raw == "":
                        amt = 0.0
                    else:
                        # Seibro returns e.g. ".1" for 0.1 — float handles it.
                        amt = float(raw)
                    rows.append({
                        "obs_date": _ymd_dash(obs_date) if obs_date else None,
                        "instrument": instrument,
                        "market": market,
                        "side": side,
                        "amount_musd": amt,
                    })
    df = pl.DataFrame(rows).with_columns(
        pl.col("obs_date").str.to_date(),
    )
    return df


def long_to_wide_display(df: pl.DataFrame) -> pl.DataFrame:
    """
    Pivot to the same wide layout as the .xls export the site produces:
      obs_date | instrument | <market>_<side> ...
    in the display order (Eurozone first, US, Japan, HK, China, Other, Total),
    with sell before buy.
    """
    cols = [f"{m}_{s}" for m in DISPLAY_MARKET_ORDER for s in ("sell", "buy")]
    wide = (
        df.with_columns(
            (pl.col("market") + "_" + pl.col("side")).alias("col"),
        )
        .pivot(values="amount_musd", index=["obs_date", "instrument"], on="col")
        .select(["obs_date", "instrument", *cols])
    )
    # Preserve instrument order: 주식 then 채권
    order = {"주식": 0, "채권": 1}
    wide = wide.with_columns(
        pl.col("instrument").replace_strict(order, return_dtype=pl.Int8).alias("_o")
    ).sort("_o").drop("_o")
    return wide


def render_html_xls(wide: pl.DataFrame) -> str:
    """Render an HTML table that mimics the SEIBro .xls export (EUC-KR)."""
    # Two-row header just like the site
    head_top = (
        "<tr><th rowspan='2'>구분</th><th rowspan='2'>구분</th>"
        + "".join(f"<th colspan='2'>{m}</th>" for m in DISPLAY_MARKET_ORDER)
        + "</tr>"
    )
    head_sub = "<tr>" + "".join("<th>매도</th><th>매수</th>" for _ in DISPLAY_MARKET_ORDER) + "</tr>"
    body_rows = []
    for row in wide.iter_rows(named=True):
        ymd = row["obs_date"].strftime("%Y%m%d") if row["obs_date"] else ""
        tds = [f"<td>{ymd}</td>", f"<td>{row['instrument']}</td>"]
        for m in DISPLAY_MARKET_ORDER:
            for s in ("sell", "buy"):
                v = row[f"{m}_{s}"]
                tds.append(f"<td>{_fmt(v)}</td>")
        body_rows.append("<tr>" + "".join(tds) + "</tr>")
    html = (
        "<meta http-equiv='Content-Type' content='application/vnd.ms-excel; "
        "charset=euc-kr'></meta>"
        "<table border='1'>"
        + head_top + head_sub + "".join(body_rows)
        + "</table>"
    )
    return html


def _fmt(v: float) -> str:
    """Match the site's quirky formatting: '.1', '0', '1438.94', '1749'."""
    if v is None:
        return ""
    if v == 0:
        return "0"
    if v == int(v):
        return str(int(v))
    # Drop trailing zeros after the decimal
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    # Strip leading zero on values like 0.1 -> .1 (matches the site export)
    if s.startswith("0.") and v < 1:
        s = s[1:]
    if s.startswith("-0.") and v > -1:
        s = "-" + s[2:]
    return s


# %% main flow per day
def download_day(obs_date: str | dt.date) -> dict[str, Path]:
    """
    Fetch one day's data and persist the three bronze artifacts.
    Returns a dict of {kind: path}.
    """
    obs = _ymd_dash(obs_date)
    obs_compact = _ymd_compact(obs_date)
    ts = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    gh = _git_hash()
    tag = f"data_01a__flows__{obs}__{ts}__{gh}"

    xml_dir = BRONZE_ROOT / "raw_xml"
    pq_dir = BRONZE_ROOT / "flows"
    xls_dir = BRONZE_ROOT / "xls"
    for d in (xml_dir, pq_dir, xls_dir):
        d.mkdir(parents=True, exist_ok=True)

    print(f"[seibro] fetching {obs} (compact={obs_compact}) ...")
    xml_text = fetch_day_xml(obs)
    xml_path = xml_dir / f"{tag}.xml"
    xml_path.write_text(xml_text, encoding="utf-8")
    print(f"  -> raw xml {xml_path.name} ({len(xml_text)} chars)")

    long_df = parse_xml_to_long(xml_text)
    if long_df.is_empty():
        raise RuntimeError(f"no rows parsed for {obs}; raw saved at {xml_path}")
    long_df = long_df.with_columns(
        pl.lit(gh).alias("code_git_hash"),
        pl.lit(dt.datetime.now(dt.UTC).replace(tzinfo=None)).alias("run_ts"),
    )
    pq_path = pq_dir / f"{tag}.parquet"
    long_df.write_parquet(pq_path)
    print(f"  -> parquet {pq_path.name}  ({long_df.height} rows)")

    wide = long_to_wide_display(long_df.drop(["code_git_hash", "run_ts"]))
    html = render_html_xls(wide)
    xls_path = xls_dir / f"{tag}.xls"
    xls_path.write_bytes(html.encode("euc-kr", errors="replace"))
    print(f"  -> xls     {xls_path.name}  ({xls_path.stat().st_size} bytes)")

    return {"xml": xml_path, "parquet": pq_path, "xls": xls_path}


# %% runnable example
if __name__ == "__main__":
    targets = sys.argv[1:] or ["2026-05-20", "2022-04-01"]
    for d in targets:
        out = download_day(d)
        df = pl.read_parquet(out["parquet"])
        print(df.sort(["instrument", "market", "side"]).to_pandas().to_string(index=False))
        print()
