"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");

const DEFAULT_FINANCE_URL = "https://fit-tadpole-7001.edgespark.app";
const DEFAULT_CREDENTIAL_FILE = path.join(os.homedir(), ".bloome", "finance-credential.json");
const RUN_FILE = ".bloome-finance-run.json";

function readJson(file, fallback = null) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); }
  catch (error) { if (error.code === "ENOENT" || error instanceof SyntaxError) return fallback; throw error; }
}

function writePrivateJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true, mode: 0o700 });
  const temporary = `${file}.${process.pid}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(temporary, file);
  try { fs.chmodSync(file, 0o600); } catch {}
}

function baseUrl(options = {}) {
  return (options.baseUrl || process.env.BLOOME_FINANCE_URL || DEFAULT_FINANCE_URL).replace(/\/$/, "");
}

function credentialFile(options = {}) {
  return options.credentialFile || process.env.BLOOME_FINANCE_CREDENTIAL_FILE || DEFAULT_CREDENTIAL_FILE;
}

async function responseJson(response, options = {}) {
  const text = await response.text();
  let payload = {};
  try { payload = text ? JSON.parse(text) : {}; }
  catch { throw new Error("Bloome Finance returned invalid JSON"); }
  if (!response.ok) {
    const message = String(payload.error || payload.message || "request failed").slice(0, 300);
    if (response.status === 402) throw new Error(`${message}. Purchase research credits at ${baseUrl(options)}/pricing`);
    throw new Error(`Bloome Finance ${response.status}: ${message}`);
  }
  return payload;
}

function launchBrowser(url) {
  const [command, args] = process.platform === "darwin"
    ? ["open", [url]]
    : process.platform === "win32"
      ? ["cmd", ["/c", "start", "", url]]
      : ["xdg-open", [url]];
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: "ignore" });
    child.once("error", reject);
    child.once("exit", (code) => code === 0 ? resolve() : reject(new Error(`${command} exited with ${code}`)));
  });
}

function abortError() {
  return new DOMException("The operation was aborted", "AbortError");
}

async function wait(milliseconds, signal, sleep = (value) => new Promise((resolve) => setTimeout(resolve, value))) {
  if (signal?.aborted) throw abortError();
  await sleep(milliseconds);
  if (signal?.aborted) throw abortError();
}

async function authorizeDevice(options = {}) {
  const fetcher = options.fetcher || fetch;
  const start = await responseJson(await fetcher(`${baseUrl(options)}/api/public/device/start`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ deviceName: String(options.deviceName || `${os.hostname()} · Bloome Investment Research`).slice(0, 80) }),
    signal: options.signal,
  }), options);
  if (!start.deviceCode || !start.verificationUri) throw new Error("Bloome Finance returned an invalid device authorization");

  const approvalUrl = start.verificationUriComplete || start.verificationUri;
  const verificationUri = new URL(approvalUrl, baseUrl(options)).toString();
  try { await (options.openBrowser || launchBrowser)(verificationUri); }
  catch { throw new Error(`Open ${verificationUri} to authorize Bloome Investment Research`); }

  const deadline = Date.now() + Math.min(Number(start.expiresIn) || 600, 900) * 1000;
  const interval = Math.max(0, Number(start.interval) || 5) * 1000;
  while (Date.now() < deadline) {
    if (interval) await wait(interval, options.signal, options.sleep);
    else if (options.signal?.aborted) throw abortError();
    const response = await fetcher(`${baseUrl(options)}/api/public/device/token`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ deviceCode: start.deviceCode }),
      signal: options.signal,
    });
    if (response.status === 202) continue;
    const payload = await responseJson(response, options);
    if (!payload.accessToken) throw new Error("Bloome Finance did not return an access token");
    const credential = { accessToken: payload.accessToken };
    writePrivateJson(credentialFile(options), credential);
    return credential;
  }
  throw new Error(`Bloome Finance authorization expired. Retry and open ${verificationUri}`);
}

async function authorizedRequest(endpoint, init = {}, options = {}) {
  const file = credentialFile(options);
  const fetcher = options.fetcher || fetch;
  const request = (credential) => fetcher(`${baseUrl(options)}${endpoint}`, {
    ...init,
    headers: { ...(init.headers || {}), authorization: `Bearer ${credential.accessToken}` },
  });
  let credential = readJson(file) || await authorizeDevice({ ...options, signal: init.signal });
  let response = await request(credential);
  if (response.status !== 401) return response;
  try { fs.unlinkSync(file); } catch (error) { if (error.code !== "ENOENT") throw error; }
  credential = await authorizeDevice({ ...options, signal: init.signal });
  response = await request(credential);
  if (response.status === 401) throw new Error("Bloome Finance authorization failed after sign-in");
  return response;
}

function resolvedWorkspace(workspace) {
  if (!workspace || !path.isAbsolute(workspace)) throw new Error("workspace must be an absolute path");
  let root;
  try { root = fs.realpathSync(workspace); }
  catch { throw new Error("workspace must be an existing directory"); }
  if (!fs.statSync(root).isDirectory()) throw new Error("workspace must be an existing directory");
  return root;
}

function workspaceTopic(workspace) {
  for (const name of ["state.json", "plan.json"]) {
    const data = readJson(path.join(workspace, name), {});
    if (String(data?.topic || "").trim()) return String(data.topic).trim().slice(0, 200);
  }
  return path.basename(workspace).slice(0, 200);
}

function workspaceKey(workspace) {
  return crypto.createHash("sha256").update(resolvedWorkspace(workspace)).digest("hex");
}

async function researchRequest(operation, payload, workspace, signal, options = {}) {
  if (!["search", "chunk", "context"].includes(operation)) throw new Error(`unknown research operation: ${operation}`);
  const root = resolvedWorkspace(workspace);
  const request = (endpoint, body) => authorizedRequest(endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  }, options);
  const run = await responseJson(await request("/api/public/mcp/runs/start", {
    workspaceKey: workspaceKey(root),
    topic: workspaceTopic(root),
  }), options);
  const runId = run.runId || run.run?.id;
  const expiresAt = run.expiresAt || run.run?.expiresAt;
  if (!runId) throw new Error("Bloome Finance did not return a research run");
  writePrivateJson(path.join(root, RUN_FILE), { runId, status: "active", expiresAt });
  return responseJson(await request(`/api/public/mcp/research/${operation}`, { runId, payload }), options);
}

async function completeResearchRun(workspace, options = {}) {
  const root = resolvedWorkspace(workspace);
  const file = path.join(root, RUN_FILE);
  const run = readJson(file);
  if (!run?.runId || run.status !== "active") return false;
  const response = await authorizedRequest(`/api/public/mcp/runs/${encodeURIComponent(run.runId)}/complete`, {
    method: "POST",
  }, options);
  if (response.status === 404) {
    writePrivateJson(file, { ...run, status: "stale", closedAt: new Date().toISOString() });
    return false;
  }
  await responseJson(response, options);
  writePrivateJson(file, { ...run, status: "completed", completedAt: new Date().toISOString() });
  return true;
}

module.exports = {
  DEFAULT_FINANCE_URL,
  RUN_FILE,
  authorizeDevice,
  authorizedRequest,
  completeResearchRun,
  researchRequest,
  workspaceKey,
};
