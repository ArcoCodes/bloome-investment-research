---
name: market-data
description: Answer follow-up market data questions with the gilddataMarketData MCP tools (GildData / 恒生聚源) after an investment research report is delivered, or for standalone quick lookups in conversation. Covers quotes, valuation and financial snapshots, macro and industry time series, stock/fund screening, A-share announcements, news, and corporate registry data. Triggers: 现在股价多少, 最新估值, 这个指标最近怎么走, 查一下公告, 帮我筛选, current price, latest PE, macro series. Do NOT use while an investment-research workflow run is in progress — workflow retrieval stays on research_search.
---

# Market Data Follow-up (GildData)

Use the `gilddataMarketData` MCP server to answer market data questions the user asks after a research report is delivered, or as standalone quick lookups. All tools accept natural-language questions; ask in the user's language.

## Tool selection

High-frequency tools:

- **FinQuery** — structured snapshot for one entity (stock/bond/fund/index/industry/concept): profile, financials, quote, valuation. Default for "现在多少钱 / 最新PE / 这季度营收".
- **MacroIndustryData** — macro and industry economic time series (GDP, CPI, semiconductor sales, regional and global indicators). Default for "这个指标最近怎么走".
- **AnnouncementData** — A-share announcement full-text retrieval (financial reports, major events, governance, dividends, related transactions, restructuring).

Others as the question demands:

- **SmartStockSelection / SmartFundSelection / SmartFundManagerSelection** — multi-condition candidate screening (returns ranked candidate lists, not single-entity detail).
- **FinancialResearchReport** — sell-side narrative retrieval (analyst views, industry structure, historical events).
- **NewsDataQuery** — news and sentiment retrieval for recent developments and trending events.
- **IcEnterpriseDataQuery** — corporate registry and compliance data for listed and unlisted companies (registration, changes, executives, risk).

## Answer rules

- Attribute every figure: `数据来源：恒生聚源（GildData） · as-of <date>`. Use the as-of/period date returned by the tool; if the tool response carries no date, say so instead of inventing one.
- Report returned values verbatim. Compute derived numbers (differences, percentages, changes) with a script, never mentally.
- If the credential is missing or the gateway is unreachable, report the exact error status and stop; do not fill in from stale model knowledge.

## Boundary with the research workflow

- This skill serves conversation-time lookups only. GildData results are not research evidence: never write them into `evidence.json`, workspace artifacts, or a delivered `report.html`.
- Do not reopen or start a research workspace to answer a follow-up, and do not call `research_search`/`research_get_chunk` here; those belong to the `investment-research` workflow and its sell/primary corpora.
- GildData calls use their own token (`GILDDATA_API_TOKEN` or `~/.bloome/gilddata-api-token`) and consume no Bloome research credit.
- If a follow-up actually requires new evidence-grade research (new claims, new report content), tell the user it needs a new `investment-research` run instead of answering from GildData alone.
