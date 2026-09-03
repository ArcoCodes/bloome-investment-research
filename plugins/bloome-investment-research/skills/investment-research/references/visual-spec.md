# Controlled Visual Specifications

`visuals.json` is the only visual data input to the React report renderer. It keeps chart data, evidence links, and presentation intent separate from report prose. A visual must answer one investment question faster than prose; otherwise use prose. Never put raw HTML, SVG, CSS, JavaScript, event handlers, or internal production notes in the reader-facing report.

Use this top-level shape even when no visual is useful:

```json
{ "visuals": [] }
```

Each visual requires:

```json
{
  "key": "qualified-capacity-gap",
  "type": "line",
  "title": "Qualified capacity trails demand through 2028",
  "deck": "Optional context for basis and period",
  "aria_label": "Accessible description of conclusion and encoding",
  "uncertainty": "What could change the interpretation",
  "evidence_ids": ["chunk-12"]
}
```

- `key` is a descriptive lowercase slug, unique within the report. Do not use `V01`-style IDs.
- `title` states the conclusion.
- `evidence_ids` contains exact accepted `evidence.json` `id` or `chunk_id` values.
- `aria_label` is required. `deck` is recommended, and uncertainty is required when scenarios, forecasts, normalization, or conflicting evidence affect the conclusion.
- Quantitative `bar`, `line`, and `range` visuals require `unit`. Every bar/range item requires a concise `label` and human-readable `display`; every line series requires `name`, and every point requires both `label` and `display`. The renderer rejects missing labels rather than guessing axis text or exposing auto-generated decimal ticks.
- Place the visual in `report.md` on its own line: `{{visual:qualified-capacity-gap}}`.
- Every specification must have one marker, and every marker must resolve to one specification.
- To create one professional two-chart row, place two compatible markers consecutively with no intervening prose. The renderer creates a two-column desktop grid and stacks it on narrow screens. Pair only compact, complementary charts; keep dense tables and matrices full width.

## Supported types

The renderer supports `bar`, `line`, `range`, `flow`, `table`, or `matrix`.

### `bar`

One-period comparison or ranking. Bars use a zero baseline.

```json
{
  "type": "bar",
  "unit": "%",
  "items": [
    { "label": "ABF", "value": 34, "display": "34%", "highlight": true },
    { "label": "CCL", "value": 18, "display": "18%" }
  ]
}
```

### `line`

Time series with a shared set of labels. Put the main series first. Keep every series on a comparable scale and distinguish forecasts in the title, deck, or point display.

```json
{
  "type": "line",
  "unit": "%",
  "series": [
    { "name": "Demand", "values": [
      { "label": "2026", "value": 100, "display": "100" },
      { "label": "2027E", "value": 134, "display": "134" }
    ]}
  ]
}
```

### `range`

Valuation, scenario, or estimate ranges on one common basis.

```json
{
  "type": "range",
  "unit": "USD",
  "items": [
    { "label": "Base security", "low": 8, "base": 13, "high": 18, "current": 10, "display": "$8–18" }
  ]
}
```

### `flow`

A restrained causal bridge in one reading direction. Use three to five short nodes; put qualifications in `uncertainty`, not extra nodes. Do not use a flow for chronology, evidence lists, or prose broken into boxes.

```json
{
  "type": "flow",
  "nodes": [
    { "label": "Demand", "detail": "Unit content rises" },
    { "label": "Qualified supply", "detail": "Yield gates output" },
    { "label": "EPS", "detail": "Price reaches margin", "highlight": true }
  ]
}
```

### `table`

Exact lookup across comparable measures. Keep cells to one concise claim. The renderer uses a table on desktop and labeled row cards on narrow screens.

```json
{
  "type": "table",
  "columns": ["Company", "2028E P/E", "Key boundary"],
  "rows": [["A", "11.0x", "Yield"], ["B", "18.2x", "Valuation"]]
}
```

### `matrix`

Two-variable sensitivity with printed values.

```json
{
  "type": "matrix",
  "corner": "Price / volume",
  "columns": ["-10%", "Base", "+10%"],
  "rows": [
    { "label": "Low", "values": ["8", "10", "12"] },
    { "label": "Base", "values": ["11", "13", "15"] }
  ],
  "base_row": 1,
  "base_column": 1
}
```

Prefer `line` for time, `bar` for one-period comparison, `range` for valuation, `table` or `matrix` for qualitative comparisons, and `flow` only for a short causal bridge. Interview fragments and isolated observations belong in cited prose, not pseudo-quantitative charts. Use a prose section instead of inventing unsupported geometry. Add a new React component and contract test only when a recurring evidence structure cannot be expressed honestly by these types.
