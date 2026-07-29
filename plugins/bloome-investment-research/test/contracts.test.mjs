import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const root = new URL("../", import.meta.url);
const contracts = new Map([
  ["skills/investment-research/assets/template.html", "852b77895aff9685e8387562583c612eac8dab48452a438d8cff35dfaea4ae18"],
  ["skills/investment-research/references/file-specs.md", "895282055c32abb774ac70f59204ed43076d7c3c08502dc71d1e531e64578adf"],
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
  assert.match(skill, /reader-facing and single-page/);
  assert.match(template, /class="report"/);
  assert.doesNotMatch(template, /data-report-tab|data-evidence-section/);
});

test("report-link delivery uses direct user-facing language", async () => {
  const [skill, server, readme] = await Promise.all([
    "skills/investment-research/SKILL.md",
    "mcp/server.cjs",
    "../../README.md",
  ].map((file) => readFile(new URL(file, root), "utf8")));
  assert.match(skill, /报告链接已生成：<link>/);
  assert.match(skill, /never knowingly generate a stale link/);
  assert.doesNotMatch(skill, /我先把当前版本上传|Bloome 外部服务|私有链接/);
  assert.match(server, /Validate report and generate link/);
  assert.doesNotMatch(server, /file-upload|deployable report link/);
  assert.match(readme, /生成可直接访问的报告链接/);
});

test("research skill makes industry-expert search a mandatory first gate", async () => {
  const skill = await readFile(new URL("skills/investment-research/SKILL.md", root), "utf8");
  assert.match(skill, /Keep `sell` and `primary` as separate corpus searches/);
  assert.match(skill, /run two separate `research_search` calls using different concepts and phrases/);
  assert.match(skill, /never `source_types`/);
  assert.match(skill, /classify accepted primary evidence as `primary_layer: "expert"` or `"official"`/);
  assert.match(skill, /customer, supplier, competitor, channel, and former-employee roles/);
  assert.match(skill, /Industry-expert material includes expert interviews, former-employee interviews, industry interviews, consultant conversations, channel checks, fieldwork/);
  assert.match(skill, /Official material includes regulatory filings, company announcements, government documents, investor-relations materials, and earnings releases or calls/);
  assert.match(skill, /Earnings-call management commentary is official material, not an expert interview/);
  assert.match(skill, /one expert item never completes the expert gate/);
  assert.match(skill, /highest-priority reader-facing evidence layer/);
  assert.match(skill, /complete verbatim passages from multiple independent expert sources/);
  assert.match(skill, /a single quote, sentence excerpt, or source-only listing is invalid/);
  assert.match(skill, /For every core industry claim supported or challenged by expert evidence/);
  assert.match(skill, /Official material never substitutes for the expert-first gate/);
  assert.match(skill, /instead of writing an official-only report/);
});

test("shared workflow delegates memos with host-managed concurrency and a sequential fallback", async () => {
  const [skill, moduleContract, workflow, reportStructure, worker, auditor] = await Promise.all([
    "skills/investment-research/SKILL.md",
    "skills/investment-research/references/module-contract.md",
    "skills/investment-research/references/multiagent-workflow.md",
    "skills/investment-research/references/report-structure.md",
    "agents/research-module.md",
    "agents/evidence-auditor.md",
  ].map((file) => readFile(new URL(file, root), "utf8")));
  assert.match(skill, /Let the host manage worker scheduling and concurrency/);
  assert.match(skill, /run only the missing modules sequentially in the parent/);
  assert.match(skill, /render all of `report\.md` into a static single-page `report\.html`/);
  assert.match(skill, /evidence_disposition\.md/);
  assert.match(skill, /Save `decision\.md`/);
  assert.match(skill, /Continue until additional retrieval no longer materially changes/);
  assert.match(skill, /Synthesize `final_report\.md` from the chapter drafts, reconciled evidence, and `decision\.md`/);
  assert.match(skill, /writes only `modules\/<id>\.md`/);
  assert.match(workflow, /MCP provides research data and deterministic validation; it never starts agents or calls a model/);
  assert.match(moduleContract, /# Conflicts and date reconciliation/);
  assert.match(moduleContract, /must not write an executive summary, chapter, outline, `evidence\.json`, or final report/);
  assert.match(reportStructure, /natural-language editorial plan/);
  assert.match(reportStructure, /Do not require internal labels such as `S01` or `V01`/);
  assert.match(worker, /Write only the assigned module memo/);
  assert.match(auditor, /Do not edit files or write report prose/);
});

test("editorial investment visualization is owned by a reusable skill, not validator markup", async () => {
  const [skill, grammar, review, researchSkill] = await Promise.all([
    "skills/investment-visualization/SKILL.md",
    "skills/investment-visualization/references/editorial-grammar.md",
    "skills/investment-visualization/references/review.md",
    "skills/investment-research/SKILL.md",
  ].map((file) => readFile(new URL(file, root), "utf8")));
  assert.match(skill, /This skill owns visual judgment/);
  assert.match(skill, /quality bar, not a request to reproduce another publisher's brand/);
  assert.match(skill, /Render the actual report in a browser and inspect it at desktop and narrow-phone widths/);
  assert.match(grammar, /The visual sentence/);
  assert.match(grammar, /Show probability evidence separately from payoff/);
  assert.match(review, /Five-second test/);
  assert.match(review, /Final decision test/);
  assert.match(researchSkill, /use the `investment-visualization` skill/);
});
