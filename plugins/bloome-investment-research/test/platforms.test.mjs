import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";

const pluginRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repositoryRoot = path.resolve(pluginRoot, "../..");

async function json(relativeTo, file) {
  return JSON.parse(await readFile(path.join(relativeTo, file), "utf8"));
}

test("canonical config drives matching Codex and Claude Code identities", async () => {
  const [config, codex, claude, packageJson] = await Promise.all([
    json(pluginRoot, "plugin.config.json"),
    json(pluginRoot, ".codex-plugin/plugin.json"),
    json(pluginRoot, ".claude-plugin/plugin.json"),
    json(pluginRoot, "package.json"),
  ]);
  for (const manifest of [codex, claude, packageJson]) {
    assert.equal(manifest.name, config.name);
    assert.equal(manifest.version, config.version);
  }
  assert.equal(codex.mcpServers, "./.mcp.json");
  assert.equal(claude.mcpServers, "./.mcp.json");
  assert.equal(codex.skills, "./skills/");
  assert.equal(claude.skills, "./skills/");
  assert.ok(codex.interface);
  assert.equal(claude.interface, undefined);
});

test("both marketplaces resolve to the same shared plugin directory", async () => {
  const [config, codex, claude] = await Promise.all([
    json(pluginRoot, "plugin.config.json"),
    json(repositoryRoot, ".agents/plugins/marketplace.json"),
    json(repositoryRoot, ".claude-plugin/marketplace.json"),
  ]);
  assert.equal(codex.name, config.marketplace.name);
  assert.equal(claude.name, config.marketplace.name);
  assert.equal(codex.plugins[0].source.path, `./plugins/${config.name}`);
  assert.equal(claude.plugins[0].source, `./plugins/${config.name}`);
  assert.equal(claude.plugins[0].version, config.version);
});

test("portable MCP launcher selects plugin root in either host", async () => {
  const mcp = await json(pluginRoot, ".mcp.json");
  const launcher = mcp.mcpServers.bloomeInvestmentResearch;
  assert.equal(launcher.command, "node");
  assert.deepEqual(launcher.args.slice(0, 1), ["-e"]);
  assert.match(launcher.args[1], /CLAUDE_PLUGIN_ROOT/);
  assert.match(launcher.args[1], /process\.cwd/);
  assert.match(launcher.args[1], /runStdio/);
  assert.deepEqual(launcher.env_vars, ["BLOOME_FINANCE_URL"]);
});

test("GildData market data bridge uses the same portable launcher pattern", async () => {
  const mcp = await json(pluginRoot, ".mcp.json");
  const launcher = mcp.mcpServers.gilddataMarketData;
  assert.equal(launcher.command, "node");
  assert.deepEqual(launcher.args.slice(0, 1), ["-e"]);
  assert.match(launcher.args[1], /CLAUDE_PLUGIN_ROOT/);
  assert.match(launcher.args[1], /process\.cwd/);
  assert.match(launcher.args[1], /gilddata-bridge/);
  assert.match(launcher.args[1], /runStdio/);
  assert.deepEqual(launcher.env_vars, ["GILDDATA_API_TOKEN", "GILDDATA_MCP_URL"]);

  const bridge = await import(pathToFileURL(path.join(pluginRoot, "mcp", "gilddata-bridge.cjs")).href);
  const env = { GILDDATA_API_TOKEN: "test-token" };
  assert.match(bridge.default.endpointUrl(env), /^https:\/\/api\.gildata\.com\/.*\?token=test-token$/);
  assert.throws(() => bridge.default.endpointUrl({ GILDDATA_API_TOKEN_FILE: "/nonexistent" }), /credential is required/);
});

test("market-data skill scopes GildData to follow-up lookups outside the research workflow", async () => {
  const [skill, researchSkill] = await Promise.all([
    readFile(path.join(pluginRoot, "skills/market-data/SKILL.md"), "utf8"),
    readFile(path.join(pluginRoot, "skills/investment-research/SKILL.md"), "utf8"),
  ]);
  assert.match(skill, /^---\nname: market-data\ndescription: /);
  assert.match(skill, /gilddataMarketData/);
  assert.match(skill, /never write them into `evidence\.json`/);
  assert.match(skill, /consume no Bloome research credit/);
  assert.match(researchSkill, /`market-data` skill via the `gilddataMarketData` tools/);
});

test("portable MCP declaration boots and initializes in both host environments", async () => {
  const [config, mcp] = await Promise.all([
    json(pluginRoot, "plugin.config.json"),
    json(pluginRoot, ".mcp.json"),
  ]);
  const launcher = mcp.mcpServers.bloomeInvestmentResearch;
  const input = `${JSON.stringify({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: { protocolVersion: "2024-11-05" },
  })}\n`;

  for (const runtime of ["codex", "claude-code"]) {
    const env = { ...process.env };
    if (runtime === "claude-code") env.CLAUDE_PLUGIN_ROOT = pluginRoot;
    else delete env.CLAUDE_PLUGIN_ROOT;
    const cwd = runtime === "codex" ? pluginRoot : repositoryRoot;
    const child = spawnSync(launcher.command, launcher.args, { cwd, env, input, encoding: "utf8", timeout: 5_000 });
    assert.equal(child.status, 0, child.stderr || `${runtime} MCP process failed`);
    const response = JSON.parse(child.stdout.trim());
    assert.equal(response.result.serverInfo.version, config.version);
    assert.match(response.result.instructions, runtime === "codex" ? /Codex/ : /Claude Code/);
  }
});
