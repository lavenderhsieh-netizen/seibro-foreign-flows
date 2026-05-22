"""
data_01c_range_to_csv.py

One-shot: fetch a date range from SEIBro and write a clean CSV matching the
on-site Excel layout (same column order, no value transforms). Korean labels
by default; pass --english to get a translated version with identical layout.

Usage:
    uv run python scripts/data_01c_range_to_csv.py START END OUT_CSV [--english]
    # dates as YYYY-MM-DD; range is inclusive on both ends
"""

# %% imports
from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree as ET

import httpx

# %% constants
SEIBRO_URL = "https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp"
SEIBRO_REFERER = (
    "https://seibro.or.kr/websquare/control.jsp"
    "?w2xPath=/IPORTAL/user/ann/BIP_CNST02001V.xml&menuNo=861"
)

# Display order in the on-site .xls export
DISPLAY_MARKETS = ["유로시장", "미국", "일본", "홍콩", "중국", "기타국가", "총합계"]
API_BY_MARKET = {
    "미국": "AA1", "유로시장": "AA2", "일본": "AA3",
    "중국": "AA4", "홍콩": "AA5", "기타국가": "AA_ETC", "총합계": "AA_SUM",
}

# English translations — only labels change, layout & values are identical.
EN_MARKETS = {
    "유로시장": "Eurozone",
    "미국": "US",
    "일본": "Japan",
    "홍콩": "Hong Kong",
    "중국": "China",
    "기타국가": "Other",
    "총합계": "Total",
}
EN_INSTRUMENT = {"주식": "Equity", "채권": "Bond"}
EN_SIDE = {"매도": "Sell", "매수": "Buy"}
EN_REFDATE = "Reference date"
EN_CATEGORY = "Category"


# %% fetch
def fetch_range_xml(start_ymd: str, end_ymd: str) -> str:
    body = (
        '<reqParam action="frsecSetlCusdList" '
        'task="ksd.safe.bip.cnst.Ann.process.AnnSearchPTask">'
        '<PG_START value="1"/><PG_END value="1000"/>'
        f'<ic_start value="{start_ymd}"/><ic_end value="{end_ymd}"/>'
        f'<bDate1 value="{end_ymd}"/>'
        '<S_TYPE value="2"/><GIGAN value="1"/>'
        '<MENU_NO value="861"/>'
        '<CMM_BTN_ABBR_NM value="total_search,openall,print,hwp,word,pdf,seach,xls,"/>'
        '<W2XPATH value="/IPORTAL/user/ann/BIP_CNST02001V.xml"/>'
        '</reqParam>'
    )
    headers = {
        "Content-Type": 'application/xml; charset="UTF-8"',
        "submissionid": "submission_frsecSetlCusdList",
        "Referer": SEIBRO_REFERER,
        "User-Agent": "Mozilla/5.0",
    }
    r = httpx.post(SEIBRO_URL, content=body.encode("utf-8"),
                   headers=headers, timeout=30)
    r.raise_for_status()
    return r.content.decode("utf-8")


def parse_range_rows(xml_text: str) -> list[dict]:
    """Return one row per (date, instrument) with API field names."""
    root = ET.fromstring(xml_text)
    out = []
    for data_el in root.findall("data"):
        result = data_el.find("result")
        if result is None:
            continue
        kv = {child.tag: child.attrib.get("value", "") for child in result}
        out.append(kv)
    return out


def to_csv_lines(rows: list[dict], english: bool = False) -> list[str]:
    """
    Two-row header (markets, then sell/buy), then per-day rows.
    Layout mirrors the on-site Excel export, with one added trailing
    net-balance column:
      net balance = total buy - total sell
    Only the labels change when english=True.
    """
    refdate_h = EN_REFDATE if english else "조회기준일"
    category_h = EN_CATEGORY if english else "구분"
    net_balance_h = "Net balance" if english else "순매수"
    market_label = (lambda m: EN_MARKETS[m]) if english else (lambda m: m)
    sell_label = EN_SIDE["매도"] if english else "매도"
    buy_label = EN_SIDE["매수"] if english else "매수"
    instrument_label = (lambda i: EN_INSTRUMENT.get(i, i)) if english else (lambda i: i)

    h1 = [refdate_h, category_h]
    for m in DISPLAY_MARKETS:
        h1 += [market_label(m), ""]
    h1.append(net_balance_h)
    h2 = ["", ""]
    for _ in DISPLAY_MARKETS:
        h2 += [sell_label, buy_label]
    h2.append("")
    lines = [",".join(h1), ",".join(h2)]

    for kv in rows:
        ymd = kv.get("SETL_DT", "")
        date_str = f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}" if len(ymd) == 8 else ymd
        cells = [date_str, instrument_label(kv.get("SECN_TPCD_NM", ""))]
        for m in DISPLAY_MARKETS:
            code = API_BY_MARKET[m]
            for sfx in ("_1", "_2"):
                cells.append(kv.get(f"{code}{sfx}", ""))
        total_sell = kv.get("AA_SUM_1", "")
        total_buy = kv.get("AA_SUM_2", "")
        cells.append(_fmt_net_balance(total_sell, total_buy))
        lines.append(",".join(cells))
    return lines


def _fmt_net_balance(total_sell: str, total_buy: str) -> str:
    """Compute and format total buy minus total sell as a compact string."""
    try:
        sell = float(total_sell or 0)
        buy = float(total_buy or 0)
    except ValueError:
        return ""
    v = buy - sell
    if v == 0:
        return "0"
    if v == int(v):
        return str(int(v))
    s = f"{v:.10f}".rstrip("0").rstrip(".")
    if s.startswith("0.") and v < 1:
        s = s[1:]
    if s.startswith("-0.") and v > -1:
        s = "-" + s[2:]
    return s


# %% main
if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--english"]
    english = "--english" in sys.argv
    if len(args) != 3:
        print("usage: data_01c_range_to_csv.py START END OUT_CSV [--english]",
              file=sys.stderr)
        sys.exit(2)
    start, end, out_csv = args[0], args[1], Path(args[2])
    start_ymd = start.replace("-", "")
    end_ymd = end.replace("-", "")
    print(f"[seibro] range {start} -> {end}  (english={english}) ...")
    xml_text = fetch_range_xml(start_ymd, end_ymd)
    rows = parse_range_rows(xml_text)
    print(f"  parsed {len(rows)} rows ({len(rows) // 2} trading days)")
    if not rows:
        print("WARNING: no rows in response", file=sys.stderr)
    lines = to_csv_lines(rows, english=english)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    # UTF-8 BOM so Excel opens Korean/English headers cleanly either way.
    out_csv.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    print(f"  wrote {out_csv}  ({out_csv.stat().st_size} bytes)")
    print()
    print("\n".join(lines))
