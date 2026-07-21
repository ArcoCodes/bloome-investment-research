import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

const root = new URL("../", import.meta.url);
const contracts = new Map([
  ["skills/investment-research/assets/template.html", "5733718e3383408b26ba6b67f96113b442b8a966ffadbc19d6cd96a1d109434c"],
  ["skills/investment-research/references/file-specs.md", "952e6eca97b87ad6e66c943c2ccaf0f31659d62e1e7b0229ee3dec44ea639aab"],
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
  assert.match(skill, /Use `assets\/template\.html` as the visual source of truth/);
  assert.match(skill, /Do not replace it with a newly invented card layout/);
  assert.match(skill, /evidence\.json` as the unified evidence backbone/);
  assert.match(skill, /Codex or Claude Code/);
  assert.match(skill, /host's existing account supplies the model/);
  assert.match(skill, /returned `reportPath`/);
});
