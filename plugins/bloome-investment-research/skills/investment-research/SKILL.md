---
name: investment-research-agent
description: Runs long-form multi-agent investment research in Codex or Claude/Cowork through staged intermediate artifacts instead of one-shot output. Use for deep company, industry, theme, or thesis reports that separate sell-side logic extraction from primary validation and produce traceable Markdown, HTML, and evidence deliverables.
---

# Investment Research Agent

Use the active host—Codex or Claude/Cowork (including Claude Code plugin runtimes)—as the reasoning runtime and the bundled `research_search`, `research_get_chunk`, and `research_get_report_context` MCP tools as the corpus interface. The host's existing account supplies the model; this beta does not require an additional model key or OAuth flow. Access to the private Bloome research gateway is configured separately with `RESEARCH_API_TOKEN` or `~/.bloome/research-api-token`.

Do not write a long report in one pass. Keep `evidence.json` as the unified evidence backbone. MCP is the shared data plane only: it must never spawn an agent, invoke a model CLI, or call a model API.

## Cross-Runtime Run

1. Create a project workspace at `.bloome/research/<topic-slug>/`.
2. Use the bundled research tools for retrieval and the active host for planning, validation, and synthesis.
3. Save every required staged artifact in that workspace.
4. Call `validate_research_workspace` before final delivery and repair every reported error.
5. Call `open_research_workspace` with the absolute workspace path. In Codex, promote the compact launcher into the native PiP panel and use fullscreen for the report. In Claude Code, use the returned `reportPath` to inspect `report.html`; the same progress, evidence, artifact, and validation data remain available without a rendered MCP App panel. The workbench may frame the report where supported, but the report itself must keep `assets/template.html` unchanged as its visual source of truth.

Useful starter requests:

```text
研究 AI 推理需求对 NAND 价格周期的影响，并生成完整研报。
打开这个项目的 Bloome Research 工作台。
验证当前研报是否满足 investment research 的全部输出要求。
```

The research proxy URL defaults to the Bloome beta gateway. Read the credential from `RESEARCH_API_TOKEN` first, then the user-only `~/.bloome/research-api-token` file; the file is read for each request so Codex Desktop does not need to forward or reload environment variables. `RESEARCH_SEARCH_URL` remains an optional override. If credentials are missing, the proxy is unavailable, or search returns no results, preserve partial artifacts, report the exact retrieval status, and stop evidence-based conclusions.

## Parent and Subagent Roles

The parent owns the landscape pass, module plan, evidence reconciliation, outline, chapter writing, final assembly, HTML rendering, and validation. Workers produce evidence memos only.

After the landscape pass, save `plan.json` with enough non-overlapping modules to cover the topic deeply, using the fields defined in `references/module-contract.md`. Let the question determine module count. Prefer host-native delegation:

- **Claude/Cowork:** delegate module scopes to the bundled `research-module` subagent and optionally use `evidence-auditor` after all memos exist.
- **Codex:** use native subagents with the same module and auditor contracts. Do not require users to install custom `.codex/agents` files.

Run no more than three workers concurrently. Each worker handles one scope and writes only `modules/<id>.md`; it must not write shared evidence or report files. If native subagents are unavailable, denied, lack research-tool access, or fail, run only the missing modules sequentially in the parent with the identical contract. Never replace host delegation with a spawned Claude, Codex, Pi, or model-API process.

Read `references/multiagent-workflow.md` and `references/module-contract.md` before planning or dispatching workers.

## Required Workflow

Run these stages in order:

1. Search both `sell` and `primary` corpora for the initial landscape.
2. Save the topic-shaped module plan, dispatch host-native workers or use the sequential fallback, and read every `modules/<id>.md` memo.
3. Search both corpora iteratively with varied query seeds, relevant time windows, exact chunk reads, and surrounding context. Continue until additional retrieval no longer materially changes the claims, conflicts, or known gaps, or access is exhausted. Record the stopping reason and remaining gaps in `coverage_stats.json`; do not use record counts as a proxy for depth.
4. Reconcile module evidence in the parent, then save `sell_side_logic.md`, `validation.md`, and the unified `evidence.json`. Every evidence item must link to one or more claim IDs and state whether it supports, challenges, or contextualizes them.
5. Save `report_outline.md` using the binding section IDs in `references/report-structure.md`. Plan only visuals that materially improve an evidence-backed argument.
6. Save one `chapter_XX_*.md` for each planned substantive section, in the same order and with the identical `# SXX Title` heading. Every chapter needs source citations and an explicit boundary, opposing-evidence, risk, or invalidation section.
7. Synthesize `final_report.md` from the chapter drafts and reconciled evidence. Rewrite, compress, reorder within the approved outline, and resolve contradictions as needed; preserve the decisive evidence, citations, boundaries, disagreements, and unresolved points rather than every sentence of the drafts.
8. Copy `final_report.md` into `report.md`, then render all of `report.md` into the `研报` tab of a static `report.html`. Preserve the outline's exact section order with `data-section-id`, and render every planned visual once with its `data-visual-id`, role, accessible description, and evidence chunk IDs. The same HTML must embed the complete audit trail in a switchable `证据` tab and include `coverage_stats.json`. Charts and diagrams must be static HTML/CSS/SVG.
9. Call `validate_research_workspace`, repair every error, and only then deliver or open the workspace.

Keep all staged files traceable to `evidence.json`. Do not skip from search notes or module memos directly to the final report.

The bundled `research_search` and `research_get_chunk` tools are the research corpus interface. Do not infer that the corpus is unavailable merely because no separate “knowledge base” skill is installed. If search returns no results or the research proxy is unavailable, report that exact retrieval status and stop evidence-based conclusions; do not replace the research with unsupported industry generalizations.

## Evidence Layers

`sell` is the analytical and quantitative layer. Use it to extract conclusions, causal chains, assumptions, indicators, risks, market size, shipments, pricing, capex, shares, costs, forecasts, model tables, and historical series.

`primary` is the validation layer. Use expert notes, industry interviews, channel checks, filings, earnings transcripts, announcements, and other primary or near-primary materials to support, narrow, calibrate, or challenge each sell-side claim.

For every material claim, record support, opposing evidence, calibration result, unresolved point, evidence strength, and what would change the judgment. If no primary calibration exists, state that plainly.

## Long Report

`final_report.md` is a deliberate synthesis of the chapter drafts and reconciled evidence, not a one-shot answer or a dump of module memos.

- Let the topic determine the number and depth of substantive sections. Go as deep as the available evidence supports; do not target a word, chapter, argument, or source count.
- Each substantive section should connect its conclusion to source-backed data, causal transmission, comparison or calculation where relevant, boundary/opposing evidence, and research implication.
- Use extracted sell-side data, primary calibration, disagreements, scenarios, sensitivities, and company-level transmission where they help answer the topic. Do not add generic filler or repeat the same number.
- Rewrite and compress chapter material when synthesis improves clarity. Preserve decisive evidence, citations, disagreements, boundaries, and unresolved points, and resolve draft contradictions before delivery.

## Final Report and HTML

Use sell-side material for the analytical frame and structured data; use primary material for validation, narrative proof, and calibration. Sell-side data may be used extensively in tables, charts, forecasts, valuation ranges, and model calculations, but key figures should be calibrated by primary evidence where available.

Render primary evidence quotations visibly with `<blockquote class="primary-quote">` and replace `{{primary_quote_source}}` with the exact matched `evidence.json` source label. The visible source line should use the returned source party/title and publication date; keep page/line locators inside `evidence.json` for traceability, not in the reader-facing line. Never write a generic label or invent a source. Keep sell-side references as the template's `.src` hover tooltips. Every claim, quote, table source, chart source, and tooltip must map to `evidence.json`. Keep disagreements visible.

Use `assets/template.html` as the visual source of truth; fill its placeholders and insert report content within its existing structure. Do not replace it with a newly invented card layout or a different page structure. Preserve its header, judgment block, section layout, source bar, hover-tooltip system, and existing visual language. Surface source coverage in the report, including sell-side reports read and primary/industry materials read. Pass `report_month` to `research_synthesize` when a data cutoff is specified, for example `2026年7月`.

Keep both views in the same self-contained `report.html`:

- The `研报` tab contains the complete reader-facing investment report and remains the default view.
- The `证据` tab renders the structured content of `sell_side_logic.md`, then the claim-by-claim content of `validation.md`, then the complete `evidence.json` ledger. Do not link out to the Markdown files or paste raw Markdown into the page.
- Preserve every claim ID across sell-side logic and validation. Set each logic entry's `data-logic-claim-id` and each validation entry's `data-validation-claim-id` to the exact claim ID. For each validation claim, visibly include support evidence, opposing evidence, calibration result, unverified point, evidence strength, and what would change the judgment.
- Render every `evidence.json` item once in the ledger and set its entry's `data-evidence-id` to the exact `chunk_id`, `data-evidence-claim-ids` to its space-separated claim IDs, and `data-relation` to `support`, `challenge`, or `context`. Include those links plus stance, corpus, quote, source title, publication date, and page/line locator.
- Preserve the template's accessible tab roles, keyboard behavior, mobile layout, and print behavior. Do not remove the tab script or leave any template placeholders unresolved.

For numeric data, use the available `reson-charts` capability and follow its documented schema; if unavailable, use self-contained inline SVG/CSS. For relationships and mechanisms, use the reusable components in `references/concept-diagrams.md`. Select chart types from the data structure and read `references/chart-rules.md` before rendering. Visuals are optional; if one is planned, ground and render it fully rather than substituting decoration.

## Output References

Read `references/file-specs.md` for staged artifact shapes, evidence fields, and coverage statistics.

Read `references/multiagent-workflow.md` and `references/module-contract.md` for host-native delegation, fallback, plan fields, and module memo output.

Read `references/report-structure.md` for binding section IDs, outline order, and planned visual markup.

Read `references/chart-rules.md` for chart selection and rendering requirements.

Read `references/concept-diagrams.md` for causal chains, thresholds, convergence, multiples, process flows, and ranges.
