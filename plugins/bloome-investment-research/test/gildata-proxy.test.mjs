import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { test } from "node:test";

const require = createRequire(import.meta.url);
const proxy = require("../mcp/gildata-proxy.cjs");

test("Gildata proxy adds the environment token only at request time", async () => {
  let request;
  const response = await proxy.forwardRpc(
    { jsonrpc: "2.0", id: 1, method: "tools/list", params: {} },
    {
      env: { GILDATA_MCP_TOKEN: "test-token" },
      fetcher: async (url, options) => {
        request = { url, options };
        return {
          ok: true,
          headers: { get: () => "application/json" },
          text: async () => JSON.stringify({ jsonrpc: "2.0", id: 1, result: { tools: [] } }),
        };
      },
    },
  );
  assert.equal(request.url.origin, "https://api.gildata.com");
  assert.equal(request.url.searchParams.get("token"), "test-token");
  assert.equal(request.options.headers.authorization, undefined);
  assert.deepEqual(JSON.parse(request.options.body), {
    jsonrpc: "2.0",
    id: 1,
    method: "tools/list",
    params: {},
  });
  assert.deepEqual(response.result.tools, []);
});

test("Gildata proxy accepts streamable HTTP event responses", () => {
  const response = proxy.parseResponse(
    'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"FinQuery"}]}}\n\n',
    "text/event-stream",
  );
  assert.equal(response.result.tools[0].name, "FinQuery");
});

test("Gildata proxy fails clearly when its credential is missing", async () => {
  await assert.rejects(
    () => proxy.forwardRpc(
      { jsonrpc: "2.0", id: 1, method: "initialize", params: {} },
      { env: {}, fetcher: async () => assert.fail("fetch must not run") },
    ),
    /set GILDATA_MCP_TOKEN or ~\/\.bloome\/gildata-mcp-token/,
  );
});

test("optional Gildata server initializes cleanly before a credential is configured", () => {
  const initialized = proxy.unconfiguredResponse({
    jsonrpc: "2.0",
    id: 1,
    method: "initialize",
    params: { protocolVersion: "2025-03-26" },
  });
  const listed = proxy.unconfiguredResponse({
    jsonrpc: "2.0",
    id: 2,
    method: "tools/list",
    params: {},
  });
  assert.equal(initialized.result.serverInfo.name, "gildata-data-map");
  assert.match(initialized.result.instructions, /GILDATA_MCP_TOKEN/);
  assert.deepEqual(listed.result.tools, []);
});
