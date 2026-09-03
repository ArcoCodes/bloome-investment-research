# Chart Rules

Use visuals expansively whenever structure materially lowers the reader's effort. A controlled table is better for exact lookup and prose is better for one or two isolated values, but do not leave decision-relevant time series, composition, comparison, causal transmission, scenarios, valuation, sensitivities, or monitoring frameworks as dense prose when a supported visual can express them clearly. More useful visuals are welcome; duplicated or decorative visuals are not.

## Selection

| Evidence structure | `visuals.json` type |
|---|---|
| Time series or inflection | `line` |
| One-period comparison or ranking | `bar` |
| Valuation, target-price, or scenario interval | `range` |
| Causal transmission or process | `flow` |
| Exact multi-metric comparison | `table` |
| Two-variable sensitivity | `matrix` |

Use only types defined in `visual-spec.md`. If a recurring evidence structure genuinely needs another form, add one reviewed React component and contract test; never improvise raw HTML/SVG in a report run.

## Data and editorial rules

- Every visual has a conclusion-led title, visible units or basis, accessible description, uncertainty where material, and exact accepted evidence IDs.
- Every quantitative mark has a nearby human-readable display label. Category and period labels must be concise, unambiguous, and collision-free; never rely on raw axis ticks such as auto-generated decimals to explain the chart.
- The sentence before `{{visual:key}}` states the claim the visual supports.
- Two compact complementary visuals may share one row by using consecutive markers. Their shared preceding sentence must explain why they should be read together; dense tables, matrices, and unrelated arguments remain full width.
- Observed, forecast, and scenario values remain distinguishable in labels and explanatory text.
- Keep dates, forecast periods, currencies, fiscal/calendar years, and accounting definitions comparable.
- Use a zero baseline for `bar` values.
- Put the main series first in `line`; all series share one honest scale and label sequence.
- Use `range` only when every item shares one basis.
- Keep `flow` in one reading direction with few nodes and explicit gates.
- Print every `matrix` value and mark the base row/column.
- Never invent interpolation, missing periods, normalized scores, or proportions.
- Do not rely on hover, remote assets, chart libraries, raw HTML, custom CSS, or JavaScript.

The controlled React renderer owns geometry, palette, typography, and mobile behavior. The `investment-visualization` skill owns selection, normalization, annotation text, uncertainty, sequence, and screenshot review.
