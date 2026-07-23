# Report Structure and Visual Contract

The approved outline is binding. Do not invent a different report structure during final HTML rendering.

## Outline format

Give every substantive section a stable ID and use the same heading verbatim in its chapter:

```markdown
# S01 Executive judgment
Purpose: State the decision and why it wins.
Claims: C1, C4
Visual: V01 | comparison | Risk–reward comparison | chart

# S02 Causal mechanism
Purpose: Explain the transmission chain.
Claims: C1, C2
Visual: V02 | mechanism | Demand-to-earnings transmission | diagram

# S03 Scenarios and valuation
Purpose: Turn assumptions into an investable decision.
Claims: C3, C5
Visual: V03 | decision | Scenario and valuation range | chart
```

Use `S01`, `S02`, and so on in outline order. Chapter files must start with the identical `# SXX Title` heading. Additional subsections may appear inside a chapter, but they must not replace, rename, or reorder the planned sections.

## Visual plan

Each `Visual:` line has four pipe-separated fields:

```text
Visual: <ID> | <role> | <title> | <form>
```

- ID: `V01`, `V02`, and so on; unique and ordered.
- Role: `comparison`, `mechanism`, or `decision`.
- Title: the visible claim-oriented title.
- Form: `chart`, `diagram`, or `table`.

A deep investment report needs all three roles. Include both a quantitative chart and a causal diagram; use a table where exact lookup is more useful. Do not add decorative charts or invent missing values.

## HTML binding

Render report sections in outline order with `data-section-id="SXX"`. Render every planned visual exactly once as:

```html
<figure
  data-visual-id="V01"
  data-visual-role="comparison"
  data-visual-title="Risk–reward comparison"
  data-visual-source="chunk-id-1 chunk-id-2"
  aria-label="Accessible description of the comparison"
>
  <!-- static inline SVG or a table -->
</figure>
```

`data-visual-source` contains one or more exact `evidence.json` chunk IDs. Charts and diagrams use static inline SVG; tables use HTML table markup. Every visual also needs a visible title, units where relevant, printed numeric anchors, and a source line. The prose immediately before it states the conclusion it supports.
