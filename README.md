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

## Storage layout

```
/home/workspace/Data/seibro/
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
