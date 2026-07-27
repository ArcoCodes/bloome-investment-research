"use strict";

const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const readline = require("node:readline");

const DEFAULT_MCP_URL = "https://api.gildata.com/mcp-servers/aidata-assistant-srv-tool";

function readText(file) {
  try { return fs.readFileSync(file, "utf8"); }
  catch (error) {
    if (error.code === "ENOENT") return "";
    throw error;
  }
}

function gildataToken(env = process.env) {
  if (env.GILDATA_MCP_TOKEN) return env.GILDATA_MCP_TOKEN.trim();
  const file = env.GILDATA_MCP_TOKEN_FILE || path.join(os.homedir(), ".bloome", "gildata-mcp-token");
  return readText(file).trim();
}

function endpointUrl(env = process.env) {
  const token = gildataToken(env);
  if (!token) {
    throw new Error(
      "Gildata credential is required; set GILDATA_MCP_TOKEN or ~/.bloome/gildata-mcp-token",
    );
  }
  const url = new URL(env.GILDATA_MCP_URL || DEFAULT_MCP_URL);
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    throw new Error("GILDATA_MCP_URL must use HTTP or HTTPS");
  }
  url.searchParams.set("token", token);
  return url;
}

function parseResponse(text, contentType = "") {
  if (!text.trim()) return null;
  if (contentType.includes("text/event-stream") || text.startsWith("event:") || text.startsWith("data:")) {
    const events = text
      .split(/\r?\n/)
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trim())
      .filter((line) => line && line !== "[DONE]");
    if (!events.length) return null;
    return JSON.parse(events.at(-1));
  }
  return JSON.parse(text);
}

async function forwardRpc(message, options = {}) {
  const env = options.env || process.env;
  const fetcher = options.fetcher || fetch;
  const endpoint = endpointUrl(env);
  let response;
  try {
    response = await fetcher(endpoint, {
      method: "POST",
      headers: {
        accept: "application/json, text/event-stream",
        "content-type": "application/json",
      },
      body: JSON.stringify(message),
      signal: options.signal,
    });
  } catch (error) {
    if (options.signal?.aborted) throw error;
    throw new Error("Gildata MCP service is unavailable");
  }
  const text = await response.text();
  if (!response.ok) {
    let detail = "request failed";
    try {
      const payload = JSON.parse(text);
      detail = String(payload.message || payload.error?.message || detail);
    } catch {}
    throw new Error(`Gildata MCP ${response.status}: ${detail.slice(0, 300)}`);
  }
  try {
    return parseResponse(text, response.headers?.get?.("content-type") || "");
  } catch {
    throw new Error("Gildata MCP returned an invalid response");
  }
}

function rpcError(id, error) {
  return {
    jsonrpc: "2.0",
    id: id ?? null,
    error: { code: -32603, message: error instanceof Error ? error.message : String(error) },
  };
}

function unconfiguredResponse(message) {
  if (message.method === "initialize") {
    return {
      jsonrpc: "2.0",
      id: message.id,
      result: {
        protocolVersion: message.params?.protocolVersion || "2025-03-26",
        capabilities: { tools: { listChanged: false } },
        serverInfo: { name: "gildata-data-map", version: "1.0.0" },
        instructions:
          "Gildata Data Map is optional. Set GILDATA_MCP_TOKEN or ~/.bloome/gildata-mcp-token, then start a new task to enable its tools.",
      },
    };
  }
  if (message.method === "tools/list") {
    return { jsonrpc: "2.0", id: message.id, result: { tools: [] } };
  }
  return rpcError(
    message.id,
    new Error("Gildata credential is required; set GILDATA_MCP_TOKEN or ~/.bloome/gildata-mcp-token"),
  );
}

function runStdio() {
  const input = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
  let queue = Promise.resolve();
  input.on("line", (line) => {
    if (!line.trim()) return;
    queue = queue.then(async () => {
      let message;
      try {
        message = JSON.parse(line);
      } catch {
        process.stdout.write(`${JSON.stringify(rpcError(null, new Error("Invalid JSON-RPC input")))}\n`);
        return;
      }
      if (!gildataToken()) {
        if (message.id !== undefined) {
          process.stdout.write(`${JSON.stringify(unconfiguredResponse(message))}\n`);
        }
        return;
      }
      try {
        const result = await forwardRpc(message);
        if (message.id !== undefined && result) process.stdout.write(`${JSON.stringify(result)}\n`);
      } catch (error) {
        if (message.id !== undefined) process.stdout.write(`${JSON.stringify(rpcError(message.id, error))}\n`);
        else process.stderr.write(`${error.message}\n`);
      }
    });
  });
  input.on("close", () => {
    queue.catch((error) => process.stderr.write(`${error.message}\n`));
  });
}

module.exports = {
  DEFAULT_MCP_URL,
  endpointUrl,
  forwardRpc,
  gildataToken,
  parseResponse,
  runStdio,
  unconfiguredResponse,
};

if (require.main === module) runStdio();
