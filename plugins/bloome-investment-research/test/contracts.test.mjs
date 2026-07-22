import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const root = new URL("../", import.meta.url);
const contracts = new Map([
  ["skills/investment-research/assets/template.html", "485aa53489e01ea0aef1d1eb38a37025c83daacd98a8d911567c29bbd0175d9d"],
  ["skills/investment-research/references/file-specs.md", "57b5e1a94d7ec1f9341cc709f460863658f968eff0a6cb5cccd2114088c97b81"],
  ["skills/investment-research/references/chart-rules.md", "06227979f4a4374ace20ade380f0cd6d5cdbe6b36f32ad5f4ce2e973ab84e32d"],
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
  assert.match(skill, /Codex or Claude Code/);
  assert.match(skill, /host's existing account supplies the model/);
  assert.match(skill, /returned `reportPath`/);
  assert.match(skill, /same self-contained `report\.html`/);
  assert.match(template, /data-report-tab="report"/);
  assert.match(template, /data-report-tab="evidence"/);
  assert.match(template, /data-evidence-section="validation"/);
  assert.match(template, /data-evidence-id="\{\{evidence_chunk_id\}\}"/);
});
