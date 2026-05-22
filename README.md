# seibro-foreign-flows

Downloader for SEIBro (Korea Securities Depository) **외화증권예탁결제 / 전체내역** — daily foreign-securities settlement flows by market and instrument.

Source page: <https://seibro.or.kr/websquare/control.jsp?w2xPath=/IPORTAL/user/ann/BIP_CNST02001V.xml&menuNo=861>

## Data shape

For each day, two rows (주식 = equity, 채권 = bond), each with sell (매도) and buy (매수) amounts in **million USD** across 7 markets:

| API code | Market |
|----------|------------------|
| AA1 | 미국 (US) |
| AA2 | 유로시장 (Eurozone) |
| AA3 | 일본 (Japan) |
| AA4 | 중국 (China) |
| AA5 | 홍콩 (Hong Kong) |
| AA_ETC | 기타국가 (Other) |
| AA_SUM | 총합계 (Total) |

Suffix `_1` = sell (매도), `_2` = buy (매수). Reference unit on the page: **백만USD** (million USD).

## Scripts

| Script | Purpose |
|---|---|
| `scripts/data_01a_download_flows.py` | Fetch one day's data via the WebSquare XHR, parse to a tidy Polars frame, save bronze parquet + raw XML + HTML-XLS replica. |
| `scripts/data_01b_verify_against_references.py` | Diff the downloader output for 2026-05-20 and 2022-04-01 against the two reference `.xls` files shared by Romain. |
| `scripts/data_01c_range_to_csv.py` | One-shot: fetch a date range, write an Excel workbook matching the on-site layout plus a trailing net-balance column. `--english` flag translates labels. |
| `scripts/data_01d_morning_send.py` | Daily morning job: fetch, validate, **persist to bronze**, and email an English Excel workbook with a net-balance column to Angela. |
| `scripts/data_02a_build_manifest.py` | Build a manifest of bronze parquets and sync to DuckDB (`Data/seibro/seibro.duckdb`). |

## Scheduled daily email (Zo agent)

A Zo agent runs Mon–Fri 09:00 SGT and emails the English Excel workbook to `angelahsieh@gic.com.sg` using the connected Gmail account `quantgolem@gmail.com`. The agent:

1. Runs `data_01d_morning_send.py`.
2. Skips the send and pings Telegram if validation fails (no full 5-day window).
3. Otherwise calls `use_app_gmail` (`gmail-send-email`) with the workbook attached via a temporary download URL (Pipedream's Gmail action runs in their sandbox and cannot read local paths on Zo, hence the temporary public link).

Agent id: `5a0cbef5-fb2f-49e8-93f7-f5d6d5104b87`. Edit/list/delete via `list_agents` / `edit_agent` / `delete_agent`.

**Historical note** — the original agent used Microsoft Outlook (`rorozozo-ai@outlook.com`), but the Outlook integration was connected read-only, so every send was rejected at Pipedream's auth layer. Switched to Gmail on 2026-05-22.

## Storage layout

```
/home/workspace/Data/seibro/
├── manifest.parquet  (Latest versions per date)
├── seibro.duckdb     (Table: flows)
├── bronze/
│   ├── raw_xml/data_01a__flows__YYYY-MM-DD__<ts>__<git>.xml
│   ├── flows/   data_01a__flows__YYYY-MM-DD__<ts>__<git>.parquet
│   └── xls/     data_01a__flows__YYYY-MM-DD__<ts>__<git>.xls  (HTML replica matching the site export)
```

Data is **outside** git per repo conventions.

## How the API works

- Endpoint: `POST https://seibro.or.kr/websquare/engine/proworks/callServletService.jsp`
- Content-Type: `application/xml; charset="UTF-8"`
- Header `submissionid: submission_frsecSetlCusdList`
- Body (one day):
  ```xml
  <reqParam action="frsecSetlCusdList" task="ksd.safe.bip.cnst.Ann.process.AnnSearchPTask">
    <PG_START value="1"/><PG_END value="10"/>
    <ic_start value="YYYYMMDD"/><ic_end value="YYYYMMDD"/>
    <bDate1 value="YYYYMMDD"/>
    <S_TYPE value="2"/>   <!-- 1=결제건수 count, 2=결제금액 amount, 3=보관금액 -->
    <GIGAN value="1"/>    <!-- 1=일 day, 2=월, 3=분기, 4=년 -->
    <MENU_NO value="861"/>
    <CMM_BTN_ABBR_NM value="total_search,openall,print,hwp,word,pdf,seach,xls,"/>
    <W2XPATH value="/IPORTAL/user/ann/BIP_CNST02001V.xml"/>
  </reqParam>
  ```
- No auth or cookies required.

## Status

Two-date verification only — no scheduling, no incremental orchestrator yet.
