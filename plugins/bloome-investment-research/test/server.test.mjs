import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const server = require("../mcp/server.cjs");

async function fixtureWorkspace() {
  const root = await mkdtemp(path.join(os.tmpdir(), "bloome-research-test-"));
  const evidence = [
    { claim:"需求扩张",claim_ids:["C1"],relation:"support",stance:"support",kind:"fact",corpus:"sell",chunk_id:"s1",report_id:"sr1",quote:"需求增长",source_type:"sell-side",title:"NAND Market Outlook",source_path:"sell/report.pdf",page_start:1,published_at:"2026-07-01" },
    { claim:"交付约束",claim_ids:["C1"],relation:"challenge",stance:"challenge",kind:"fact",corpus:"primary",chunk_id:"p1",report_id:"pr1",quote:"交付仍受约束",source_type:"interview",title:"Industry Interview",source_path:"primary/interview.txt",line_start:2,line_end:3,published_at:"2026-07-02" },
  ];
  const coverage = {
    retrieval_rounds: [{ corpus:"sell" }, { corpus:"primary" }],
    query_seeds:["AI NAND demand", "NAND delivery constraints"],
    stopping_reason:"Additional searches repeated the same claims and did not close the remaining company-level gap.",
    remaining_gaps:["Company product mix"],
    sell_reports_retrieved:3, primary_sources_retrieved:2,
    sell_reports_read:2, primary_sources_read:2,
  };
  const sectionTitles = ["Executive judgment", "Causal mechanism"];
  const chapters = Array.from({ length:sectionTitles.length }, (_, offset) => {
    const index = offset + 1;
    return [
      `# S${String(index).padStart(2,"0")} ${sectionTitles[offset]}`,
      "",
      "## Evidence and transmission",
      "",
      `第${index}章保留完整的论证链：需求扩张先改变订单能见度，再通过库存与供给纪律影响价格弹性；这个判断以可追溯数据为基础，而不是把行业常识当作证据。[NAND Market Outlook, p.1]`,
      "",
      "还需要把公司层面的产品组合、资本开支节奏和客户验证周期放回同一个测算框架，避免只用单一总量指标推导盈利结果。情景测算必须说明假设变化如何传导到收入、利润率和估值区间。",
      "",
      "## Boundary and opposing evidence",
      "",
      "边界条件是交付约束可能让需求信号晚于预期兑现，且独立产业访谈仍显示供应链存在不确定性。[Industry Interview, lines 2-3] 如果后续订单、库存或报价没有按时间窗口改善，应下调判断强度而不是删除反方证据。",
    ].join("\n");
  });
  const visualPlans = [
    { id:"V01",role:"mechanism",title:"Demand-to-earnings transmission",form:"diagram" },
  ];
  const outline = sectionTitles.map((title, index) => {
    const visual = visualPlans[index];
    return [`# S${String(index + 1).padStart(2,"0")} ${title}`, "Purpose: Test purpose", "Claims: C1", visual ? `- Visual: ${visual.id} | ${visual.role} | ${visual.title} | ${visual.form}` : ""].filter(Boolean).join("\n");
  }).join("\n\n");
  const finalReport = chapters.join("\n\n");
  const visibleCitations = chapters.map(() => `<span class="src">NAND Market Outlook<span class="tip"><u>需求增长</u></span></span><blockquote class="primary-quote">交付仍受约束<cite>Industry Interview · 2026-07-02</cite></blockquote>`).join("");
  const figures = visualPlans.map((visual) => `<figure data-visual-id="${visual.id}" data-visual-role="${visual.role}" data-visual-title="${visual.title}" data-visual-source="s1 p1" aria-label="${visual.title}"><h3>${visual.title}</h3><svg role="img" aria-label="${visual.title}"></svg><div class="chart-source">NAND Market Outlook</div></figure>`);
  const reportSections = chapters.map((chapter, index) => `<section data-section-id="S${String(index + 1).padStart(2,"0")}">${chapter}${figures[index] || ""}</section>`).join("");
  const html = `<!doctype html><html><body><div class="report-shell"><nav class="report-tabs"><button data-report-tab="report">研报</button><button data-report-tab="evidence">证据</button></nav><section data-report-panel="report"><div class="report"><div class="top-bar"></div><div class="header"><div class="header-title">NAND cycle</div><div class="header-meta">2026年7月</div></div><div class="section judge-box">${reportSections}${visibleCitations}</div><div class="source-bar">Sources</div><div class="bottom-bar"></div></div></section><section data-report-panel="evidence" hidden><div data-evidence-section="sell-side-logic"><article class="logic-item" data-claim-id="C1" data-logic-claim-id="C1">需求扩张</article></div><div data-evidence-section="validation"><article class="validation-item" data-claim-id="C1" data-validation-claim-id="C1"><span data-validation-field="support">需求增长</span><span data-validation-field="opposing">交付约束</span><span data-validation-field="calibration">谨慎校准</span><span data-validation-field="unverified">价格传导</span><span data-validation-field="strength">medium</span><span data-validation-field="falsifier">库存回升</span></article></div><div data-evidence-section="ledger"><article class="evidence-entry" data-evidence-id="s1" data-evidence-claim-ids="C1" data-relation="support">需求增长</article><article class="evidence-entry" data-evidence-id="p1" data-evidence-claim-ids="C1" data-relation="challenge">交付约束</article></div></section></div><script>document.querySelectorAll('[data-report-tab]').forEach((tab)=>tab.addEventListener('click',()=>{}));</script></body></html>`;
  const moduleMemo = [
    "# Direct answer", "需求与供给纪律共同决定周期弹性。[NAND Market Outlook, p.1]",
    "# Claim–evidence pairs", "产业访谈对交付节奏构成反方校准。[Industry Interview, lines 2-3]",
    "# Metrics", "跟踪订单、库存、报价和资本开支，并保留每个数字的原始定位。",
    "# Conflicts and date reconciliation", "同一证据链采用较新日期，独立来源的分歧继续保留。",
    "# Invalidating conditions", "若订单和报价未在验证窗口改善，则需求传导假设失效。",
    "# Remaining gaps", "公司产品组合与客户验证节奏仍需更多独立来源。",
  ].join("\n\n");
  const modules = ["demand"].map((id, index) => ({
    id,
    question:`question ${index}`,
    scope:`scope ${index}`,
    support_hypothesis:`support ${index}`,
    challenge_hypothesis:`challenge ${index}`,
    evidence_needed:[`evidence ${index}`],
    query_seeds:[`query ${index}`],
  }));
  const files = {
    "state.json": JSON.stringify({ topic:"AI 与 NAND",current_judgment:"需求和供给共同进入验证期。" }),
    "plan.json": JSON.stringify({ topic:"AI 与 NAND",modules }),
    "sell_side_logic.md":"# Logic\n\n## C1 需求扩张\n",
    "validation.md":"# Validation\n\n## C1 需求扩张\n",
    "report_outline.md":outline,
    "final_report.md":finalReport,
    "report.md":finalReport,
    "report.html":html,
    "evidence.json":JSON.stringify(evidence),
    "coverage_stats.json":JSON.stringify(coverage),
  };
  for (const module of modules) files[`modules/${module.id}.md`]=moduleMemo;
  for (const [index, chapter] of chapters.entries()) files[`chapter_${String(index + 1).padStart(2,"0")}_section.md`]=chapter;
  await mkdir(path.join(root, "modules"));
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

test("research proxy reads the user credential file when the host filters environment variables", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bloome-credential-test-"));
  const file = path.join(root, "token");
  const previousToken = process.env.RESEARCH_API_TOKEN;
  const previousFile = process.env.RESEARCH_API_TOKEN_FILE;
  delete process.env.RESEARCH_API_TOKEN;
  process.env.RESEARCH_API_TOKEN_FILE = file;
  await writeFile(file, "file-token\n");
  try {
    let authorization;
    await server.researchProxy("/search", { corpus:"sell" }, undefined, async (_url, options) => {
      authorization = options.headers.authorization;
      return { ok:true, text:async()=>"{}" };
    });
    assert.equal(authorization, "Bearer file-token");
  } finally {
    if (previousToken === undefined) delete process.env.RESEARCH_API_TOKEN;
    else process.env.RESEARCH_API_TOKEN = previousToken;
    if (previousFile === undefined) delete process.env.RESEARCH_API_TOKEN_FILE;
    else process.env.RESEARCH_API_TOKEN_FILE = previousFile;
  }
});

test("research proxy fails clearly when the beta credential is missing", async () => {
  const previousToken = process.env.RESEARCH_API_TOKEN;
  const previousFile = process.env.RESEARCH_API_TOKEN_FILE;
  delete process.env.RESEARCH_API_TOKEN;
  process.env.RESEARCH_API_TOKEN_FILE = path.join(os.tmpdir(), `missing-bloome-token-${process.pid}`);
  try {
    await assert.rejects(() => server.researchProxy("/search", { corpus:"sell" }), /set RESEARCH_API_TOKEN or ~\/\.bloome\/research-api-token/);
  } finally {
    if (previousToken !== undefined) process.env.RESEARCH_API_TOKEN = previousToken;
    else delete process.env.RESEARCH_API_TOKEN;
    if (previousFile !== undefined) process.env.RESEARCH_API_TOKEN_FILE = previousFile;
    else delete process.env.RESEARCH_API_TOKEN_FILE;
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
  assert.equal(result.chapters, 2);
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

test("workspace validator requires claim-linked evidence", async () => {
  const workspace = await fixtureWorkspace();
  const evidencePath = path.join(workspace, "evidence.json");
  const evidence = JSON.parse(await readFile(evidencePath, "utf8"));
  delete evidence[0].claim_ids;
  await writeFile(evidencePath, JSON.stringify(evidence));
  const result = await server.validateWorkspace(workspace);
  assert.equal(result.ok, false);
  assert.ok(result.errors.includes("s1: missing claim_ids"));
});

test("workspace validator rejects shallow module and chapter artifacts", async () => {
  const workspace = await fixtureWorkspace();
  await writeFile(path.join(workspace, "modules/demand.md"), "# Direct answer\n");
  await writeFile(path.join(workspace, "chapter_01_section.md"), "# Chapter\n\nA title without evidence.\n");
  const result = await server.validateWorkspace(workspace);
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((error) => /module demand is title-only/.test(error)));
  assert.ok(result.errors.some((error) => /chapter_01_section\.md requires at least one exact source citation/.test(error)));
  assert.ok(result.errors.some((error) => /chapter_01_section\.md requires an explicit boundary/.test(error)));
});

test("workspace validator allows final synthesis to rewrite chapter drafts", async () => {
  const workspace = await fixtureWorkspace();
  const synthesis = await readFile(path.join(workspace, "chapter_01_section.md"), "utf8");
  await Promise.all([
    writeFile(path.join(workspace, "final_report.md"), synthesis),
    writeFile(path.join(workspace, "report.md"), synthesis),
  ]);
  const result = await server.validateWorkspace(workspace);
  assert.equal(result.ok, true, result.errors.join("\n"));
});

test("workspace validator rejects summary HTML that omits the Markdown report", async () => {
  const workspace = await fixtureWorkspace();
  const summaryHtml = `<!doctype html><html><body><div class="report"><div class="top-bar"></div><div class="header"><div class="header-title">Summary</div><div class="header-meta">2026年7月</div></div><div class="section judge-box">Only a short dashboard summary.</div><div class="source-bar">Sources</div><div class="bottom-bar"></div></div></body></html>`;
  await writeFile(path.join(workspace, "report.html"), summaryHtml);
  const result = await server.validateWorkspace(workspace);
  assert.equal(result.ok, false);
  assert.ok(result.errors.some((error) => /HTML omits report\.md narrative/.test(error)));
});

test("workspace validator binds HTML sections and visuals to the approved outline", async () => {
  const workspace = await fixtureWorkspace();
  const htmlPath = path.join(workspace, "report.html");
  const html = await readFile(htmlPath, "utf8");
  await writeFile(htmlPath, html.replace('data-section-id="S02"', 'data-section-id="S05"').replace('data-visual-id="V01"', 'data-visual-id="missing"'));
  const result = await server.validateWorkspace(workspace);
  assert.equal(result.ok, false);
  assert.ok(result.errors.includes("HTML report sections must match outline IDs and order exactly"));
  assert.ok(result.errors.includes("HTML must render visual V01 exactly once"));
});

test("workspace validator allows a topic with no useful visual", async () => {
  const workspace = await fixtureWorkspace();
  const outlinePath = path.join(workspace, "report_outline.md");
  const htmlPath = path.join(workspace, "report.html");
  const outline = await readFile(outlinePath, "utf8");
  const html = await readFile(htmlPath, "utf8");
  await Promise.all([
    writeFile(outlinePath, outline.replace(/^- Visual:.*\n?/m, "")),
    writeFile(htmlPath, html.replace(/<figure\b[\s\S]*?<\/figure>/, "")),
  ]);
  const result = await server.validateWorkspace(workspace);
  assert.equal(result.ok, true, result.errors.join("\n"));
});

test("widget resource is a self-contained MCP app with real Bloome assets", async () => {
  const html = server.resourceText();
  assert.match(html, /data:image\/svg\+xml;base64,/);
  assert.match(html, /--blue:#2556b6/);
  assert.match(html, /window\.openai\?\.callTool/);
  assert.match(html, /The plugin shell does not restyle this document/);
  assert.doesNotMatch(html, /\{\{BLOOME_WORDMARK\}\}/);
});
