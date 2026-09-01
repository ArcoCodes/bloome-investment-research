# Exchange-Session Freshness

Judge price freshness against the latest completed exchange session, never against a fixed number of elapsed wall-clock hours.

## Decision Rule

A regular-session close is current for research when it belongs to the most recent regular trading session that has completed at the retrieval cutoff. Time outside exchange sessions does not age the close:

- Friday's U.S. close remains the latest completed session throughout Saturday, Sunday, and Monday pre-market unless Monday is an exchange holiday.
- The close before an exchange holiday remains current until the next regular session produces a newer eligible observation.
- Before the open, retain the latest completed close as the regular-session reference and label any pre-market quote separately.
- During an open session, use the research-grade current or delayed quote when available, but do not mix an incomplete intraday bar into daily close-based backtests.
- After the close, require the newly completed session once the provider's declared publication delay has elapsed.

Determine the latest completed session from exchange-calendar or provider session metadata. If neither is available, mark session freshness as uncertain; do not declare a quote stale merely because more than 24 natural hours elapsed.

## Stale Conditions

Treat a quote as stale only when at least one regular exchange session completed after its effective time and the provider still has no eligible newer observation, or when the provider explicitly reports stale/unavailable data. Record the exchange time zone, session date, retrieval time, provider delay, and reason.

`observed_staleness_seconds` includes nights, weekends, and holidays. It is diagnostic wall-clock age, not the freshness verdict and not feed latency.

## Example

At a Sunday, 2026-08-23 cutoff, the U.S. Friday, 2026-08-21 regular-session close is the latest completed U.S. equity session and therefore session-current for research. It must not fail solely because more than 24 hours elapsed. It remains a research-grade close, not an exchange-authorized live execution quote.
