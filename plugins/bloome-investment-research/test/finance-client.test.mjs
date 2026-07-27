import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const finance = require("../mcp/finance-client.cjs");

function json(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

test("first protected request completes device authorization and stores only the access token locally", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bloome-finance-auth-"));
  const credentialFile = path.join(root, "credential.json");
  const opened = [];
  let polls = 0;
  const fetcher = async (url, options = {}) => {
    const pathname = new URL(url).pathname;
    if (pathname === "/api/public/device/start") {
      return json({
        deviceCode: "device-secret",
        userCode: "ABCD-EFGH",
        verificationUri: "https://finance.example/activate",
        verificationUriComplete: "https://finance.example/activate?code=device-secret",
        expiresIn: 600,
        interval: 0,
      });
    }
    if (pathname === "/api/public/device/token") {
      polls += 1;
      return polls === 1 ? json({ status: "pending" }, 202) : json({ accessToken: "access-secret" });
    }
    assert.equal(options.headers.authorization, "Bearer access-secret");
    return json({ ok: true });
  };

  const response = await finance.authorizedRequest("/api/public/mcp/test", {}, {
    baseUrl: "https://finance.example",
    credentialFile,
    fetcher,
    openBrowser: (url) => opened.push(url),
    sleep: async () => {},
  });

  assert.equal(response.ok, true);
  assert.deepEqual(opened, ["https://finance.example/activate?code=device-secret"]);
  assert.deepEqual(JSON.parse(await readFile(credentialFile, "utf8")), { accessToken: "access-secret" });
});

test("research request starts one workspace run and forwards only the research payload", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bloome-finance-run-"));
  const credentialFile = path.join(root, "credential.json");
  const workspace = path.join(root, "workspace");
  await mkdir(workspace);
  await writeFile(credentialFile, JSON.stringify({ accessToken: "access-secret" }));
  await writeFile(path.join(workspace, "plan.json"), JSON.stringify({ topic: "AI NAND cycle" }));

  const requests = [];
  const fetcher = async (url, options = {}) => {
    const pathname = new URL(url).pathname;
    const body = JSON.parse(options.body);
    requests.push({ pathname, body, authorization: options.headers.authorization });
    if (pathname === "/api/public/mcp/runs/start") return json({ run: { id: "run-1", expiresAt: "2026-08-01T00:00:00.000Z" }, charged: true });
    if (pathname === "/api/public/mcp/research/search") return json({ reports: [{ report_id: "r1" }] });
    return json({ error: "not found" }, 404);
  };

  const result = await finance.researchRequest("search", { corpus: "sell", size: 2 }, workspace, undefined, {
    baseUrl: "https://finance.example",
    credentialFile,
    fetcher,
  });

  assert.equal(result.reports[0].report_id, "r1");
  assert.equal(requests[0].body.topic, "AI NAND cycle");
  assert.match(requests[0].body.workspaceKey, /^[a-f0-9]{64}$/);
  assert.deepEqual(requests[1].body, { runId: "run-1", payload: { corpus: "sell", size: 2 } });
  assert.ok(requests.every((request) => request.authorization === "Bearer access-secret"));
  assert.equal(JSON.parse(await readFile(path.join(workspace, ".bloome-finance-run.json"), "utf8")).runId, "run-1");
});

test("workspace billing keys canonicalize aliases and reject missing directories", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bloome-finance-workspace-key-"));
  const workspace = path.join(root, "workspace");
  const alias = path.join(root, "alias");
  await mkdir(workspace);
  await symlink(workspace, alias);
  assert.equal(finance.workspaceKey(workspace), finance.workspaceKey(alias));
  assert.throws(() => finance.workspaceKey(path.join(root, "missing")), /existing directory/);
});

test("completing a workspace closes its saved run once", async () => {
  const root = await mkdtemp(path.join(os.tmpdir(), "bloome-finance-complete-"));
  const credentialFile = path.join(root, "credential.json");
  await writeFile(credentialFile, JSON.stringify({ accessToken: "access-secret" }));
  await writeFile(path.join(root, ".bloome-finance-run.json"), JSON.stringify({ runId: "run-1", status: "active" }));
  let completions = 0;
  const fetcher = async (url) => {
    assert.equal(new URL(url).pathname, "/api/public/mcp/runs/run-1/complete");
    completions += 1;
    return json({ ok: true });
  };

  assert.equal(await finance.completeResearchRun(root, { baseUrl: "https://finance.example", credentialFile, fetcher }), true);
  assert.equal(await finance.completeResearchRun(root, { baseUrl: "https://finance.example", credentialFile, fetcher }), false);
  assert.equal(completions, 1);
  assert.equal(JSON.parse(await readFile(path.join(root, ".bloome-finance-run.json"), "utf8")).status, "completed");
});
