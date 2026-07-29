---
name: investment-research-agent
description: Runs long-form multi-agent investment research in Codex or Claude/Cowork through staged intermediate artifacts instead of one-shot output. Use for deep company, industry, theme, or thesis reports that separate sell-side logic extraction from primary validation and produce traceable Markdown, HTML, and evidence deliverables.
---

# Investment Research Agent

Use the active host—Codex or Claude/Cowork (including Claude Code plugin runtimes)—as the reasoning runtime and the bundled `research_search`, `research_get_chunk`, and `research_get_report_context` MCP tools as the corpus interface. The host's existing account supplies the model. On the first research call, Bloome Finance opens a browser for account sign-in and device authorization when needed. A new workspace's first retrieval returns a quote without charging; explicit confirmation starts its run and consumes one research credit, or zero credits during active annual unlimited access. All later retrieval using that same absolute workspace while the run is active is included.

Do not write a long report in one pass. Keep `evidence.json` as the unified evidence backbone. MCP is the shared data plane only: it must never spawn an agent, invoke a model CLI, or call a model API.

## Expert Evidence Gate

`sell` and `primary` may be searched in either order. Keep `sell` and `primary` as separate corpus searches. Primary results have no expert/official source label, so within `primary` run separate expert-targeted and official-targeted `research_search` calls using different concepts and phrases—never `source_types`. Search experts with company or product plus customer, supplier, competitor, channel, and former-employee roles, varying demand, orders, inventory, capacity, pricing, delivery, product progress, share, and time terms. Finding official materials does not complete primary research. Expert evidence is the highest-priority retrieval requirement for completed research: the report must show multiple independent expert passages distributed across the core industry claims rather than concentrated in one section. This is a floor on expert coverage, not a ceiling on other evidence — never cut or thin sell-side or official content to shift the evidence mix toward experts. If a core industry claim lacks expert evidence, repeat the expert search with different roles, chain positions, and wording. If a gap remains after exhaustive expert search, still write the complete report: state the gap explicitly where the affected claim is argued and label that claim as lacking expert corroboration, instead of dropping the claim, thinning the analysis, or withholding the report because only official material supports it.

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

Before the first corpus tool call in each task, tell the user in their language: Bloome Finance may open in the browser for sign-in and device approval on first use, and research will continue automatically after approval. Never open an unexplained login window.

## Billing Confirmation Gate

`confirmationRequired` is a quote, not an error, quota exhaustion, or an execution-environment block. When a retrieval tool returns it:

1. Stop and show the returned topic, cost, and current balance.
2. Ask the user to confirm the quoted cost in conversation. Do not substitute stale workspace chunks or claim remote retrieval is unavailable while confirmation is pending.
3. Call `confirm_research_run` only after the user explicitly agrees.
4. After confirmation succeeds, retry the original retrieval with the exact same absolute workspace path. Do not ask again during that active run.

Only a `402` response means the account has no usable credits. Report that exact status and direct the user to the returned pricing URL. Pass the same absolute research workspace path in every corpus tool call; changing it creates a separate quoted run. The local MCP stores the authorized device credential under `~/.bloome/` and a non-secret run marker inside the workspace. Once the finished report passes validation, `validate_research_workspace` generates and returns its directly accessible `/reports/<id>` link while closing the active run. Keep user-facing delivery simple: before the action, say “我现在生成报告链接。” only when a progress update is useful; after success, say “报告链接已生成：<link>”. Do not narrate transport, storage, or publication infrastructure; describe the result only as a directly accessible report link. The approved research run already authorizes final link generation, so no second confirmation is needed. If material new evidence is found before link generation, update and revalidate the report first; never knowingly generate a stale link and explain the missing evidence as a caveat afterward. If authorization is revoked, credits are exhausted, the gateway is unavailable, or search returns no results, preserve partial artifacts, report the exact retrieval status, and stop evidence-based conclusions. `BLOOME_FINANCE_URL` may override the Finance service URL for local development.

## Parent and Subagent Roles

The parent owns the landscape pass, module plan, evidence reconciliation, outline, chapter writing, final assembly, HTML rendering, and validation. Workers produce evidence memos only.

After the landscape pass, save `plan.json` with enough non-overlapping modules to cover the topic deeply, using the fields defined in `references/module-contract.md`. Let the question determine module count, but when coverage depth is in doubt, prefer more, narrower modules over fewer broad ones: split any module whose question contains two separable evidence questions, and give each mechanism, market layer, or company that materially affects the conclusion its own module. Prefer host-native delegation:

- **Claude/Cowork:** delegate module scopes to the bundled `research-module` subagent and optionally use `evidence-auditor` after all memos exist.
- **Codex:** use native subagents with the same module and auditor contracts. Do not require users to install custom `.codex/agents` files.

Let the host manage worker scheduling and concurrency. Each worker handles one scope and writes only `modules/<id>.md`; it must not write shared evidence or report files. If native subagents are unavailable, denied, lack research-tool access, or fail, run only the missing modules sequentially in the parent with the identical contract. Never replace host delegation with a spawned Claude, Codex, Pi, or model-API process.

Read `references/multiagent-workflow.md` and `references/module-contract.md` before planning or dispatching workers.

## Required Workflow

Run these stages in order:

1. Search both `sell` and `primary` corpora for the initial landscape in either order, passing the same absolute `workspace` path to every research tool call. Within `primary`, make and record separate expert-targeted and official-targeted calls as required above. Handle the one-time conversational research confirmation before continuing retrieval.
2. Save the topic-shaped module plan, dispatch host-native workers or use the sequential fallback, and read every `modules/<id>.md` memo.
3. Search both corpora iteratively with varied query seeds, relevant time windows, exact chunk reads, and surrounding context. In every `primary` iteration, keep expert-targeted and official-targeted directions separate; official results do not count as expert coverage. Continue expert retrieval with different roles, value-chain positions, and wording until the core industry claims are covered by multiple independent expert sources. Continue until additional retrieval no longer materially changes the claims, conflicts, or known gaps, or access is exhausted. Record the stopping reason and remaining gaps in `coverage_stats.json`; do not use record counts as a proxy for depth.
4. Reconcile every module candidate in `evidence_disposition.md`: accept it into the evidence backbone or reject it with a reason. Then save `sell_side_logic.md`, `validation.md`, and the unified `evidence.json`. Every accepted item must link to one or more claim IDs and state whether it supports, challenges, or contextualizes them.
5. Save `decision.md` as natural Markdown. For a ranked investment decision, state the priority rule and ranking, compare every alternative on the same basis, explain any normalization, identify the evidence that drives the order, and say what would change it. Use scores or weights only when they improve the reasoning.
6. Save `report_outline.md` as a natural-language editorial plan using `references/report-structure.md`. Do not add internal section or visual IDs. For comparative or ranked decisions, plan enough sections to show the decision rule, common comparison basis, winner's full demand-to-earnings mechanism, cycle/scenario boundary, security-level valuation, every material alternative, monitoring, and unresolved gaps. When a visual materially improves an evidence-backed argument, use the `investment-visualization` skill to plan it; otherwise use prose or a compact table. Plan sections so that every module whose evidence was accepted is visibly represented in the report; do not average several evidence-rich modules into one thin section.
7. Save one `chapter_XX_*.md` for each planned substantive section in the same editorial order. Use natural headings. Every chapter needs a direct answer, source-backed mechanism, relevant numbers or calculations, investment implication, and an explicit boundary, opposing-evidence, risk, or invalidation discussion. Do not let chapter drafts collapse into executive-summary paragraphs. Write the reasoning out at full length: every causal step in a chain gets its own explicit statement with its supporting evidence — never leap from a premise to a conclusion across unstated intermediate links.
8. Synthesize `final_report.md` from the chapter drafts, reconciled evidence, and `decision.md`. Rewrite and remove repetition, but do not compress away causal bridges, comparison logic, valuation normalization, scenario sensitivity, company-by-company reasoning, or conditions that change the ranking. Compression may remove repetition, never reasoning steps: a reader must be able to follow every conclusion from its evidence without reconstructing missing logic. Preserve decisive accepted evidence, citations, boundaries, disagreements, unresolved points, and the exact final ranking.
9. Copy `final_report.md` into `report.md`, then render all of `report.md` into a static single-page `report.html` using `assets/template.html`. Preserve the outline's editorial order without exposing internal planning labels. Confirm content parity: every reader-facing heading, paragraph, list, table, primary quote, citation, and planned visual in `report.md` must appear in `report.html`; never render only an executive-summary excerpt. Use the `investment-visualization` skill to render and visually review every planned figure inside that template.
10. Call `validate_research_workspace`, repair every error, and only then deliver or open the workspace. Successful validation also closes the research run.

Keep all staged files traceable to `evidence.json`. Do not skip from search notes or module memos directly to the final report.

The bundled `research_search` and `research_get_chunk` tools are the research corpus interface. Do not infer that the corpus is unavailable merely because no separate “knowledge base” skill is installed. If search returns no results or the research proxy is unavailable, report that exact retrieval status and stop evidence-based conclusions; do not replace the research with unsupported industry generalizations.

## Evidence Layers

`sell` contains research published by sell-side institutions. It represents what the market believes: earnings forecasts, key assumptions, debates, risks, and valuation frameworks. Use it to extract the investment logic, causal chain, key assumptions, forecasts, disagreements, and valuation framework, and to understand what the market has priced in. Treat its conclusions as hypotheses to test against industry-expert and official material, not proof of industry reality.

`sell` is also the primary source of structured quantitative material—market size, shipments, pricing, capex, shares, costs, forecasts, model tables, and historical series—and it should be used extensively to build the report's tables, charts, forecasts, valuation ranges, and model calculations. Calibrate key figures against `primary` where available, but using `sell` quantitative content is expected and correct, not a defect; only its uncalibrated conclusions are treated as hypotheses rather than proven fact.

Within `primary`, search two material categories separately:

- **Industry-expert material:** expert interviews, former-employee interviews, industry-participant or consultant conversations, channel checks, fieldwork, and research notes based on direct industry-participant commentary. Search across customers and end users, procurement or operations staff, upstream suppliers, competitors, distributors and channel partners, integrators, and former executives or employees. Use these materials to identify leading changes in demand, orders, inventory, capacity, pricing, delivery, product progress, and market share. Seek supporting, opposing, and conflicting views from different roles and value-chain positions.
- **Official material:** regulatory filings, company announcements, government documents, investor-relations materials, and earnings releases or calls. Use these materials to confirm disclosed facts and management statements. Earnings-call management commentary is official material, not an expert interview.

Finding official materials does not complete primary research. Continue searching industry-expert material until the core industry claims have broad, independent expert coverage or the remaining evidence gap is explicitly reported.

`primary` outweighs `sell`. Sell-side views are hypotheses to be tested against primary reality, not co-equal proof: when the two conflict on the same question, let `primary` control the conclusion and keep the overruled sell-side view visible as a disagreement. A claim supported only by sell-side narrative carries less evidence strength than one calibrated by primary material, and should be labeled accordingly.

For every material claim, record support, opposing evidence, calibration result, unresolved point, evidence strength, and what would change the judgment. If no primary calibration exists, state that plainly.

## Long Report

The expert-evidence gate is a quality bar on the evidence, not a scope limit on the report. Passing it does not mean the report is finished: the final report must still present the complete argumentation for every accepted module, covering each accepted module's full reasoning from evidence to conclusion. Satisfying the visible-primary requirements never licenses dropping analysis, chapters, or accepted modules to make validation easier.

`final_report.md` is a deliberate synthesis of the chapter drafts and reconciled evidence, not a one-shot answer or a dump of module memos.

- Let the topic and available evidence determine the final length. Do not set word, character, chapter, argument, or source-count targets. Judge completeness by whether the report contains every decision-relevant layer supported by the evidence.
- Argue completely. Every material conclusion must show its full inference chain — data → mechanism → intermediate inference → conclusion — with each link stated explicitly and either evidence-backed or flagged as an assumption. Do not skip intermediate reasoning steps; if a link cannot be supported, say so instead of writing around it. When brevity and a complete argument conflict, keep the complete argument.
- Before finalizing a deep comparative or ranked report, reopen the outline and module memos and check for missing mechanism, normalization, scenarios, alternatives, valuation, monitoring, or unresolved conflicts. Give each material alternative enough separate treatment to make the ranking auditable; do not hide distinct investment questions inside one compressed paragraph.
- Each substantive section should connect its conclusion to source-backed data, causal transmission, comparison or calculation where relevant, primary calibration, boundary/opposing evidence, and research implication. The winning thesis needs a complete demand → qualified supply → pricing → margin/EPS → valuation bridge.
- Preserve useful detail from chapter drafts: decisive numbers, formula or normalization basis, scenario assumptions, stock-specific catalysts, disconfirming evidence, and ranking-flip conditions. Summarize source descriptions, not the reasoning needed to audit the investment decision.
- Use extracted sell-side data, primary calibration, disagreements, scenarios, sensitivities, and company-level transmission where they help answer the topic. Do not add generic filler or repeat the same number.
- Rewrite and compress chapter material only when synthesis improves clarity. Before delivery, compare `final_report.md` against every chapter heading and `decision.md`; any omitted section must be either redundant or explicitly out of scope. Preserve decisive accepted evidence, citations, disagreements, boundaries, unresolved points, and the ranking recorded in `decision.md`; resolve draft contradictions before delivery.

## Final Report and HTML

Use sell-side material for the analytical frame and structured data; use primary material for validation, narrative proof, and calibration. Sell-side data may be used extensively in tables, charts, forecasts, valuation ranges, and model calculations, but key figures should be calibrated by primary evidence where available. Expert evidence is the report's highest-priority reader-facing evidence layer. The report body must show complete verbatim passages from multiple independent expert sources and cover the core industry claims; a single quote, sentence excerpt, or source-only listing is invalid. For every core industry claim supported or challenged by expert evidence, show at least one matched expert passage in a visible `primary-quote` block.

Render primary evidence quotations visibly with `<blockquote class="primary-quote">` and replace `{{primary_quote_source}}` with the exact matched `evidence.json` source label. The visible source line should use the returned source party/title and publication date; keep page/line locators inside `evidence.json` for traceability, not in the reader-facing line. Never write a generic label or invent a source. Keep sell-side references as the template's `.src` hover tooltips. The tooltip body (`tip-bd`) must carry the full original passage from the matched evidence entry — the complete paragraph(s) containing the cited statement, copied verbatim, not a one-sentence summary or paraphrase. Readers open tooltips to read the original text; include every relevant paragraph (separated by blank lines; the tip scrolls, so length is not a reason to trim). Keep tooltip body text plain: do not wrap passages or sentences in `<u>` and do not add underlines. Both the visible `primary-quote` blocks and the `.src` tooltip bodies must reproduce the matched `evidence.json` `quote` in full and verbatim: validation checks that the rendered text contains the whole quote string, so never abridge it, drop clauses, or replace interior text with an ellipsis (`……`/`...`), and never compress a multi-sentence passage into a single representative line. When the original passage is long, show all of it — a longer, complete quotation is correct; a short excerpt is a defect. Every claim, quote, table source, chart source, and tooltip must map to `evidence.json`. Keep disagreements visible.

Use `assets/template.html` as the visual source of truth; fill its placeholders and insert report content within its existing structure. Do not replace it with a newly invented card layout or a different page structure. Preserve its header, judgment block, section layout, source bar, hover-tooltip system, and existing visual language. Surface source coverage in the report, including sell-side reports read and primary/industry materials read. Pass `report_month` to `research_synthesize` when a data cutoff is specified, for example `2026年7月`.

Keep `report.html` reader-facing and single-page, exactly following the bundled template's structure and visual language. Do not add report/evidence tabs or embed the audit ledger into the page. The audit trail remains in `sell_side_logic.md`, `validation.md`, `evidence_disposition.md`, `decision.md`, and `evidence.json`. Do not leave template placeholders unresolved.

Long HTML should feel like an editorial report, not a tall text dump. Keep the full prose, but break it with natural section labels, compact evidence tables, visible primary quotations, and only the figures that materially advance the decision. On desktop and narrow-phone screenshots, inspect the beginning, middle, and end of the page; verify that wide tables remain readable, no section is clipped, and the renderer has not silently dropped late-report content.

For visual selection, financial chart grammar, annotation, uncertainty, responsive composition, and screenshot-based review, use the separate `investment-visualization` skill. Keep that editorial judgment in the skill rather than encoding it as validator regex or fixed HTML classes.

## Output References

Read `references/file-specs.md` for staged artifact shapes, evidence fields, and coverage statistics.

Read `references/multiagent-workflow.md` and `references/module-contract.md` for host-native delegation, fallback, plan fields, and module memo output.

Read `references/report-structure.md` for the natural-language outline, chapter order, and visual notes.
