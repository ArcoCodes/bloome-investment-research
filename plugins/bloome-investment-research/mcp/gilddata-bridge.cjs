"use strict";

// Stdio bridge to the GildData (恒生聚源) hosted MCP service.
// The remote endpoint speaks MCP over streamable HTTP and authenticates with a
// beta token passed as a query parameter. The token never lives in this repo:
// it is read per request from GILDDATA_API_TOKEN or ~/.bloome/gilddata-api-token.

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");

const DEFAULT_ENDPOINT = "https://api.gildata.com/mcp-servers/aidata-assistant-srv-tool";

function readText(file, fallback = "") {
  try { return fs.readFileSync(file, "utf8"); }
  catch (error) { if (error.code === "ENOENT") return fallback; throw error; }
}

function gilddataToken(env = process.env) {
  if (env.GILDDATA_API_TOKEN) return env.GILDDATA_API_TOKEN;
  const file = env.GILDDATA_API_TOKEN_FILE || path.join(os.homedir(), ".bloome", "gilddata-api-token");
  return readText(file).trim();
}

function endpointUrl(env = process.env) {
  const base = (env.GILDDATA_MCP_URL || DEFAULT_ENDPOINT).replace(/\/$/, "");
  if (/[?&]token=/.test(base)) return base;
  const token = gilddataToken(env);
  if (!token) throw new Error("GildData credential is required; set GILDDATA_API_TOKEN or ~/.bloome/gilddata-api-token");
  return `${base}${base.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}

function lastSseJson(text) {
  const events = text.split(/\n/).filter((line) => line.startsWith("data:"));
  for (let i = events.length - 1; i >= 0; i -= 1) {
    try { return JSON.parse(events[i].slice(5).trim()); } catch {}
  }
  throw new Error("GildData MCP returned an unreadable event stream");
}

const session = { id: "" };

async function forward(message, fetcher = fetch, env = process.env) {
  const headers = {
    "content-type": "application/json",
    accept: "application/json, text/event-stream",
  };
  if (session.id) headers["mcp-session-id"] = session.id;
  let response;
  try {
    response = await fetcher(endpointUrl(env), {
      method: "POST",
      headers,
      body: JSON.stringify(message),
    });
  } catch (error) {
    throw new Error(`GildData MCP unreachable: ${error.message}`);
  }
  const sessionId = response.headers?.get?.("mcp-session-id");
  if (sessionId) session.id = sessionId;
  const text = await response.text();
  if (!response.ok) throw new Error(`GildData MCP ${response.status}: ${text.slice(0, 300)}`);
  if (message.id == null || !text.trim()) return null;
  const contentType = response.headers?.get?.("content-type") || "";
  if (contentType.includes("text/event-stream")) return lastSseJson(text);
  try { return JSON.parse(text); }
  catch { throw new Error("GildData MCP returned invalid JSON"); }
}

function rpcError(id, code, message) {
  return { jsonrpc: "2.0", id, error: { code, message } };
}

function runStdio() {
  const input = readline.createInterface({ input: process.stdin });
  input.on("line", async (line) => {
    if (!line.trim()) return;
    let message;
    try { message = JSON.parse(line); }
    catch (error) { process.stdout.write(`${JSON.stringify(rpcError(null, -32700, `Parse error: ${error.message}`))}\n`); return; }
    let response;
    try { response = await forward(message); }
    catch (error) { response = message.id == null ? null : rpcError(message.id, -32000, error?.message || String(error)); }
    if (response) process.stdout.write(`${JSON.stringify(response)}\n`);
  });
}

module.exports = { DEFAULT_ENDPOINT, endpointUrl, forward, gilddataToken, runStdio };

if (require.main === module) runStdio();
