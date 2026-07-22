# File Specs

Use these file shapes unless the user asks for another format.

## `sell_side_logic.md`

Recommended sections:

1. Topic and scope
2. Core conclusion
3. Causal chain
4. Key assumptions
5. Leading indicators
6. Risks and invalidation conditions
7. Claims to validate
8. Optional coverage note

Keep each claim short and uniquely numbered, for example `C1`, `C2`, `C3`.

## `validation.md`

Structure the file by claim ID.

For each claim, include:

1. Claim
2. Support evidence
3. Opposing evidence
4. Calibration result
5. Unverified point
6. Evidence strength: `strong`, `medium`, `weak`, or `insufficient`
7. What would change the judgment

Prefer exact source locators when available.

## `report_outline.md`

Recommended fields per section:

1. Section title
2. Purpose
3. 2-4 sentence summary
4. Supporting validated claims
5. Planned tables or figures
6. Planned source coverage summary

Use outline text as a blueprint, not draft prose.

## `final_report.md`

Recommended structure:

1. Executive summary
2. Topic framing and why it matters
3. Logic map
4. Validation findings
5. Key debates and unresolved points
6. Implications
7. Risks and falsifiers
8. Conclusion
9. Sources and coverage

Default citation policy:

- Use `sell` for framework and structured data.
- Use `primary` for reader-facing quoted passages and qualitative proof.
- Render primary quotations as visible `<blockquote class="primary-quote">` blocks. Replace `{{primary_quote_source}}` with a source label built from the matched evidence entry's source party/title and `published_at`; keep page/line locators in `evidence.json` for traceability, not in the visible source line. Do not use generic or invented source labels. Keep sell-side citations in the template's `.src` hover tooltip.
- Allow charts, tables, and quantitative analysis to use `sell` data extensively.
- Calibrate key `sell` figures with `primary` where possible.
- Do not let uncalibrated `sell` data silently function as validated fact.

The final report should include a source coverage section that summarizes:

- retrieval rounds used
- total `sell` reports retrieved, deduplicated, read, and cited
- total `primary` sources retrieved, deduplicated, read, and cited
- recency coverage across the main time windows
- the main source mix used in the report

## Optional Files

### `coverage_stats.json`

Use when search breadth matters. Suggested fields:

- `retrieval_rounds`
- `query_seeds`
- `sell_reports_retrieved`
- `sell_reports_deduped`
- `sell_reports_read`
- `sell_reports_cited`
- `primary_sources_retrieved`
- `primary_sources_deduped`
- `primary_sources_read`
- `primary_sources_cited`
- `sources_cited`
- `recent_30d_count`
- `recent_30d_share`
- `recent_90d_count`
- `recent_90d_share`
- `recent_180d_count`
- `recent_180d_share`

### `evidence_ledger.json`

Use when traceability matters. Suggested fields:

- `claim_id`
- `stance`
- `source_type`
- `title`
- `institution`
- `published_at`
- `locator`
- `quote`
- `notes`

## `evidence.json`

Treat this as the unified evidence backbone.

Every important claim, quoted passage, tooltip, table source, and chart source should map back to an evidence entry.

Recommended fields include:

- `claim`
- `stance`
- `kind`
- `corpus`
- `chunk_id`
- `report_id`
- `quote`
- `source_type`
- `title`
- `source_path`
- `published_at`
- `page_start` or `line_start`

## `report.html`

Treat `report.html` as one self-contained deliverable with two accessible views:

1. `研报`: the complete reader-facing report rendered in the native investment report template.
2. `证据`: an audit trail rendered from `sell_side_logic.md`, `validation.md`, and every entry in `evidence.json`.

The evidence view must preserve claim IDs using `data-logic-claim-id="<claim_id>"` and `data-validation-claim-id="<claim_id>"`, and show the sell-side causal frame, assumptions, indicators, risks and invalidation conditions. For each validation claim, show support evidence, opposing evidence, calibration result, unverified point, evidence strength, and what would change the judgment. Render the complete evidence ledger with one `data-evidence-id="<chunk_id>"` entry per evidence item, including stance, corpus, quote, title, publication date, and exact page/line locator.

Keep the two views inside the same HTML document. Do not use external pages, external JavaScript, or links to local Markdown files as a substitute for embedded content.
