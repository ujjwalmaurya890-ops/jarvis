// Run while `python main.py --api` is running. Uses Node 20's built-in fetch.
// It exercises the exact URLs/headers sent by src/lib/useBackend.ts. No token
// or response body is printed, and the token is never stored in the repo.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const base = "http://127.0.0.1:8765";
const origin = "http://127.0.0.1:5173";
let assertions = 0;

function expect(condition, name) {
  if (!condition) throw new Error(`FAIL: ${name}`);
  assertions++;
  console.log(`PASS: ${name}`);
}

if (process.argv.includes("--expect-offline")) {
  try {
    await fetch(`${base}/api/health`, { signal: AbortSignal.timeout(2500) });
    throw new Error("A local API responded; this test requires it to be stopped first.");
  } catch (error) {
    if (error.message?.startsWith("A local API responded")) throw error;
    expect(error instanceof TypeError || error.name === "TimeoutError", "offline API rejects promptly (UI can show connection error)");
  }
  process.exit(0);
}

const token = readFileSync(join(root, "jarvis", "data", "api-token"), "utf8").trim();
expect(token.length >= 30, "local token exists and is long enough (not printed)");
const health = await fetch(`${base}/api/health`, { headers: { Origin: origin }, signal: AbortSignal.timeout(5000) });
const healthJson = await health.json();
expect(health.status === 200 && healthJson.app === "JARVIS" && healthJson.api === "loopback-only", "health contract /api/health");
expect(health.headers.get("access-control-allow-origin") === origin, "local dashboard CORS origin accepted");
expect(!JSON.stringify(healthJson).includes(token), "health response never contains bearer token");

const preflight = await fetch(`${base}/api/command`, {
  method: "OPTIONS",
  headers: { Origin: origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type" },
});
expect(preflight.status === 204 && /Authorization/i.test(preflight.headers.get("access-control-allow-headers") || ""), "CORS preflight permits bearer auth");

const wrongOrigin = await fetch(`${base}/api/health`, { headers: { Origin: "https://evil.invalid" } });
expect(wrongOrigin.status === 403 && !wrongOrigin.headers.get("access-control-allow-origin"), "foreign browser origin rejected");

const denied = await fetch(`${base}/api/session`, { headers: { Origin: origin, Authorization: "Bearer wrong-token" } });
expect(denied.status === 401, "invalid bearer token rejected");
const session = await fetch(`${base}/api/session`, { headers: { Origin: origin, Authorization: `Bearer ${token}` } });
expect(session.status === 200 && (await session.json()).authorized === true, "local token authenticates dashboard");

const command = await fetch(`${base}/api/command`, {
  method: "POST",
  headers: { Origin: origin, Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
  body: JSON.stringify({ text: "help" }),
  signal: AbortSignal.timeout(6000),
});
const reply = await command.json();
expect(command.status === 200 && typeof reply.reply === "string" && reply.reply.includes("I can:"), "typed command reaches real Python router");
expect(Array.isArray(reply.sources) && typeof reply.source === "string", "reply metadata matches React contract");
console.log(`${assertions} integration checks passed (actual Python API + Node fetch).`);