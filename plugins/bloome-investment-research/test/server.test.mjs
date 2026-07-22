import assert from "node:assert/strict";
import { mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const server = require("../mcp/server.cjs");

async function fixtureWorkspace() {
  const root = await mkdtemp(path.join(os.tmpdir(), "bloome-research-test-"));
  const report = [
    "# NAND cycle",
    "",
    "需求扩张与供给纪律共同决定价格弹性。[NAND Market Outlook, p.1]",
    "",
    "产业访谈验证交付仍受约束。[Industry Interview, lines 2-3]",
  ].join("\n");
  const evidence = [
    { claim:"需求扩张",stance:"support",kind:"fact",corpus:"sell",chunk_id:"s1",report_id:"sr1",quote:"需求增长",source_type:"sell-side",title:"NAND Market Outlook",source_path:"sell/report.pdf",page_start:1,published_at:"2026-07-01" },
    { claim:"交付约束",stance:"challenge",kind:"fact",corpus:"primary",chunk_id:"p1",report_id:"pr1",quote:"交付仍受约束",source_type:"interview",title:"Industry Interview",source_path:"primary/interview.txt",line_start:2,line_end:3,published_at:"2026-07-02" },
  ];
  const html = `<!doctype html><html><body><div class="report-shell"><nav class="report-tabs"><button data-report-tab="report">研报</button><button data-report-tab="evidence">证据</button></nav><section data-report-panel="report"><div class="report"><div class="top-bar"></div><div class="header"><div class="header-title">NAND cycle</div><div class="header-meta">2026年7月</div></div><div class="section judge-box"><span class="src">NAND Market Outlook<span class="tip"><u>需求增长</u></span></span><blockquote class="primary-quote">交付仍受约束<cite>Industry Interview · 2026-07-02</cite></blockquote></div><div class="source-bar">Sources</div><div class="bottom-bar"></div></div></section><section data-report-panel="evidence" hidden><div data-evidence-section="sell-side-logic"><article class="logic-item" data-claim-id="C1" data-logic-claim-id="C1">需求扩张</article></div><div data-evidence-section="validation"><article class="validation-item" data-claim-id="C1" data-validation-claim-id="C1"><span data-validation-field="support">需求增长</span><span data-validation-field="opposing">交付约束</span><span data-validation-field="calibration">谨慎校准</span><span data-validation-field="unverified">价格传导</span><span data-validation-field="strength">medium</span><span data-validation-field="falsifier">库存回升</span></article></div><div data-evidence-section="ledger"><article class="evidence-entry" data-evidence-id="s1">需求增长</article><article class="evidence-entry" data-evidence-id="p1">交付约束</article></div></section></div><script>document.querySelectorAll('[data-report-tab]').forEach((tab)=>tab.addEventListener('click',()=>{}));</script></body></html>`;
  const coverage = {
    retrieval_rounds: [
      { corpus:"sell",published_from:null }, { corpus:"sell",published_from:"2026-01-01" },
      { corpus:"primary",published_from:null }, { corpus:"primary",published_from:"2026-01-01" },
    ],
    sell_reports_retrieved:40, primary_sources_retrieved:40,
    sell_reports_read:8, primary_sources_read:7,
  };
  const files = {
    "state.json": JSON.stringify({ topic:"AI 与 NAND",current_judgment:"需求和供给共同进入验证期。" }),
    "plan.json": JSON.stringify({ topic:"AI 与 NAND",modules:[{ id:"demand",question:"需求传导",scope:"AI 推理负载" }] }),
    "sell_side_logic.md":"# Logic\n\n## C1 需求扩张\n",
    "validation.md":"# Validation\n\n## C1 需求扩张\n",
    "report_outline.md":"# Outline\n",
    "final_report.md":report,
    "report.md":report,
    "report.html":html,
    "evidence.json":JSON.stringify(evidence),
    "coverage_stats.json":JSON.stringify(coverage),
  };
  for (let index=1; index<=5; index++) files[`chapter_${String(index).padStart(2,"0")}_section.md`]=`# Chapter ${index}\n`;
  await Promise.all(Object.entries(files).map(([name, content]) => writeFile(path.join(root, name), content)));
  return root;
}

test("MCP initializes and exposes the five focused tools", async () => {
  const initialized = await server.handleRpc({ jsonrpc:"2.0",id:1,method:"initialize",params:{} }, "codex");
  const listed = await server.handleRpc({ jsonrpc:"2.0",id:2,method:"tools/list",params:{} }, "codex");
  assert.equal(initialized.result.serverInfo.name, "bloome-investment-research");
  assert.deepEqual(listed.result.tools.map((tool) => tool.name), [
    "research_search", "research_get_chunk", "research_get_report_context", "open_research_workspace", "validate_research_workspace",
  ]);
});

test("runtime profiles keep host-specific presentation out of the shared research core", async () => {
  const codex = await server.handleRpc({ jsonrpc:"2.0",id:1,method:"tools/list",params:{} }, "codex");
  const claude = await server.handleRpc({ jsonrpc:"2.0",id:2,method:"tools/list",params:{} }, "claude-code");
  const codexOpen = codex.result.tools.find((tool) => tool.name === "open_research_workspace");
  const claudeOpen = claude.result.tools.find((tool) => tool.name === "open_research_workspace");
  assert.ok(codexOpen._meta);
  assert.equal(claudeOpen._meta, undefined);
  assert.match(claudeOpen.description, /reportPath/);

  const claudeInit = await server.handleRpc({ jsonrpc:"2.0",id:3,method:"initialize",params:{} }, "claude-code");
  assert.equal(claudeInit.result.capabilities.resources, undefined);
  assert.match(claudeInit.result.instructions, /Claude Code/);
});

test("research proxy uses the beta endpoint with an environment credential", async () => {
  let request;
  const previous = process.env.RESEARCH_API_TOKEN;
  process.env.RESEARCH_API_TOKEN = "test-token";
  try {
    const payload = await server.researchProxy("/search", { corpus:"sell",size:2 }, undefined, async (url, options) => {
      request = { url, options };
      return { ok:true, text:async()=>JSON.stringify({ reports:[{ report_id:"1" }] }) };
    });
    assert.equal(request.url, "https://research-search-proxy.dev-0da.workers.dev/search");
    assert.equal(request.options.headers.authorization, "Bearer test-token");
    assert.equal(payload.reports[0].report_id, "1");
  } finally {
    if (previous === undefined) delete process.env.RESEARCH_API_TOKEN;
    else process.env.RESEARCH_API_TOKEN = previous;
  }
});

test("research proxy fails clearly when the beta credential is missing", async () => {
  const previous = process.env.RESEARCH_API_TOKEN;
  delete process.env.RESEARCH_API_TOKEN;
  try {
    await assert.rejects(() => server.researchProxy("/search", { corpus:"sell" }), /RESEARCH_API_TOKEN is required/);
  } finally {
    if (previous !== undefined) process.env.RESEARCH_API_TOKEN = previous;
  }
});

test("workspace snapshot drives progress, evidence, and native report preview", async () => {
  const workspace = await fixtureWorkspace();
  const snapshot = server.buildSnapshot(workspace);
  assert.equal(snapshot.topic, "AI 与 NAND");
  assert.equal(snapshot.progress, 100);
  assert.equal(snapshot.stage, 5);
  assert.equal(snapshot.evidence.length, 2);
  assert.match(snapshot.reportHtml, /class="report"/);
  assert.equal(snapshot.reportPath, path.join(workspace, "report.html"));
});

test("Claude Code workspace response returns paths without injecting report HTML", async () => {
  const workspace = await fixtureWorkspace();
  const snapshot = await server.callTool("open_research_workspace", { workspace }, "claude-code");
  assert.equal(snapshot.runtime, "claude-code");
  assert.equal(snapshot.workbenchAvailable, false);
  assert.equal(snapshot.reportHtml, undefined);
  assert.equal(snapshot.reportPath, path.join(workspace, "report.html"));
});

test("workspace validator enforces all staged and report contracts", async () => {
  const workspace = await fixtureWorkspace();
  const result = await server.validateWorkspace(workspace);
  assert.equal(result.ok, true, result.errors.join("\n"));
  assert.equal(result.chapters, 5);
});

test("workspace validator rejects an incomplete embedded evidence ledger", async () => {
  const workspace = await fixtureWorkspace();
  const htmlPath = path.join(workspace, "report.html");
  const html = await readFile(htmlPath, "utf8");
  await writeFile(htmlPath, html.replace('data-evidence-id="p1"', 'data-evidence-id="missing"'));
  const result = await server.validateWorkspace(workspace);
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((error) => error.includes("chunk_id p1")));
});

test("workspace validator requires every staged validation claim in the evidence tab", async () => {
  const workspace = await fixtureWorkspace();
  const htmlPath = path.join(workspace, "report.html");
  const html = await readFile(htmlPath, "utf8");
  await writeFile(htmlPath, html.replace('data-validation-claim-id="C1"', 'data-validation-claim-id="C2"'));
  const result = await server.validateWorkspace(workspace);
  assert.equal(result.ok, false);
  assert.ok(result.errors.includes("Validation claim C1 must be rendered exactly once"));
});

test("widget resource is a self-contained MCP app with real Bloome assets", async () => {
  const html = server.resourceText();
  assert.match(html, /data:image\/svg\+xml;base64,/);
  assert.match(html, /--blue:#2556b6/);
  assert.match(html, /window\.openai\?\.callTool/);
  assert.match(html, /The plugin shell does not restyle this document/);
  assert.doesNotMatch(html, /\{\{BLOOME_WORDMARK\}\}/);
});
