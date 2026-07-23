# Multi-Agent Workflow

The parent host owns the research run. MCP provides research data and deterministic validation; it never starts agents or calls a model.

## Dispatch

1. Run a broad sell-side and primary-source landscape search.
2. Save `plan.json` with enough non-overlapping modules to cover the topic deeply, using `module-contract.md`.
3. Prefer the host's native subagents. Run at most three module workers concurrently; queue any remainder.
4. Give each worker the topic, one complete module object, the absolute workspace, its unique `modules/<id>.md` output path, and the module contract.
5. If native delegation is unavailable, denied, lacks the research tools, or a worker fails, complete only the missing modules sequentially in the parent with the same contract. Do not invoke a model CLI, process, or API as a substitute.
6. Read every completed memo. Optionally use one native evidence-auditor subagent to identify unsupported claims, source conflicts, duplicate evidence, and missing locators. The auditor does not write shared artifacts.

## Reconciliation and writing

The parent, not a worker:

1. Deduplicates evidence by source and locator.
2. Resolves same-chain date conflicts while preserving independent disagreement.
3. Merges accepted rows into the single `evidence.json` backbone.
4. Writes `sell_side_logic.md`, `validation.md`, and a binding outline with stable section IDs and an evidence-backed visual plan.
5. Writes each chapter in the outline's exact order and keeps its section ID/title unchanged. Every substantive chapter includes citations and an explicit boundary, opposing-evidence, risk, or invalidation section.
6. Synthesizes `final_report.md` from the chapter drafts and reconciled evidence. Rewrite and compress where useful, resolve contradictions, and retain decisive evidence, citations, boundaries, disagreements, and unresolved points.
7. Copies `final_report.md` into `report.md`, renders all of it—not a dashboard summary—into static `report.html`, then runs workspace validation.

Only module memos may be written concurrently. Shared evidence, validation, chapter, and final-report files are parent-owned and written after workers finish.
