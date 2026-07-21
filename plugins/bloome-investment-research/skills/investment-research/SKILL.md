---
name: investment-research-agent
description: Runs long-form investment research natively in Codex through staged intermediate artifacts instead of one-shot output. Use for deep company, industry, theme, or thesis reports that separate sell-side logic extraction from primary validation and produce traceable Markdown, HTML, and evidence deliverables.
---

# Investment Research Agent

Use Codex as the reasoning runtime and the bundled `research_search`, `research_get_chunk`, and `research_get_report_context` MCP tools as the corpus interface. Codex's existing account supplies the model; this beta does not require an additional model key or OAuth flow. Access to the private Bloome research gateway is configured separately with `RESEARCH_API_TOKEN`.

Do not write a long report in one pass. Keep `evidence.json` as the unified evidence backbone.

## Codex Native Run

1. Create a project workspace at `.bloome/research/<topic-slug>/`.
2. Use the bundled research tools for retrieval and Codex for planning, validation, and synthesis.
3. Save every required staged artifact in that workspace.
4. Call `validate_research_workspace` before final delivery and repair every reported error.
5. Call `open_research_workspace` with the absolute workspace path to render the Bloome workbench. The workbench may frame the report, but the report itself must keep `assets/template.html` unchanged as its visual source of truth.

Useful starter requests:

```text
研究 AI 推理需求对 NAND 价格周期的影响，并生成完整研报。
打开这个项目的 Bloome Research 工作台。
验证当前研报是否满足 investment research 的全部输出要求。
```

The research proxy URL defaults to the Bloome beta gateway. `RESEARCH_API_TOKEN` is required and must come from the local environment; `RESEARCH_SEARCH_URL` remains an optional override. If credentials are missing, the proxy is unavailable, or search returns no results, preserve partial artifacts, report the exact retrieval status, and stop evidence-based conclusions.

## Required Workflow

Run these stages in order:

1. Search both `sell` and `primary` corpora.
2. Run at least two retrieval rounds for each corpus, with multiple query seeds, at least one recency window per corpus, and at least 40 retrieved records per corpus. The `size=20` limit is per call, not the project total.
3. Save `sell_side_logic.md`.
4. Save `validation.md`.
5. Save `report_outline.md` or `report_outline.json`.
6. Save one `chapter_XX_*.md` for each substantive report section.
7. Save and synthesize `final_report.md`, `report.md`, `report.html`, `evidence.json`, and `coverage_stats.json`.

Keep all staged files traceable to `evidence.json`. Do not skip from search notes directly to the final report.

The bundled `research_search` and `research_get_chunk` tools are the research corpus interface. Do not infer that the corpus is unavailable merely because no separate “knowledge base” skill is installed. If search returns no results or the research proxy is unavailable, report that exact retrieval status and stop evidence-based conclusions; do not replace the research with unsupported industry generalizations.

## Evidence Layers

`sell` is the analytical and quantitative layer. Use it to extract conclusions, causal chains, assumptions, indicators, risks, market size, shipments, pricing, capex, shares, costs, forecasts, model tables, and historical series.

`primary` is the validation layer. Use expert notes, industry interviews, channel checks, filings, earnings transcripts, announcements, and other primary or near-primary materials to support, narrow, calibrate, or challenge each sell-side claim.

For every material claim, record support, opposing evidence, calibration result, unresolved point, evidence strength, and what would change the judgment. If no primary calibration exists, state that plainly.

## Long Report

`final_report.md` is assembled from chapter drafts, not generated as a short summary.

- Use at least five substantive sections for a deep report, excluding the opening judgment, source coverage, and references.
- Each substantive section needs at least two argument units. Each unit should connect a conclusion, source-backed data, causal transmission, comparison or calculation, boundary/opposing evidence, and investment implication.
- Use extracted sell-side data, primary calibration, disagreements, scenarios, sensitivities, and company-level transmission to add depth. Do not add generic filler or repeat the same number.
- Preserve chapter detail, tables, charts, citations, and unresolved points in the final assembly.

## Final Report and HTML

Use sell-side material for the analytical frame and structured data; use primary material for validation, narrative proof, and calibration. Sell-side data may be used extensively in tables, charts, forecasts, valuation ranges, and model calculations, but key figures should be calibrated by primary evidence where available.

Render primary evidence quotations visibly with `<blockquote class="primary-quote">` and replace `{{primary_quote_source}}` with the exact matched `evidence.json` source label. The visible source line should use the returned source party/title and publication date; keep page/line locators inside `evidence.json` for traceability, not in the reader-facing line. Never write a generic label or invent a source. Keep sell-side references as the template's `.src` hover tooltips. Every claim, quote, table source, chart source, and tooltip must map to `evidence.json`. Keep disagreements visible.

Use `assets/template.html` as the visual source of truth; fill its placeholders and insert report content within its existing structure. Do not replace it with a newly invented card layout or a different page structure. Preserve its header, judgment block, section layout, source bar, hover-tooltip system, and existing visual language. Surface source coverage in the report, including sell-side reports read and primary/industry materials read. Pass `report_month` to `research_synthesize` when a data cutoff is specified, for example `2026年7月`.

For numeric data, use the available `reson-charts` capability and follow its documented schema; if unavailable, use self-contained inline SVG/CSS. For relationships and mechanisms, use the reusable components in `references/concept-diagrams.md`. Select chart types from the data structure and read `references/chart-rules.md` before rendering.

## Output References

Read `references/file-specs.md` for staged artifact shapes, evidence fields, and coverage statistics.

Read `references/chart-rules.md` for chart selection and rendering requirements.

Read `references/concept-diagrams.md` for causal chains, thresholds, convergence, multiples, process flows, and ranges.
