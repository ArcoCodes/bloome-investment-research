# Data Layer Contract

## Dispatch boundary

Upstream Skills call only `scripts/market_data.py`. The dispatch layer reads `scripts/provider_config.json` for each dataset and tries only explicitly configured providers. It distinguishes two semantics:

- `fallback`: try equivalent-basis sources, such as earnings-date providers, in declared order and stop after success.
- `union`: merge complementary news, events, positioning, macro, or technical sources in parallel or sequence. Record `success` or `not_observed` for each source; never misrepresent a union as fallback.

A single-provider failure returns unavailable immediately. Multi-provider fallback must report every attempt.

Price chains may override the generic chain by market. Mainland China is fixed to `tencent → sina → yfinance`: Tencent and Sina are near-real-time web quote sources without a formal SLA, while yfinance is an explicitly delayed 30-minute fallback. Every cross-source switch must appear in `provider_attempts`; adapters must never mix in fields from another provider internally.

Legacy scripts remain compatibility entry points, but no new direct SDK imports may be added. Register any new data vendor adapter in the provider runtime, then add it to explicit configuration.

## Identity model

- `entity`: legal entity or issuer.
- `listing`: the entity's listing relationship at a venue, including MIC, local code, currency, and time zone.
- `security`: the specific instrument, including security type, ISIN, and provider symbols.

Without a regulatory or exchange entity identifier, identity must be marked `provisional`. A company display name cannot serve as a stable ID because names may change.

## Time and provenance

Every successful result contains `_meta`:

- `as_of`: historical-query cutoff; null for a current query.
- `retrieved_at`: actual local retrieval time.
- `point_in_time_safe`: whether the data can be proven public by the cutoff.
- `fallback_used` and `provider_attempts`: explicit fallback audit.
- `sources[].effective_at`: economic or trading period represented by the data.
- `sources[].published_at`: time the source first made the data public.
- `sources[].published_at_basis`: source-reported time, first-observed time, or unknown.
- `sources[].retrieved_at`: local retrieval time.
- `sources[].availability.status`: `delayed`, `real_time`, `end_of_day`, or `unknown`.
- `sources[].availability.declared_delay_seconds`: provider-declared nominal delay for that exchange.
- `sources[].availability.observed_staleness_seconds`: difference between retrieval and quote time; includes market-closed time and is not equivalent to network or feed delay.
- `sources[].availability.required_freshness_basis`: requires comparison with the latest completed exchange session rather than wall-clock age.
- `sources[].availability.wall_clock_age_alone_is_stale`: always false; a later completed session or explicit provider state is required for a stale verdict.
- `sources[].availability.delay_source_url`: source page supporting the stated delay.
- `sources[].adjustment`: unadjusted, forward/back adjusted, or not applicable.
- `sources[].fields`: every leaf field path supported by the source.
- `payload_sha256`: hash of the normalized business payload.

Data without `published_at` may be used for current research but cannot pass a strict historical query. Never present `retrieved_at` as an earnings publication time.

Price freshness follows [session-freshness.md](session-freshness.md). `observed_staleness_seconds` must never be compared with a fixed 24-hour cutoff. A quote is session-current when it represents the latest completed exchange session at retrieval time; closed-market hours, weekends, holidays, and pre-market time do not create missing sessions. Treat it as stale only when a later regular session has completed without a newer eligible observation or the provider explicitly reports stale/unavailable data.

Quote `effective_at` must use the exchange quote time returned by the provider, not retrieval time. When a provider declares only a nominal delay and does not report per-tick publication time, use local first-observed time as `published_at` and mark `published_at_basis` as `first_observed`. Yahoo Finance declares a 20-minute delay for `.T`, `.KS`, and `.KQ`; retain both `declared_delay_seconds=1200` and the supporting URL.

Tencent and Sina mainland-China quotes use `near_real_time_unofficial`: retain the quote time returned by the source and calculate `observed_staleness_seconds`, but leave `declared_delay_seconds` null and set `service_level=none`. Never claim exchange-authorized real-time status merely because updates were observed seconds apart during market hours.

## Derived technical-indicator basis

Technical indicators may consume only provider-normalized `Open/High/Low/Close/Volume`; indicator functions must not access vendors internally. Output retains the input source, adjustment basis, last input bar, and whether an incomplete daily bar was skipped.

- `OBV`: accumulate volume by closing-price direction. The series begins at zero on the first bar in the supplied window, so absolute values cannot be compared directly across different historical windows.
- `VWAP`: by default, a 20-trading-day bar-derived rolling VWAP using `(High + Low + Close) / 3`; it is not exchange tick-level or official session VWAP.
- `Bollinger`: 20-day closing-price mean plus/minus two population standard deviations, `ddof=0`.
- `KDJ`: `9,3,3`, with recursive smoothing after RSV using `alpha=1/3`; J is not clipped to 0–100.
- `MFI`: 14-day typical-price money flow whose direction is inferred from changes in typical price; it is not aggressive buy/sell or signed order flow.

For zero volume, missing fields, or insufficient samples, indicators return `status=unavailable` with a reason. Never fill values with guesses.

## Historical queries

Every successful current retrieval writes an immutable snapshot. Historical analysis must call:

```bash
python scripts/market_data.py as-of prices AAPL --as-of 2026-08-21T20:00:00Z
```

The query returns a snapshot only if every source has `published_at <= as_of`; otherwise it fails explicitly and must not fall back to current network data. Set `STOCK_DATA_SNAPSHOT_DIR` to choose the snapshot directory.
