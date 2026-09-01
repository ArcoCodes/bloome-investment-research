---
name: stock-market-data
description: Unified, provenance-aware market data dispatcher for stock research. Resolves entity/listing/security identity and fetches research-grade current or delayed prices, fundamentals, filings, events, positioning, sentiment, macro and technical inputs through explicit provider chains, immutable snapshots and strict point-in-time queries. Use whenever an analysis needs any market number, ticker resolution, source audit, historical as-of data, valuation, volume, positioning, crowding or catalyst dates. Never use model memory for numbers.
---

# Stock Market Data (Unified Dispatch Layer)

Provide stock-analysis agents with unified data retrieval and evidence provenance. Most currently integrated sources require no API key, but every call must follow an explicit provider policy. Future official sources for China, Japan, and Korea must use the same dispatch layer.

## Required operating discipline

1. **Use the unified entry point for every new call**: `python scripts/market_data.py <command>`. Legacy scripts are compatibility entry points only.
2. **Resolve the security identity before analyzing a stock**: `python scripts/market_data.py resolve "<company name>"`. Never guess a ticker; consume `entity → listing → security`, and do not treat a ticker as a permanent identity.
3. **Script failures return JSON error declarations**. Treat the data item as unavailable, disclose the degraded result, and never fabricate a substitute. Reddit and StockTwits may be rate-limited (429/403); this is an expected degradation path.
4. Cache writes go to the system temporary directory and may be redirected with `SKILL_CACHE_DIR`. Scripts are repeatable and idempotent.
5. **Never fall back silently**: use only the order explicitly listed in `scripts/provider_config.json`, and inspect `_meta.provider_attempts` and `_meta.fallback_used`.
6. **Every field must be traceable**: read the reference period, publication time, retrieval time, adjustment basis, and field paths in `_meta.sources`. Data without `published_at` must not be used for strict historical analysis.
7. **Never backfill historical analysis with current network data**: use `market_data.py as-of` to read immutable snapshots that were public before the cutoff. If the query fails, declare the data unavailable.
8. Always describe current prices as “research-grade current/delayed quotes,” never as exchange-authorized real-time quotes or execution inputs. Check `quote_availability`. Disclose the nominal 20-minute delay for Japan/Korea yfinance quotes. Label Tencent/Sina mainland-China quotes as near-real-time web quotes with no SLA, and distinguish quote time from retrieval time.
9. Judge quote freshness by the latest completed exchange session, not elapsed natural hours. Read [references/session-freshness.md](references/session-freshness.md). Nights, weekends, holidays, and pre-market time do not by themselves make the latest completed close stale.

See [references/data-contract.md](references/data-contract.md) for the full contract.

## Unified entry point

```bash
python scripts/market_data.py resolve "Apple"
python scripts/market_data.py price AAPL MSFT
python scripts/market_data.py fundamentals AAPL
python scripts/market_data.py events AAPL
python scripts/market_data.py news AAPL
python scripts/market_data.py earnings-calendar AAPL
python scripts/market_data.py short-interest AAPL
python scripts/market_data.py positioning AAPL
python scripts/market_data.py technicals AAPL --period medium
python scripts/market_data.py macro --market us
python scripts/market_data.py providers prices --market CN-SH
python scripts/market_data.py as-of prices AAPL --as-of 2026-08-21T20:00:00Z
```

## Script catalog

Run every script with the interpreter (`python scripts/<name>.py`). Dependencies use inline PEP 723 declarations, which support automatic installation through `uv run`. Every script outputs JSON.

| Script                                 | Purpose                                                                                                                                                                                                | Example                                                                                    |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| `scripts/market_data.py`               | Unified dispatch for identity, prices, fundamentals, events, news, earnings dates, positioning, macro, technicals, and historical queries                                                              | `python scripts/market_data.py price NBIS`                                                 |
| `scripts/search_ticker.py`             | Compatibility entry point: company name → ticker (US/HK)                                                                                                                                               | `python scripts/search_ticker.py "nebius"`                                                 |
| `scripts/fetch_price.py`               | Compatibility entry point: research-grade regular-session price, volume ratio, and explicitly labeled yfinance pre/post-market quotes when available                                                   | `python scripts/fetch_price.py NBIS`                                                       |
| `scripts/fetch_fundamentals.py`        | Valuation and financial summary (PE/PS/margins/growth)                                                                                                                                                 | `python scripts/fetch_fundamentals.py NBIS`                                                |
| `scripts/fetch_events.py`              | Event scan: earnings, rating changes, and insider trades                                                                                                                                               | `python scripts/fetch_events.py NBIS`                                                      |
| `scripts/fetch_news.py`                | Company and macro news from Yahoo, Google News RSS, and Seeking Alpha                                                                                                                                  | `python scripts/fetch_news.py NBIS`                                                        |
| `scripts/fetch_earnings_calendar.py`   | Next earnings date, required for wait-state decisions                                                                                                                                                  | `python scripts/fetch_earnings_calendar.py NBIS`                                           |
| `scripts/check_earnings_released.py`   | Whether earnings have been released                                                                                                                                                                    | `python scripts/check_earnings_released.py NBIS`                                           |
| `scripts/fetch_earnings_report.py`     | Locate original earnings filings (SEC 10-Q/10-K)                                                                                                                                                       | `python scripts/fetch_earnings_report.py NBIS`                                             |
| `scripts/fetch_earnings_transcript.py` | Earnings-call transcript (8-K; 6-K foreign issuers are not yet supported)                                                                                                                              | `python scripts/fetch_earnings_transcript.py NBIS`                                         |
| `scripts/fetch_short_interest.py`      | US reported short-interest fields; HK same-day short-selling turnover and shortable eligibility. Not real-time borrow data or a complete net-short position                                            | `python scripts/fetch_short_interest.py NBIS`                                              |
| `scripts/fetch_options_flow.py`        | US options-volume Put/Call, FINRA off-exchange short-sale volume, and selected Form 4 data. Not signed options flow or dealer GEX                                                                      | `python scripts/fetch_options_flow.py NBIS`                                                |
| `scripts/fetch_13f.py`                 | A specified manager's quarterly 13F long positions from SEC EDGAR, not market-wide institutional ownership by ticker                                                                                   | `python scripts/fetch_13f.py --cik 0001423298`                                             |
| `scripts/fetch_reddit.py`              | Discussion activity on finance subreddits via RSS, with automatic rate-limit backoff                                                                                                                   | `python scripts/fetch_reddit.py NBIS --limit 5`                                            |
| `scripts/fetch_stocktwits.py`          | StockTwits message stream for retail sentiment                                                                                                                                                         | `python scripts/fetch_stocktwits.py NBIS`                                                  |
| `scripts/fetch_polymarket.py`          | Prediction-market implied probabilities for macro/events                                                                                                                                               | `python scripts/fetch_polymarket.py "fed rate"`                                            |
| `scripts/fetch_macro.py`               | Macro snapshot: VIX, equity-index futures, oil, and US Treasuries                                                                                                                                      | `python scripts/fetch_macro.py`                                                            |
| `scripts/analyze_technicals.py`        | Technical-research inputs: trend, regime, multi-timeframe structure, OBV, 20-day rolling VWAP, Bollinger Bands, KDJ, MFI, volume-price effort/result, and bar metadata                                 | `python scripts/analyze_technicals.py NBIS --period medium`                                |
| `scripts/generate_chart.py`            | Weekly, daily, or intraday research chart plus a same-stem component-data sidecar: candlesticks, volume, EMA8/21/55, support/resistance, and swing structure; research mode excludes trade annotations | `python scripts/generate_chart.py --symbol NBIS --mode research --interval 1wk --days 104` |
| `scripts/analyze_zones.py`             | Trading-layer entry zones and backtest (win rate/odds/Kelly); must not be used by research-layer `stock-technical-structure`                                                                           | `python scripts/analyze_zones.py --symbol NBIS --direction long`                           |

Utility modules (not called directly): `provider_runtime.py` (explicit dispatch), `data_contract.py` (provenance contract), `security_master.py` (three-layer identity), `snapshot_store.py` (immutable snapshots), `cache_utils.py` (short-term cache), `resample_utils.py` (candlestick resampling), and `technical_indicators.py` (deterministic calculations that consume only normalized OHLCV and never access a data source directly).

## Recommended combinations by analytical dimension

- **Fundamentals**: `market_data.py fundamentals` + `market_data.py events` (insiders)
- **News**: `market_data.py news` + `market_data.py events` + `market_data.py earnings-calendar`
- **Earnings period**: `check_earnings_released` + `fetch_earnings_report` + `fetch_earnings_transcript`
- **Sentiment/crowding inputs**: `market_data.py short-interest` + `market_data.py positioning`; Reddit, StockTwits, and 13F remain compatibility entry points. `stock-sentiment-positioning` owns basis distinctions, official-source supplementation, and conclusions.
- **Technicals**: `market_data.py price` + `market_data.py technicals` + weekly and daily `generate_chart.py --mode research` views; trading-layer `analyze_zones` remains a compatibility entry point.
- **Macro backdrop**: `market_data.py macro` + `fetch_polymarket`

## Sources and acknowledgments

The scripts originated from the novark market-data toolkit. `fetch_reddit`, `fetch_stocktwits`, and `fetch_polymarket` were adapted from TauricResearch/TradingAgents (Apache-2.0).
