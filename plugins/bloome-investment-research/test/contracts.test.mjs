import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const root = new URL("../", import.meta.url);
const contracts = new Map([
  ["skills/investment-research/assets/template.html", "0c6f43ef2a6b75bb6ce344595b9aee2982f0b64df19522be9df2820d0026f85f"],
  ["skills/investment-research/references/file-specs.md", "da5cd2dfa9f1fc5736bdc6e0e5e6652647f5f3b25c6f8e1b08202101d9918270"],
  ["skills/investment-research/references/chart-rules.md", "0f00fd2b12f7dac44b6ee3c718d638fd09fd5e078e03c1c6d2bc72b108282e10"],
]);

test("investment report contracts remain byte-for-byte unchanged", async () => {
  for (const [file, expected] of contracts) {
    const digest = createHash("sha256").update(await readFile(new URL(file, root))).digest("hex");
    assert.equal(digest, expected, file);
  }
});

test("cross-runtime skill keeps the original report template as source of truth", async () => {
  const skill = await readFile(new URL("skills/investment-research/SKILL.md", root), "utf8");
  const template = await readFile(new URL("skills/investment-research/assets/template.html", root), "utf8");
  assert.match(skill, /Use `assets\/template\.html` as the visual source of truth/);
  assert.match(skill, /Do not replace it with a newly invented card layout/);
  assert.match(skill, /evidence\.json` as the unified evidence backbone/);
  assert.match(skill, /Codex or Claude\/Cowork/);
  assert.match(skill, /host's existing account supplies the model/);
  assert.match(skill, /returned `reportPath`/);
  assert.match(skill, /same self-contained `report\.html`/);
  assert.match(template, /data-report-tab="report"/);
  assert.match(template, /data-report-tab="evidence"/);
  assert.match(template, /data-evidence-section="validation"/);
  assert.match(template, /data-evidence-id="\{\{evidence_chunk_id\}\}"/);
  assert.match(template, /data-evidence-claim-ids="\{\{evidence_claim_ids\}\}"/);
  assert.match(template, /data-relation="\{\{evidence_relation\}\}"/);
});

test("shared workflow delegates bounded memos with a sequential fallback", async () => {
  const [skill, moduleContract, workflow, reportStructure, worker, auditor] = await Promise.all([
    "skills/investment-research/SKILL.md",
    "skills/investment-research/references/module-contract.md",
    "skills/investment-research/references/multiagent-workflow.md",
    "skills/investment-research/references/report-structure.md",
    "agents/research-module.md",
    "agents/evidence-auditor.md",
  ].map((file) => readFile(new URL(file, root), "utf8")));
  assert.match(skill, /Run no more than three workers concurrently/);
  assert.match(skill, /run only the missing modules sequentially in the parent/);
  assert.match(skill, /render all of `report\.md` into the `研报` tab of a static `report\.html`/);
  assert.match(skill, /Continue until additional retrieval no longer materially changes/);
  assert.match(skill, /Synthesize `final_report\.md` from the chapter drafts and reconciled evidence/);
  assert.match(skill, /writes only `modules\/<id>\.md`/);
  assert.match(workflow, /MCP provides research data and deterministic validation; it never starts agents or calls a model/);
  assert.match(moduleContract, /# Conflicts and date reconciliation/);
  assert.match(moduleContract, /must not write an executive summary, chapter, outline, `evidence\.json`, or final report/);
  assert.match(reportStructure, /The approved outline is binding/);
  assert.match(reportStructure, /data-visual-source="chunk-id-1 chunk-id-2"/);
  assert.match(worker, /Write only the assigned module memo/);
  assert.match(auditor, /Do not edit files or write report prose/);
});
