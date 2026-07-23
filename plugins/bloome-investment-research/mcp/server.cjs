"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");
const { pathToFileURL } = require("node:url");

const ROOT = path.resolve(__dirname, "..");
const PLUGIN_CONFIG = JSON.parse(fs.readFileSync(path.join(ROOT, "plugin.config.json"), "utf8"));
const SERVER_NAME = "bloome-investment-research";
const SERVER_VERSION = PLUGIN_CONFIG.version;
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
    "openai/toolInvocation/invoking": "Opening research panel",
    "openai/toolInvocation/invoked": "Research panel ready",
  };
}

function runtimeName(env = process.env) {
  if (env.BLOOME_RUNTIME === "codex" || env.BLOOME_RUNTIME === "claude-code") return env.BLOOME_RUNTIME;
  return env.CLAUDE_PLUGIN_ROOT ? "claude-code" : "codex";
}

function runtimeProfile(runtime = runtimeName()) {
  if (runtime === "claude-code") {
    return {
      name: runtime,
      supportsWorkbench: false,
      instructions: "Use Claude Code as the reasoning runtime. Preserve the investment research workflow and assets/template.html report contract. Use the returned reportPath to inspect the finished HTML report.",
    };
  }
  return {
    name: "codex",
    supportsWorkbench: true,
    instructions: "Use Codex as the reasoning runtime. Preserve the investment research workflow and assets/template.html report contract. Bloome styling applies only to the workbench shell.",
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

function toolDefinitions(runtime = runtimeName()) {
  const profile = runtimeProfile(runtime);
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
      profile.supportsWorkbench ? "Open Bloome Research" : "Inspect Bloome Research workspace",
      profile.supportsWorkbench
        ? "Open the Bloome research workbench for an absolute project workspace path. The UI promotes from a compact conversation launcher into a native PiP panel and expands to fullscreen for the report while preserving the investment skill's native report HTML."
        : "Inspect an absolute Bloome research workspace path and return progress, evidence, artifacts, and report paths. Read report.html from reportPath to view the finished report in Claude Code.",
      objectSchema({ workspace: { type: "string", minLength: 1 } }, ["workspace"]),
      { widget: profile.supportsWorkbench },
    ),
    tool(
      "validate_research_workspace",
      "Validate investment report",
      "Validate staged files, binding outline order, planned visuals, evidence traceability, chapter depth, and the native report template contract.",
      objectSchema({ workspace: { type: "string", minLength: 1 } }, ["workspace"]),
    ),
  ];
}

function researchApiToken(env = process.env) {
  if (env.RESEARCH_API_TOKEN) return env.RESEARCH_API_TOKEN;
  const file = env.RESEARCH_API_TOKEN_FILE || path.join(os.homedir(), ".bloome", "research-api-token");
  return readText(file).trim();
}

async function researchProxy(endpoint, body, signal, fetcher = fetch) {
  const base = (process.env.RESEARCH_SEARCH_URL || DEFAULT_SEARCH_URL).replace(/\/$/, "");
  const token = researchApiToken();
  if (!token) throw new Error("Bloome research credential is required; set RESEARCH_API_TOKEN or ~/.bloome/research-api-token");
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

function buildSnapshot(workspace, options = {}) {
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
  const snapshot = {
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
    reportPath: path.join(root, "report.html"),
  };
  if (options.includeReportHtml !== false) snapshot.reportHtml = reportHtml;
  return snapshot;
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

function citations(markdown) {
  return [...markdown.matchAll(/(?:\[([^\]\n]+)\]|〔([^〕\n]+)〕|【([^】\n]+)】)/g)]
    .map((match) => match[1] ?? match[2] ?? match[3])
    .filter((value) => /,\s*(?:p{1,2}\.\d+(?:-\d+)?|lines? \d+(?:-\d+)?)/.test(value));
}

function bodyParagraphs(markdown) {
  return markdown
    .replace(/^#{1,6}\s+.*$/gm, "")
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.replace(/\s+/g, " ").trim())
    .filter(Boolean);
}

function moduleErrors(id, markdown) {
  const errors = [];
  const bodyLength = bodyParagraphs(markdown).join("").length;
  if (bodyLength < 120) errors.push(`module ${id} is title-only or lacks substantive evidence`);
  for (const heading of ["Direct answer", "Claim–evidence pairs", "Metrics", "Conflicts and date reconciliation", "Invalidating conditions", "Remaining gaps"]) {
    if (!new RegExp(`^#{1,6}\\s+${heading}\\s*$`, "im").test(markdown)) errors.push(`module ${id} missing section: ${heading}`);
  }
  if (!citations(markdown).length) errors.push(`module ${id} requires at least one exact source citation`);
  return errors;
}

function chapterErrors(name, markdown) {
  const errors = [];
  if (bodyParagraphs(markdown).join("").length < 160) errors.push(`${name} is title-only or lacks substantive analysis`);
  if (!citations(markdown).length) errors.push(`${name} requires at least one exact source citation`);
  if (!/^#{1,6}\s+.*(?:边界|反方|挑战|限制|风险|失效|证伪|不确定|争议|opposing|challenge|boundary|limitation|risk|invalidat|falsif)/im.test(markdown)) {
    errors.push(`${name} requires an explicit boundary, opposing-evidence, risk, or invalidation section`);
  }
  return errors;
}

function finalAssemblyErrors(finalReport, chapters) {
  const errors = [];
  const normalizedFinal = finalReport.replace(/\s+/g, " ").trim();
  const finalCitations = new Set(citations(finalReport));
  for (const { name, markdown } of chapters) {
    if (bodyParagraphs(markdown).some((paragraph) => !normalizedFinal.includes(paragraph))) {
      errors.push(`final_report.md drops body text from ${name}`);
    }
    const missing = citations(markdown).filter((citation) => !finalCitations.has(citation));
    if (missing.length) errors.push(`final_report.md drops citations from ${name}: ${[...new Set(missing)].join(", ")}`);
  }
  return errors;
}

function reportStructureErrors(outline, chapters, html, evidence) {
  const errors = [];
  const sections = [...outline.matchAll(/^#\s+(S\d{2})\s+(.+)$/gmi)].map((match) => ({ id: match[1].toUpperCase(), title: match[2].trim() }));
  if (!sections.length) return ["report_outline.md must define binding # SXX Title sections"];
  if (sections.length !== chapters.length) errors.push("report outline section count must match chapter files");
  for (const [index, section] of sections.entries()) {
    const heading = chapters[index]?.markdown.match(/^#\s+(S\d{2})\s+(.+)$/mi);
    if (!heading || heading[1].toUpperCase() !== section.id || heading[2].trim() !== section.title) {
      errors.push(`${chapters[index]?.name || `chapter ${index + 1}`} must start with # ${section.id} ${section.title}`);
    }
  }
  const renderedSections = [...html.matchAll(/\bdata-section-id\s*=\s*["'](S\d{2})["']/gi)].map((match) => match[1].toUpperCase());
  if (renderedSections.join("|") !== sections.map(({ id }) => id).join("|")) errors.push("HTML report sections must match outline IDs and order exactly");

  const visuals = [...outline.matchAll(/^-\s*Visual:\s*(V\d{2})\s*\|\s*(comparison|mechanism|decision)\s*\|\s*([^|]+?)\s*\|\s*(chart|diagram|table)\s*$/gmi)]
    .map((match) => ({ id: match[1].toUpperCase(), role: match[2].toLowerCase(), title: match[3].trim(), form: match[4].toLowerCase() }));
  if (!visuals.length) errors.push("report outline must include an evidence-backed visual plan");
  for (const role of ["comparison", "mechanism", "decision"]) if (!visuals.some((visual) => visual.role === role)) errors.push(`visual plan is missing ${role}`);
  for (const form of ["chart", "diagram"]) if (!visuals.some((visual) => visual.form === form)) errors.push(`visual plan is missing a ${form}`);
  if (new Set(visuals.map(({ id }) => id)).size !== visuals.length) errors.push("visual IDs must be unique");

  const figures = [...html.matchAll(/<figure\b([^>]*)>([\s\S]*?)<\/figure>/gi)].map((match) => ({ attributes: match[1], body: match[2] }));
  const evidenceIds = new Set(Array.isArray(evidence) ? evidence.map((item) => String(item.chunk_id)) : []);
  const attribute = (attributes, name) => attributes.match(new RegExp(`\\b${name}\\s*=\\s*["']([^"']*)["']`, "i"))?.[1] || "";
  for (const visual of visuals) {
    const matches = figures.filter((figure) => attribute(figure.attributes, "data-visual-id").toUpperCase() === visual.id);
    if (matches.length !== 1) { errors.push(`HTML must render visual ${visual.id} exactly once`); continue; }
    const figure = matches[0];
    if (attribute(figure.attributes, "data-visual-role").toLowerCase() !== visual.role) errors.push(`visual ${visual.id} role must be ${visual.role}`);
    if (attribute(figure.attributes, "data-visual-title") !== visual.title) errors.push(`visual ${visual.id} title must match the outline`);
    if (!attribute(figure.attributes, "aria-label")) errors.push(`visual ${visual.id} requires an accessible description`);
    const sources = attribute(figure.attributes, "data-visual-source").split(/\s+/).filter(Boolean);
    if (!sources.length || sources.some((id) => !evidenceIds.has(id))) errors.push(`visual ${visual.id} must cite exact evidence chunk IDs`);
    if ((visual.form === "chart" || visual.form === "diagram") && !/<svg\b/i.test(figure.body)) errors.push(`visual ${visual.id} must render static inline SVG`);
    if (visual.form === "table" && !/<table\b/i.test(figure.body)) errors.push(`visual ${visual.id} must render an HTML table`);
    if (!/class\s*=\s*["'][^"']*\bchart-source\b/i.test(figure.body)) errors.push(`visual ${visual.id} requires a visible source line`);
  }
  return errors;
}

async function validateWorkspace(workspace) {
  const root = workspacePath(workspace);
  const core = await import(pathToFileURL(path.join(ROOT, "scripts", "core.mjs")).href);
  const artifacts = artifactList(root);
  const names = new Set(artifacts.map((item) => item.name));
  const errors = REQUIRED_FILES.filter((name) => !names.has(name)).map((name) => `missing ${name}`);
  if (!names.has("report_outline.md")) errors.push("missing binding report_outline.md");
  const chapterArtifacts = artifacts.filter((item) => item.name.startsWith("chapter_"));
  const chapters = chapterArtifacts.length;
  if (chapters < 5) errors.push("deep report requires at least 5 chapter_XX_*.md files");

  const plan = readJson(path.join(root, "plan.json"), {});
  const modules = Array.isArray(plan.modules) ? plan.modules : [];
  if (!names.has("plan.json")) errors.push("missing plan.json");
  if (modules.length < 3 || modules.length > 5) errors.push("deep report requires 3-5 research modules");
  try { core.assertPlan(modules); } catch (error) { errors.push(error.message); }
  for (const module of modules) {
    const id = String(module.id || "");
    if (!/^[A-Za-z0-9_-]{1,40}$/.test(id)) { errors.push(`invalid module id: ${id || "missing"}`); continue; }
    const memo = readText(path.join(root, "modules", `${id}.md`));
    if (!memo) errors.push(`missing modules/${id}.md`);
    else errors.push(...moduleErrors(id, memo));
  }

  const chapterFiles = chapterArtifacts.map(({ name }) => ({ name, markdown: readText(path.join(root, name)) }));
  for (const chapter of chapterFiles) errors.push(...chapterErrors(chapter.name, chapter.markdown));
  const finalReport = readText(path.join(root, "final_report.md"));
  errors.push(...finalAssemblyErrors(finalReport, chapterFiles));

  const evidence = readJson(path.join(root, "evidence.json"), []);
  const report = readText(path.join(root, "report.md"));
  if (report.replace(/\s+/g, " ").trim() !== finalReport.replace(/\s+/g, " ").trim()) {
    errors.push("report.md must preserve final_report.md narrative and citations without compression");
  }
  const html = readText(path.join(root, "report.html"));
  errors.push(...reportStructureErrors(readText(path.join(root, "report_outline.md")), chapterFiles, html, evidence));
  const sellSideLogic = readText(path.join(root, "sell_side_logic.md"));
  const validationMarkdown = readText(path.join(root, "validation.md"));
  const coverage = readJson(path.join(root, "coverage_stats.json"), {});
  errors.push(...coverageErrors(coverage));
  if (Array.isArray(evidence) && report && html) {
    const validation = core.validateReport(report, html, evidence, { sellSideLogic, validation: validationMarkdown });
    errors.push(...validation.errors);
    return { ok: errors.length === 0, workspace: root, errors: [...new Set(errors)], warnings: validation.warnings, artifacts, chapters };
  }
  if (!Array.isArray(evidence)) errors.push("evidence.json must be an array");
  return { ok: errors.length === 0, workspace: root, errors: [...new Set(errors)], warnings: [], artifacts, chapters };
}

function resourceMeta() {
  return {
    "openai/widgetDescription": "Bloome investment research panel with progress, evidence, artifacts, and an unmodified native report preview. The inline surface is only a compact launcher.",
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

async function callTool(name, args = {}, runtime = runtimeName()) {
  if (name === "research_search") return researchProxy("/search", args);
  if (name === "research_get_chunk") return researchProxy("/chunk", args);
  if (name === "research_get_report_context") return researchProxy("/context", args);
  if (name === "open_research_workspace") {
    const profile = runtimeProfile(runtime);
    return {
      ...buildSnapshot(args.workspace, { includeReportHtml: profile.supportsWorkbench }),
      runtime: profile.name,
      workbenchAvailable: profile.supportsWorkbench,
    };
  }
  if (name === "validate_research_workspace") return validateWorkspace(args.workspace);
  throw new Error(`unknown tool: ${name}`);
}

function toolResult(payload, name, runtime = runtimeName()) {
  const result = {
    content: [{ type: "text", text: JSON.stringify(payload) }],
    structuredContent: payload,
    isError: false,
  };
  if (name === "open_research_workspace" && runtimeProfile(runtime).supportsWorkbench) result._meta = widgetMeta();
  return result;
}

function toolError(error) {
  const payload = { ok: false, error: error?.message || String(error) };
  return { content: [{ type: "text", text: JSON.stringify(payload) }], structuredContent: payload, isError: true };
}

function rpcResponse(id, result) { return { jsonrpc: "2.0", id, result }; }
function rpcError(id, code, message) { return { jsonrpc: "2.0", id, error: { code, message } }; }

async function handleRpc(message, runtime = runtimeName()) {
  if (!message || typeof message !== "object" || Array.isArray(message)) return rpcError(null, -32600, "Invalid Request");
  const { id, method } = message;
  const params = message.params && typeof message.params === "object" ? message.params : {};
  if (typeof method !== "string") return id == null ? null : rpcError(id, -32600, "Invalid Request");
  if (method.startsWith("notifications/") || method === "$/cancelRequest") return null;
  const profile = runtimeProfile(runtime);
  try {
    if (method === "initialize") return rpcResponse(id, {
      protocolVersion: params.protocolVersion || "2024-11-05",
      capabilities: profile.supportsWorkbench
        ? { tools: { listChanged: false }, resources: { subscribe: false, listChanged: false } }
        : { tools: { listChanged: false } },
      serverInfo: { name: SERVER_NAME, title: "Bloome Investment Research", version: SERVER_VERSION },
      instructions: profile.instructions,
    });
    if (method === "ping") return rpcResponse(id, {});
    if (method === "tools/list") return rpcResponse(id, { tools: toolDefinitions(runtime) });
    if (method === "tools/call") {
      try { return rpcResponse(id, toolResult(await callTool(params.name, params.arguments || {}, runtime), params.name, runtime)); }
      catch (error) { return rpcResponse(id, toolError(error)); }
    }
    if (method === "resources/list") return rpcResponse(id, { resources: profile.supportsWorkbench ? resources() : [] });
    if (method === "resources/read") {
      if (!profile.supportsWorkbench) return rpcError(id, -32601, "Resources are not available in this runtime");
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
  runStdio,
  runtimeName,
  runtimeProfile,
  toolDefinitions,
  validateWorkspace,
};

if (require.main === module) runStdio();
