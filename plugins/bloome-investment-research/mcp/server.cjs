"use strict";

const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline");
const { pathToFileURL } = require("node:url");

const ROOT = path.resolve(__dirname, "..");
const MANIFEST = JSON.parse(fs.readFileSync(path.join(ROOT, ".codex-plugin", "plugin.json"), "utf8"));
const SERVER_NAME = "bloome-investment-research";
const SERVER_VERSION = MANIFEST.version;
const WIDGET_URI = `ui://widget/bloome-research-${encodeURIComponent(SERVER_VERSION)}.html`;
const WIDGET_MIME = "text/html;profile=mcp-app";
const DEFAULT_SEARCH_URL = "https://research-search-proxy.dev-0da.workers.dev";
const REQUIRED_FILES = [
  "sell_side_logic.md",
  "validation.md",
  "final_report.md",
  "report.md",
  "report.html",
  "evidence.json",
  "coverage_stats.json",
];

const SEARCH_PROPERTIES = {
  corpus: { type: "string", enum: ["sell", "primary"] },
  concepts: {
    type: "array",
    maxItems: 8,
    items: { type: "array", minItems: 1, maxItems: 6, items: { type: "string", minLength: 1, maxLength: 120 } },
  },
  phrases: { type: "array", maxItems: 8, items: { type: "string", minLength: 1, maxLength: 160 } },
  tickers: { type: "array", maxItems: 20, items: { type: "string", minLength: 1, maxLength: 120 } },
  institutions: { type: "array", maxItems: 20, items: { type: "string", minLength: 1, maxLength: 120 } },
  source_types: { type: "array", maxItems: 20, items: { type: "string", minLength: 1, maxLength: 120 } },
  published_from: { type: "string", pattern: "^20\\d{2}-\\d{2}-\\d{2}$" },
  size: { type: "integer", minimum: 1, maximum: 20 },
  chunks_per_report: { type: "integer", minimum: 1, maximum: 3 },
};

function objectSchema(properties, required = []) {
  return { type: "object", properties, required, additionalProperties: false };
}

function widgetMeta(visibility = ["model", "app"]) {
  return {
    ui: { resourceUri: WIDGET_URI, visibility },
    "ui/resourceUri": WIDGET_URI,
    "openai/outputTemplate": WIDGET_URI,
    "openai/widgetAccessible": true,
    "openai/toolInvocation/invoking": "Opening research workbench",
    "openai/toolInvocation/invoked": "Research workbench ready",
  };
}

function tool(name, title, description, inputSchema, options = {}) {
  const definition = {
    name,
    title,
    description,
    inputSchema,
    annotations: {
      readOnlyHint: true,
      destructiveHint: false,
      idempotentHint: true,
      openWorldHint: Boolean(options.openWorld),
    },
  };
  if (options.widget) definition._meta = widgetMeta();
  return definition;
}

function toolDefinitions() {
  return [
    tool(
      "research_search",
      "Search investment research",
      "Search the controlled sell-side or primary research corpus. Run at least two rounds per corpus, including a recency-filtered round, and retrieve at least 40 records per corpus before synthesis.",
      objectSchema(SEARCH_PROPERTIES, ["corpus"]),
      { openWorld: true },
    ),
    tool(
      "research_get_chunk",
      "Read an exact source chunk",
      "Fetch one source chunk with its exact locator for evidence traceability.",
      objectSchema(
        {
          corpus: { type: "string", enum: ["sell", "primary"] },
          chunk_id: { type: "string", minLength: 1, maxLength: 80 },
        },
        ["corpus", "chunk_id"],
      ),
      { openWorld: true },
    ),
    tool(
      "research_get_report_context",
      "Read surrounding report context",
      "Fetch ordered chunks around a known location in one report.",
      objectSchema(
        {
          corpus: { type: "string", enum: ["sell", "primary"] },
          report_id: { type: "string", minLength: 1 },
          chunk_no: { type: "integer", minimum: 1 },
          radius: { type: "integer", minimum: 0, maximum: 4 },
        },
        ["corpus", "report_id"],
      ),
      { openWorld: true },
    ),
    tool(
      "open_research_workspace",
      "Open Bloome Research",
      "Render the Bloome research workbench for an absolute project workspace path. The report preview preserves the investment skill's native report HTML.",
      objectSchema({ workspace: { type: "string", minLength: 1 } }, ["workspace"]),
      { widget: true },
    ),
    tool(
      "validate_research_workspace",
      "Validate investment report",
      "Validate required staged files, evidence traceability, source coverage, chapter depth, and the native report template contract.",
      objectSchema({ workspace: { type: "string", minLength: 1 } }, ["workspace"]),
    ),
  ];
}

async function researchProxy(endpoint, body, signal, fetcher = fetch) {
  const base = (process.env.RESEARCH_SEARCH_URL || DEFAULT_SEARCH_URL).replace(/\/$/, "");
  const token = process.env.RESEARCH_API_TOKEN;
  if (!token) throw new Error("RESEARCH_API_TOKEN is required; add it to ~/.codex/.env and restart Codex");
  let response;
  try {
    response = await fetcher(`${base}${endpoint}`, {
      method: "POST",
      headers: { authorization: `Bearer ${token}`, "content-type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new Error("Research proxy unavailable");
  }
  const text = await response.text();
  if (!response.ok) {
    let message = "request failed";
    try { message = String(JSON.parse(text).error || message); } catch {}
    throw new Error(`Research proxy ${response.status}: ${message.slice(0, 300)}`);
  }
  try { return JSON.parse(text); }
  catch { throw new Error("Research proxy returned invalid JSON"); }
}

function readText(file, fallback = "") {
  try { return fs.readFileSync(file, "utf8"); }
  catch (error) { if (error.code === "ENOENT") return fallback; throw error; }
}

function readJson(file, fallback) {
  const text = readText(file);
  if (!text) return fallback;
  try { return JSON.parse(text); }
  catch { return fallback; }
}

function workspacePath(value) {
  if (!value || !path.isAbsolute(value)) throw new Error("workspace must be an absolute path");
  return path.resolve(value);
}

function firstParagraph(markdown) {
  return markdown
    .split(/\n\s*\n/)
    .map((part) => part.replace(/^#+\s+.*$/gm, "").trim())
    .find(Boolean) || "等待形成可验证的核心判断。";
}

function artifactList(root) {
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root)
    .filter((name) => name === "plan.json" || REQUIRED_FILES.includes(name) || /^report_outline\.(md|json)$/.test(name) || /^chapter_\d+_.*\.md$/.test(name))
    .sort()
    .map((name) => ({ name, bytes: fs.statSync(path.join(root, name)).size }));
}

function stageFromArtifacts(names) {
  if (names.has("report.html")) return 5;
  if (names.has("validation.md")) return 4;
  if (names.has("sell_side_logic.md")) return 3;
  if (names.has("plan.json") || names.has("report_outline.md") || names.has("report_outline.json")) return 2;
  return 1;
}

function buildSnapshot(workspace) {
  const root = workspacePath(workspace);
  const state = readJson(path.join(root, "state.json"), {});
  const plan = readJson(path.join(root, "plan.json"), {});
  const evidence = readJson(path.join(root, "evidence.json"), []);
  const coverage = readJson(path.join(root, "coverage_stats.json"), {});
  const reportHtml = readText(path.join(root, "report.html"));
  const report = readText(path.join(root, "final_report.md"), readText(path.join(root, "report.md")));
  const artifacts = artifactList(root);
  const names = new Set(artifacts.map((item) => item.name));
  const stage = stageFromArtifacts(names);
  const chapterCount = artifacts.filter((item) => item.name.startsWith("chapter_")).length;
  const requiredReady = REQUIRED_FILES.filter((name) => names.has(name)).length +
    (names.has("report_outline.md") || names.has("report_outline.json") ? 1 : 0);
  const progress = Math.min(100, Math.round((requiredReady / 8) * 85 + Math.min(chapterCount, 5) * 3));
  return {
    ok: true,
    workspace: root,
    topic: state.topic || plan.topic || path.basename(root),
    status: reportHtml ? "ready" : stage >= 3 ? "researching" : "planning",
    progress,
    stage,
    judgment: state.current_judgment || firstParagraph(report),
    modules: Array.isArray(plan.modules) ? plan.modules : [],
    artifacts,
    evidence: Array.isArray(evidence) ? evidence.slice(0, 100) : [],
    coverage,
    chapterCount,
    reportHtml,
  };
}

function coverageErrors(coverage) {
  const errors = [];
  const rounds = Array.isArray(coverage.retrieval_rounds) ? coverage.retrieval_rounds : [];
  for (const corpus of ["sell", "primary"]) {
    const corpusRounds = rounds.filter((round) => round.corpus === corpus);
    if (corpusRounds.length < 2) errors.push(`${corpus} requires at least 2 retrieval rounds`);
    const countKey = corpus === "sell" ? "sell_reports_retrieved" : "primary_sources_retrieved";
    if (Number(coverage[countKey] || 0) < 40) errors.push(`${corpus} requires at least 40 retrieved records`);
    if (!corpusRounds.some((round) => round.published_from)) errors.push(`${corpus} requires a recency-filtered retrieval round`);
  }
  return errors;
}

async function validateWorkspace(workspace) {
  const root = workspacePath(workspace);
  const artifacts = artifactList(root);
  const names = new Set(artifacts.map((item) => item.name));
  const errors = REQUIRED_FILES.filter((name) => !names.has(name)).map((name) => `missing ${name}`);
  if (!names.has("report_outline.md") && !names.has("report_outline.json")) errors.push("missing report_outline.md or report_outline.json");
  const chapters = artifacts.filter((item) => item.name.startsWith("chapter_")).length;
  if (chapters < 5) errors.push("deep report requires at least 5 chapter_XX_*.md files");

  const evidence = readJson(path.join(root, "evidence.json"), []);
  const report = readText(path.join(root, "report.md"));
  const html = readText(path.join(root, "report.html"));
  const coverage = readJson(path.join(root, "coverage_stats.json"), {});
  errors.push(...coverageErrors(coverage));
  if (Array.isArray(evidence) && report && html) {
    const core = await import(pathToFileURL(path.join(ROOT, "scripts", "core.mjs")).href);
    const validation = core.validateReport(report, html, evidence);
    errors.push(...validation.errors);
    return { ok: errors.length === 0, workspace: root, errors: [...new Set(errors)], warnings: validation.warnings, artifacts, chapters };
  }
  if (!Array.isArray(evidence)) errors.push("evidence.json must be an array");
  return { ok: errors.length === 0, workspace: root, errors: [...new Set(errors)], warnings: [], artifacts, chapters };
}

function resourceMeta() {
  return {
    "openai/widgetDescription": "Bloome investment research workbench with progress, evidence, artifacts, and an unmodified native report preview.",
    "openai/widgetPrefersBorder": false,
    "openai/widgetCSP": {
      connect_domains: ["https://fonts.googleapis.com", "https://fonts.gstatic.com"],
      resource_domains: ["https://fonts.googleapis.com", "https://fonts.gstatic.com"],
      frame_domains: [],
    },
    ui: {
      prefersBorder: false,
      csp: {
        connectDomains: ["https://fonts.googleapis.com", "https://fonts.gstatic.com"],
        resourceDomains: ["https://fonts.googleapis.com", "https://fonts.gstatic.com"],
        frameDomains: [],
      },
    },
  };
}

function resourceText() {
  const html = fs.readFileSync(path.join(ROOT, "assets", "workbench.html"), "utf8");
  const logo = fs.readFileSync(path.join(ROOT, "assets", "wordmark-black.svg")).toString("base64");
  return html.replaceAll("{{BLOOME_WORDMARK}}", `data:image/svg+xml;base64,${logo}`);
}

function resources() {
  return [{
    uri: WIDGET_URI,
    name: "bloome_research_workbench",
    title: "Bloome Investment Research",
    description: "Native research workspace and investment report viewer.",
    mimeType: WIDGET_MIME,
    _meta: resourceMeta(),
  }];
}

async function callTool(name, args = {}) {
  if (name === "research_search") return researchProxy("/search", args);
  if (name === "research_get_chunk") return researchProxy("/chunk", args);
  if (name === "research_get_report_context") return researchProxy("/context", args);
  if (name === "open_research_workspace") return buildSnapshot(args.workspace);
  if (name === "validate_research_workspace") return validateWorkspace(args.workspace);
  throw new Error(`unknown tool: ${name}`);
}

function toolResult(payload, name) {
  const result = {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    structuredContent: payload,
    isError: false,
  };
  if (name === "open_research_workspace") result._meta = widgetMeta();
  return result;
}

function toolError(error) {
  const payload = { ok: false, error: error?.message || String(error) };
  return { content: [{ type: "text", text: JSON.stringify(payload) }], structuredContent: payload, isError: true };
}

function rpcResponse(id, result) { return { jsonrpc: "2.0", id, result }; }
function rpcError(id, code, message) { return { jsonrpc: "2.0", id, error: { code, message } }; }

async function handleRpc(message) {
  if (!message || typeof message !== "object" || Array.isArray(message)) return rpcError(null, -32600, "Invalid Request");
  const { id, method } = message;
  const params = message.params && typeof message.params === "object" ? message.params : {};
  if (typeof method !== "string") return id == null ? null : rpcError(id, -32600, "Invalid Request");
  if (method.startsWith("notifications/") || method === "$/cancelRequest") return null;
  try {
    if (method === "initialize") return rpcResponse(id, {
      protocolVersion: params.protocolVersion || "2024-11-05",
      capabilities: { tools: { listChanged: false }, resources: { subscribe: false, listChanged: false } },
      serverInfo: { name: SERVER_NAME, title: "Bloome Investment Research", version: SERVER_VERSION },
      instructions: "Use Codex as the reasoning runtime. Preserve the investment skill workflow and assets/template.html report contract. Bloome styling applies only to the workbench shell.",
    });
    if (method === "ping") return rpcResponse(id, {});
    if (method === "tools/list") return rpcResponse(id, { tools: toolDefinitions() });
    if (method === "tools/call") {
      try { return rpcResponse(id, toolResult(await callTool(params.name, params.arguments || {}), params.name)); }
      catch (error) { return rpcResponse(id, toolError(error)); }
    }
    if (method === "resources/list") return rpcResponse(id, { resources: resources() });
    if (method === "resources/read") {
      if (params.uri !== WIDGET_URI) return rpcError(id, -32602, `unknown resource: ${params.uri}`);
      return rpcResponse(id, { contents: [{ uri: WIDGET_URI, mimeType: WIDGET_MIME, text: resourceText(), _meta: resourceMeta() }] });
    }
    if (method === "resources/templates/list") return rpcResponse(id, { resourceTemplates: [] });
    if (method === "prompts/list") return rpcResponse(id, { prompts: [] });
    return rpcError(id, -32601, `Method not found: ${method}`);
  } catch (error) {
    return rpcError(id, -32000, error?.message || String(error));
  }
}

function runStdio() {
  const input = readline.createInterface({ input: process.stdin });
  input.on("line", async (line) => {
    if (!line.trim()) return;
    let message;
    try { message = JSON.parse(line); }
    catch (error) { process.stdout.write(`${JSON.stringify(rpcError(null, -32700, `Parse error: ${error.message}`))}\n`); return; }
    const response = await handleRpc(message);
    if (response) process.stdout.write(`${JSON.stringify(response)}\n`);
  });
}

module.exports = {
  WIDGET_URI,
  buildSnapshot,
  callTool,
  handleRpc,
  researchProxy,
  resourceText,
  toolDefinitions,
  validateWorkspace,
};

if (require.main === module) runStdio();
